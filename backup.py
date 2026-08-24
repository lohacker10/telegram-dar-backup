#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import shutil
import shlex
import sys
from pathlib import Path

from tdb_common import (
    CATALOG_TAG,
    FORMAT_VERSION,
    MANIFEST_TAG,
    cfg_bool,
    cfg_required,
    dar_version,
    delete_backup_from_manifest,
    delete_message_ids,
    discover_manifest_summaries,
    download_manifest,
    get_namespace,
    get_work_dir,
    load_config,
    make_client,
    month_distance,
    new_backup_id,
    parse_iso,
    read_jsonl,
    read_passphrase,
    resolve_channel,
    run_checked,
    sha512_file,
    upload_document,
    utc_iso,
    utc_now,
    write_secret_dcf,
)


def validate_local(cfg: dict[str, str]) -> Path:
    if not shutil.which("dar"):
        raise RuntimeError("Comando 'dar' non trovato. Installa il pacchetto: apt install dar")
    source = Path(cfg_required(cfg, "SOURCE")).expanduser().resolve()
    if not source.is_dir():
        raise RuntimeError(f"SOURCE non è una directory: {source}")
    if cfg_bool(cfg, "REQUIRE_SOURCE_MOUNT", True) and not os.path.ismount(source):
        raise RuntimeError(
            f"SOURCE={source} non risulta un mount point. È un controllo di sicurezza contro backup del mount vuoto. "
            "Se SOURCE è volutamente una sottocartella, imposta REQUIRE_SOURCE_MOUNT=false."
        )
    return source


def last_slice_path(job_dir: Path, journal: list[dict]) -> Path | None:
    if not journal:
        return None
    latest = max(journal, key=lambda x: int(x["slice_number"]))
    p = job_dir / latest["name"]
    return p if p.exists() else None


async def ensure_full_catalog(cfg, client, channel, full_summary, full_manifest, work: Path, passphrase: str) -> Path:
    catalogs = work / "catalogs"
    catalogs.mkdir(parents=True, exist_ok=True)
    base = catalogs / full_summary.backup_id
    expected = Path(str(base) + ".1.dar")
    if expected.exists():
        return base

    cat = full_manifest.get("catalog") or {}
    msg_id = cat.get("message_id")
    if not msg_id:
        raise RuntimeError(f"Il FULL {full_summary.backup_id} non contiene un catalogo isolato")
    msg = await client.get_messages(channel, ids=int(msg_id))
    if not msg:
        raise RuntimeError(f"Catalogo Telegram non trovato per FULL {full_summary.backup_id}")
    target = expected
    result = await client.download_media(msg, file=str(target))
    if not result:
        raise RuntimeError("Download catalogo fallito")
    if target.stat().st_size != int(cat["size"]) or sha512_file(target) != cat["sha512"]:
        target.unlink(missing_ok=True)
        raise RuntimeError("Catalogo FULL scaricato ma hash/dimensione non corrisponde")
    os.chmod(target, 0o600)
    return base


async def run_backup(args) -> None:
    cfg = load_config(args.config)
    namespace = get_namespace(cfg)
    source = validate_local(cfg)
    work = get_work_dir(cfg)
    work.mkdir(parents=True, exist_ok=True)
    (work / "jobs").mkdir(exist_ok=True)
    (work / "catalogs").mkdir(exist_ok=True)

    lock_path = work / "backup.lock"
    lock_f = lock_path.open("w")
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("Un altro backup è già in esecuzione")

    passphrase = read_passphrase(cfg, interactive=False)
    client = await make_client(cfg)
    current_remote_ids: list[int] = []
    backup_committed = False
    job_dir: Path | None = None
    try:
        channel = await resolve_channel(client, cfg)
        summaries = await discover_manifest_summaries(client, channel, namespace=namespace)
        fulls = [s for s in summaries if s.kind == "FULL"]
        latest_full = fulls[0] if fulls else None

        every = int(cfg.get("FULL_EVERY_MONTHS", "6"))
        force = args.force
        if force == "full" or latest_full is None:
            kind = "FULL"
        elif force == "diff":
            kind = "DIFF"
        else:
            age = month_distance(parse_iso(latest_full.created_utc), utc_now())
            kind = "FULL" if age >= every else "DIFF"

        full_manifest = None
        ref_catalog_base = None
        if kind == "DIFF":
            if latest_full is None:
                raise RuntimeError("Impossibile creare DIFF: non esiste un FULL")
            full_manifest = await download_manifest(client, channel, latest_full, work / "tmp")
            ref_catalog_base = await ensure_full_catalog(
                cfg, client, channel, latest_full, full_manifest, work, passphrase
            )

        backup_id = new_backup_id(kind)
        base_full_id = backup_id if kind == "FULL" else latest_full.backup_id
        created = utc_iso()
        job_dir = work / "jobs" / backup_id
        job_dir.mkdir(parents=True, exist_ok=False)
        os.chmod(job_dir, 0o700)
        archive_base = job_dir / "archive"
        journal = job_dir / "journal.jsonl"

        dcf = job_dir / "secret-create.dcf"
        write_secret_dcf(dcf, passphrase, current=True, reference=(kind == "DIFF"))

        hook_script = Path(__file__).resolve().with_name("slice_upload.py")
        hook = " ".join([
            shlex.quote(sys.executable),
            shlex.quote(str(hook_script)),
            "--config", shlex.quote(str(Path(args.config).resolve())),
            "--job-dir", shlex.quote(str(job_dir)),
            "--backup-id", shlex.quote(backup_id),
            "--namespace", shlex.quote(namespace),
            "--kind", kind,
            "--slice-path", '"%p/%b.%N.%e"',
            "--slice-number", '"%n"',
            "--context", '"%c"',
        ])

        cmd = [
            "dar", "-Q", "-c", str(archive_base),
            "-R", str(source),
            "-s", cfg.get("SLICE_SIZE", "1900M"),
            "-9", ("4,1" if kind == "DIFF" else "4"),
            f"--compression={cfg.get('COMPRESSION', 'zstd:6')}",
            "-B", str(dcf),
            "-E", hook,
        ]
        if kind == "DIFF":
            cmd += ["-A", str(ref_catalog_base)]

        print(f"Backup {kind} {backup_id} di {source} [namespace={namespace}]")

        # DAR invokes slice_upload.py as a child process. That hook opens the
        # same Telethon SQLite session, so the parent must release it while DAR
        # is running to avoid sqlite3.OperationalError: database is locked.
        await client.disconnect()
        try:
            run_checked(cmd)
        finally:
            if not client.is_connected():
                await client.connect()

        slices = read_jsonl(journal)
        if not slices:
            raise RuntimeError("DAR ha terminato senza slice registrate")
        slices.sort(key=lambda x: int(x["slice_number"]))
        current_remote_ids.extend(int(x["message_id"]) for x in slices)

        # L'ultima slice è stata lasciata locale dall'hook.
        last_local = last_slice_path(job_dir, slices)
        catalog_info = None
        if kind == "FULL":
            if not last_local:
                raise RuntimeError("Ultima slice FULL non disponibile per isolare il catalogo")
            catalog_base_tmp = job_dir / "catalog"
            dcf_iso = job_dir / "secret-isolate.dcf"
            # L'archivio sorgente è cifrato (-J); cifriamo anche il catalogo isolato (-K).
            write_secret_dcf(dcf_iso, passphrase, current=True, reference=True)
            run_checked([
                "dar", "-Q", "-C", str(catalog_base_tmp),
                "-A", str(archive_base),
                "-9", "1,4",
                "-B", str(dcf_iso),
            ])
            catalog_file = Path(str(catalog_base_tmp) + ".1.dar")
            if not catalog_file.exists():
                raise RuntimeError("DAR non ha creato il catalogo isolato atteso")
            cat_hash = sha512_file(catalog_file)
            cat_caption = f"{CATALOG_TAG} backup={backup_id} ns={namespace} created={created}"
            cat_msg = await upload_document(client, channel, catalog_file, cat_caption)
            current_remote_ids.append(int(cat_msg.id))
            catalog_info = {
                "name": f"{backup_id}.catalog.1.dar",
                "size": catalog_file.stat().st_size,
                "sha512": cat_hash,
                "message_id": int(cat_msg.id),
            }
            persistent_catalog = work / "catalogs" / f"{backup_id}.1.dar"
            shutil.copy2(catalog_file, persistent_catalog)
            os.chmod(persistent_catalog, 0o600)

        if last_local:
            last_local.unlink(missing_ok=True)

        manifest = {
            "format": FORMAT_VERSION,
            "namespace": namespace,
            "backup_id": backup_id,
            "kind": kind,
            "base_full_id": base_full_id,
            "created_utc": created,
            "source_label": cfg.get("SOURCE_LABEL", source.name or "data"),
            "slice_size": cfg.get("SLICE_SIZE", "1900M"),
            "compression": cfg.get("COMPRESSION", "zstd:6"),
            "dar_version": dar_version(),
            "slices": slices,
            "catalog": catalog_info,
            "complete": True,
        }
        manifest_path = job_dir / f"manifest-{backup_id}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        caption = (
            f"{MANIFEST_TAG} backup={backup_id} kind={kind} base={base_full_id} "
            f"ns={namespace} created={created}"
        )
        manifest_msg = await upload_document(client, channel, manifest_path, caption)
        current_remote_ids.append(int(manifest_msg.id))
        backup_committed = True

        # Solo dopo il manifest COMPLETE eliminiamo i backup precedenti dello
        # STESSO namespace. I namespace separano test e backup reali nello stesso canale.
        fresh_summaries = await discover_manifest_summaries(client, channel, namespace=namespace)
        temp_del = work / "tmp-delete"
        if kind == "FULL":
            for s in fresh_summaries:
                if s.backup_id != backup_id:
                    print(f"Rotazione: elimino backup precedente {s.backup_id}")
                    await delete_backup_from_manifest(client, channel, s, temp_del)
            # Rimuove cataloghi locali di generazioni precedenti in questo WORK_DIR.
            for p in (work / "catalogs").glob("*.dar"):
                if not p.name.startswith(backup_id + "."):
                    p.unlink(missing_ok=True)
        elif cfg_bool(cfg, "KEEP_ONLY_LATEST_DIFF", True):
            for s in fresh_summaries:
                if s.kind == "DIFF" and s.base_full_id == base_full_id and s.backup_id != backup_id:
                    print(f"Rotazione: elimino differenziale precedente {s.backup_id}")
                    await delete_backup_from_manifest(client, channel, s, temp_del)

        print(f"Backup completato: {backup_id} ({kind}), {len(slices)} slice")
        current_remote_ids.clear()

    except Exception:
        # Best effort: pulizia SOLO se il manifest COMPLETE non è stato pubblicato.
        # Se fallisce la rotazione dopo il commit, il nuovo backup resta intatto.
        if not backup_committed and job_dir is not None:
            for entry in read_jsonl(job_dir / "journal.jsonl"):
                mid = entry.get("message_id")
                if mid and int(mid) not in current_remote_ids:
                    current_remote_ids.append(int(mid))
        if (not backup_committed) and current_remote_ids:
            try:
                channel = await resolve_channel(client, cfg)
                print("Errore: provo a rimuovere le parti del backup incompleto da Telegram...")
                await delete_message_ids(client, channel, current_remote_ids)
            except Exception as cleanup_error:
                print(f"ATTENZIONE: cleanup Telegram incompleto: {cleanup_error}", file=sys.stderr)
        raise
    finally:
        await client.disconnect()
        if job_dir and job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
        fcntl.flock(lock_f, fcntl.LOCK_UN)
        lock_f.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Backup DAR cifrato su canale Telegram privato")
    p.add_argument("--config", default="/etc/telegram-dar-backup/config.env")
    p.add_argument("--force", choices=["auto", "full", "diff"], default="auto",
                   help="auto=FULL ogni N mesi, altrimenti DIFF; full/diff forza il tipo")
    args = p.parse_args()
    try:
        asyncio.run(run_backup(args))
    except Exception as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
