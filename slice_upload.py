#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from tdb_common import (
    SLICE_TAG,
    append_jsonl,
    load_config,
    make_client,
    resolve_channel,
    sha512_file,
)
from telegram_parallel import parse_upload_workers, upload_document_parallel


async def do_upload(args) -> None:
    cfg = load_config(args.config)
    path = Path(args.slice_path)
    if not path.exists():
        raise RuntimeError(f"Slice non trovata: {path}")

    size = path.stat().st_size
    digest = sha512_file(path)
    workers = parse_upload_workers(cfg.get("TELEGRAM_UPLOAD_WORKERS", "4"))
    client = await make_client(cfg)
    try:
        channel = await resolve_channel(client, cfg)
        caption = (
            f"{SLICE_TAG} backup={args.backup_id} kind={args.kind} "
            f"n={args.slice_number} ns={args.namespace}"
        )
        msg = await upload_document_parallel(
            client,
            channel,
            path,
            caption,
            workers=workers,
        )
        append_jsonl(Path(args.job_dir) / "journal.jsonl", {
            "name": path.name,
            "slice_number": int(args.slice_number),
            "size": size,
            "sha512": digest,
            "message_id": int(msg.id),
        })
    finally:
        await client.disconnect()

    # L'ultima slice viene tenuta solo finché backup.py non ha isolato il catalogo FULL.
    if args.context != "last_slice":
        path.unlink(missing_ok=True)


def main() -> None:
    p = argparse.ArgumentParser(description="Hook DAR: hash, upload Telegram e rimozione della slice locale")
    p.add_argument("--config", required=True)
    p.add_argument("--job-dir", required=True)
    p.add_argument("--backup-id", required=True)
    p.add_argument("--namespace", required=True)
    p.add_argument("--kind", choices=["FULL", "DIFF"], required=True)
    p.add_argument("--slice-path", required=True)
    p.add_argument("--slice-number", required=True)
    p.add_argument("--context", required=True)
    args = p.parse_args()
    asyncio.run(do_upload(args))


if __name__ == "__main__":
    main()
