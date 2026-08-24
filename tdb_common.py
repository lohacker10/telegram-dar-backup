#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import getpass
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from dotenv import dotenv_values
except ImportError:  # useful error later, but lets --help/static checks work
    dotenv_values = None

MANIFEST_TAG = "TDB_MANIFEST_V1"
SLICE_TAG = "TDB_SLICE_V1"
CATALOG_TAG = "TDB_CATALOG_V1"
FORMAT_VERSION = "telegram-dar-backup/v1"
_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def load_config(path: str | Path) -> dict[str, str]:
    path = Path(path)
    cfg: dict[str, str] = {}
    if path.exists():
        if dotenv_values is None:
            raise RuntimeError("Manca python-dotenv. Esegui: pip install -r requirements.txt")
        for k, v in dotenv_values(path).items():
            if v is not None:
                cfg[k] = str(v)
    for k, v in os.environ.items():
        if k.startswith(("TELEGRAM_", "DAR_", "TDB_")) or k in {
            "SOURCE", "SOURCE_LABEL", "WORK_DIR", "SLICE_SIZE", "COMPRESSION",
            "FULL_EVERY_MONTHS", "REQUIRE_SOURCE_MOUNT", "KEEP_ONLY_LATEST_DIFF"
        }:
            cfg[k] = v
    return cfg


def cfg_required(cfg: dict[str, str], key: str) -> str:
    value = cfg.get(key, "").strip()
    if not value:
        raise RuntimeError(f"Configurazione mancante: {key}")
    return value


def cfg_bool(cfg: dict[str, str], key: str, default: bool = False) -> bool:
    v = cfg.get(key)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_namespace(cfg: dict[str, str]) -> str:
    """Return the logical Telegram backup namespace.

    Older manifests did not carry a namespace. They are intentionally treated as
    belonging to ``default`` so existing installations remain compatible.
    """
    value = cfg.get("TDB_NAMESPACE", "default").strip() or "default"
    if not _NAMESPACE_RE.fullmatch(value):
        raise RuntimeError(
            "TDB_NAMESPACE non valido: usa 1..64 caratteri tra lettere, numeri, '.', '_' e '-'"
        )
    return value


def get_work_dir(cfg: dict[str, str]) -> Path:
    return Path(cfg.get("WORK_DIR", "/var/lib/telegram-dar-backup")).expanduser().resolve()


def get_session_file(cfg: dict[str, str]) -> Path:
    raw = cfg.get("TELEGRAM_SESSION_FILE")
    if raw:
        return Path(raw).expanduser().resolve()
    return get_work_dir(cfg) / "telegram.session"


def read_passphrase(cfg: dict[str, str], *, interactive: bool) -> str:
    pass_file = cfg.get("DAR_PASSPHRASE_FILE", "").strip()
    if pass_file:
        p = Path(pass_file)
        if not p.exists():
            raise RuntimeError(f"DAR_PASSPHRASE_FILE non esiste: {p}")
        secret = p.read_text(encoding="utf-8").rstrip("\r\n")
        if not secret:
            raise RuntimeError(f"DAR_PASSPHRASE_FILE è vuoto: {p}")
        return secret
    secret = cfg.get("DAR_PASSPHRASE", "")
    if secret:
        return secret
    if interactive:
        secret = getpass.getpass("Password DAR: ")
        if not secret:
            raise RuntimeError("Password DAR vuota")
        return secret
    raise RuntimeError(
        "Password DAR non configurata. Imposta DAR_PASSPHRASE_FILE (consigliato) "
        "oppure DAR_PASSPHRASE nel config.env"
    )


def dcf_quote(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise RuntimeError("La password DAR non può contenere newline")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_secret_dcf(path: Path, passphrase: str, *, current: bool = True, reference: bool = False) -> None:
    lines = []
    if current:
        lines.append(f"-K {dcf_quote('aes:' + passphrase)}")
    if reference:
        lines.append(f"-J {dcf_quote('aes:' + passphrase)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def sha512_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha512()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: datetime | None = None) -> str:
    dt = dt or utc_now()
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def month_distance(older: datetime, newer: datetime) -> int:
    older = older.astimezone(timezone.utc)
    newer = newer.astimezone(timezone.utc)
    months = (newer.year - older.year) * 12 + newer.month - older.month
    if newer.day < older.day:
        months -= 1
    return max(0, months)


def new_backup_id(kind: str) -> str:
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{kind.upper()}_{os.urandom(3).hex()}"


def run_checked(args: list[str], *, cwd: Path | None = None) -> None:
    printable = " ".join(shlex.quote(a) for a in args)
    print(f"+ {printable}", flush=True)
    proc = subprocess.run(args, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise RuntimeError(f"Comando fallito ({proc.returncode}): {args[0]}")


def dar_version() -> str:
    try:
        p = subprocess.run(["dar", "-V"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        return (p.stdout or "").strip().splitlines()[0][:200]
    except Exception:
        return "unknown"


def parse_caption(caption: str | None, tag: str) -> dict[str, str] | None:
    if not caption:
        return None
    first = caption.splitlines()[0].strip()
    if not first.startswith(tag):
        return None
    rest = first[len(tag):].strip()
    fields: dict[str, str] = {}
    for token in shlex.split(rest):
        if "=" in token:
            k, v = token.split("=", 1)
            fields[k] = v
    return fields


@dataclass
class RemoteManifestSummary:
    message_id: int
    backup_id: str
    kind: str
    base_full_id: str
    created_utc: str
    namespace: str = "default"


async def make_client(cfg: dict[str, str]):
    from telethon import TelegramClient

    api_id = int(cfg_required(cfg, "TELEGRAM_API_ID"))
    api_hash = cfg_required(cfg, "TELEGRAM_API_HASH")
    session = get_session_file(cfg)
    session.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("Sessione Telegram non autenticata. Esegui prima telegram_login.py")
    return client


async def resolve_channel(client, cfg: dict[str, str]):
    from telethon import utils

    raw = cfg_required(cfg, "TELEGRAM_CHANNEL_ID").strip()
    try:
        target_id = int(raw)
    except ValueError:
        return await client.get_entity(raw)

    try:
        return await client.get_entity(target_id)
    except Exception:
        async for dialog in client.iter_dialogs():
            try:
                if utils.get_peer_id(dialog.entity) == target_id:
                    return dialog.entity
            except Exception:
                continue
        raise RuntimeError(
            f"Canale {target_id} non risolto dalla sessione Telegram. "
            "Esegui telegram_login.py --list-channels e verifica TELEGRAM_CHANNEL_ID."
        )


async def discover_manifest_summaries(
    client,
    channel,
    limit: int = 100,
    namespace: str | None = "default",
) -> list[RemoteManifestSummary]:
    """Discover COMPLETE manifests, optionally restricted to one namespace.

    Captions created before namespace support are treated as ``default``.
    ``namespace=None`` is reserved for tools that intentionally need to see all
    namespaces; normal backup/restore callers should always pass their namespace.
    """
    found: list[RemoteManifestSummary] = []
    seen: set[int] = set()

    async def consume(iterator) -> None:
        async for msg in iterator:
            if int(msg.id) in seen:
                continue
            fields = parse_caption(getattr(msg, "message", None), MANIFEST_TAG)
            if not fields:
                continue
            msg_namespace = fields.get("ns", "default")
            if namespace is not None and msg_namespace != namespace:
                continue
            try:
                found.append(RemoteManifestSummary(
                    message_id=int(msg.id),
                    backup_id=fields["backup"],
                    kind=fields["kind"].upper(),
                    base_full_id=fields.get("base", fields["backup"]),
                    created_utc=fields["created"],
                    namespace=msg_namespace,
                ))
                seen.add(int(msg.id))
            except KeyError:
                continue

    # Telegram's server-side search is useful for older manifests but indexing
    # is not guaranteed to be immediate. Always merge it with a direct scan of
    # recent channel messages so a manifest uploaded moments ago is visible to
    # backup rotation, --list, restore and the self-test without arbitrary sleeps.
    try:
        await consume(client.iter_messages(channel, search=MANIFEST_TAG, limit=limit))
    except Exception:
        pass
    await consume(client.iter_messages(channel, limit=10000))

    found.sort(key=lambda x: parse_iso(x.created_utc), reverse=True)
    return found


async def download_manifest(client, channel, summary: RemoteManifestSummary, dest_dir: Path) -> dict[str, Any]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    msg = await client.get_messages(channel, ids=summary.message_id)
    if not msg or not getattr(msg, "file", None):
        raise RuntimeError(f"Manifest Telegram non disponibile: message_id={summary.message_id}")
    target = dest_dir / f"manifest-{summary.backup_id}.json"
    result = await client.download_media(msg, file=str(target))
    if not result:
        raise RuntimeError(f"Download manifest fallito: {summary.backup_id}")
    data = json.loads(target.read_text(encoding="utf-8"))
    data_namespace = str(data.get("namespace", "default"))
    if (
        data.get("format") != FORMAT_VERSION
        or data.get("backup_id") != summary.backup_id
        or data_namespace != summary.namespace
    ):
        raise RuntimeError(f"Manifest non valido o non corrispondente: {summary.backup_id}")
    return data


async def upload_document(client, channel, path: Path, caption: str):
    last_pct = -10

    def progress(done: int, total: int) -> None:
        nonlocal last_pct
        if total:
            pct = done * 100 // total
            if pct >= 100 or pct >= last_pct + 10:
                last_pct = pct
                print(f"Upload {path.name}: {pct:3d}%", flush=True)

    msg = await client.send_file(
        channel,
        file=str(path),
        caption=caption,
        force_document=True,
        progress_callback=progress,
    )
    print(flush=True)
    remote_size = getattr(getattr(msg, "file", None), "size", None)
    if remote_size is not None and int(remote_size) != path.stat().st_size:
        raise RuntimeError(
            f"Dimensione remota diversa per {path.name}: locale={path.stat().st_size}, remoto={remote_size}"
        )
    return msg


async def delete_message_ids(client, channel, ids: Iterable[int]) -> None:
    ids = [int(i) for i in ids if i]
    for pos in range(0, len(ids), 100):
        await client.delete_messages(channel, ids[pos:pos+100], revoke=True)


async def delete_backup_from_manifest(client, channel, summary: RemoteManifestSummary, temp_dir: Path) -> None:
    manifest = await download_manifest(client, channel, summary, temp_dir)
    ids: list[int] = []
    for item in manifest.get("slices", []):
        if item.get("message_id"):
            ids.append(int(item["message_id"]))
    cat = manifest.get("catalog") or {}
    if cat.get("message_id"):
        ids.append(int(cat["message_id"]))
    ids.append(summary.message_id)
    await delete_message_ids(client, channel, ids)


def ensure_empty_or_create(path: Path, allow_nonempty: bool) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not allow_nonempty and any(path.iterdir()):
        raise RuntimeError(
            f"La destinazione non è vuota: {path}. Per sicurezza il restore richiede una directory vuota "
            "(oppure usa --allow-nonempty sapendo cosa stai facendo)."
        )
