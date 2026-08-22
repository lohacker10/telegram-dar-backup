#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import shutil
import sys
from pathlib import Path

from tdb_common import (
    discover_manifest_summaries,
    download_manifest,
    ensure_empty_or_create,
    get_work_dir,
    load_config,
    make_client,
    read_passphrase,
    resolve_channel,
    run_checked,
    write_secret_dcf,
)


def choose_summary(summaries):
    print("Backup disponibili:\n")
    for i, s in enumerate(summaries, 1):
        print(f"  [{i}] {s.created_utc:20} {s.kind:4}  {s.backup_id}")
    while True:
        raw = input("\nNumero del backup da ripristinare: ").strip()
        try:
            idx = int(raw)
            if 1 <= idx <= len(summaries):
                return summaries[idx - 1]
        except ValueError:
            pass
        print("Scelta non valida.")


def run_archive_restore(cfg_path: Path, manifest: dict, dest: Path, work: Path, passphrase: str, verify_only: bool) -> None:
    work.mkdir(parents=True, exist_ok=True)
    map_file = work / "map.json"
    map_file.write_text(json.dumps({"slices": manifest["slices"]}, indent=2) + "\n", encoding="utf-8")
    secret = work / "secret.dcf"
    write_secret_dcf(secret, passphrase, current=True, reference=False)

    hook_script = Path(__file__).resolve().with_name("restore_fetch.py")
    hook = " ".join([
        shlex.quote(sys.executable), shlex.quote(str(hook_script)),
        "--config", shlex.quote(str(cfg_path)),
        "--restore-dir", shlex.quote(str(work)),
        "--map-file", shlex.quote(str(map_file)),
        "--slice-number", '"%n"',
        "--context", '"%c"',
    ])

    archive_base = work / "archive"
    if verify_only:
        cmd = ["dar", "-Q", "-t", str(archive_base), "--sequential-read", "-9", "4", "-B", str(secret), "-E", hook]
    else:
        cmd = [
            "dar", "-Q", "-x", str(archive_base), "--sequential-read", "-9", "4",
            "-R", str(dest), "-wa", "-B", str(secret), "-E", hook,
        ]
    try:
        run_checked(cmd)
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def restore_async(args) -> None:
    cfg_path = Path(args.config).expanduser().resolve()
    cfg = load_config(cfg_path)
    if not shutil.which("dar"):
        raise RuntimeError("Comando 'dar' non trovato. Installa: apt install dar")
    work = get_work_dir(cfg)
    work.mkdir(parents=True, exist_ok=True)

    client = await make_client(cfg)
    try:
        channel = await resolve_channel(client, cfg)
        summaries = await discover_manifest_summaries(client, channel)
        if not summaries:
            raise RuntimeError("Nessun manifest di backup trovato nel canale Telegram")

        if args.list:
            for s in summaries:
                print(f"{s.created_utc}\t{s.kind}\t{s.backup_id}\tbase={s.base_full_id}")
            return

        if args.backup_id:
            selected = next((s for s in summaries if s.backup_id == args.backup_id), None)
            if not selected:
                raise RuntimeError(f"Backup non trovato: {args.backup_id}")
        else:
            selected = choose_summary(summaries)

        selected_manifest = await download_manifest(client, channel, selected, work / "restore-manifests")
        chain = []
        if selected.kind == "FULL":
            chain = [selected_manifest]
        else:
            base = next((s for s in summaries if s.backup_id == selected.base_full_id and s.kind == "FULL"), None)
            if not base:
                raise RuntimeError(f"FULL di riferimento non trovato: {selected.base_full_id}")
            base_manifest = await download_manifest(client, channel, base, work / "restore-manifests")
            chain = [base_manifest, selected_manifest]
    finally:
        await client.disconnect()

    passphrase = read_passphrase(cfg, interactive=True)
    if args.verify:
        for manifest in chain:
            print(f"Verifico {manifest['backup_id']} ({manifest['kind']})...")
            run_archive_restore(
                cfg_path, manifest, Path("/"), work / "restore" / manifest["backup_id"], passphrase, True
            )
        print("Verifica completata senza errori.")
        return

    if not args.dest:
        raise RuntimeError("Per il ripristino serve --dest /percorso/destinazione")
    dest = Path(args.dest).expanduser().resolve()
    ensure_empty_or_create(dest, args.allow_nonempty)

    for manifest in chain:
        print(f"Ripristino {manifest['backup_id']} ({manifest['kind']}) -> {dest}")
        run_archive_restore(
            cfg_path, manifest, dest, work / "restore" / manifest["backup_id"], passphrase, False
        )
    print(f"Ripristino completato in: {dest}")


def main() -> None:
    p = argparse.ArgumentParser(description="Ripristina/valida backup DAR dal canale Telegram")
    p.add_argument("--config", default="/etc/telegram-dar-backup/config.env")
    p.add_argument("--list", action="store_true", help="Elenca i backup disponibili")
    p.add_argument("--backup-id", help="Backup specifico; se omesso viene mostrato un menu")
    p.add_argument("--dest", help="Directory di destinazione del restore")
    p.add_argument("--allow-nonempty", action="store_true", help="Consenti destinazione non vuota (sconsigliato)")
    p.add_argument("--verify", action="store_true", help="Scarica progressivamente e testa DAR senza estrarre")
    args = p.parse_args()
    try:
        asyncio.run(restore_async(args))
    except Exception as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
