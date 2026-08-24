#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from tdb_common import load_config, make_client, resolve_channel, sha512_file
from telegram_parallel import download_document_parallel, parse_download_workers


SLICE_RE = re.compile(r"\.(\d+)\.dar$")


async def fetch(args) -> None:
    cfg = load_config(args.config)
    work = Path(args.restore_dir)
    mapping = json.loads(Path(args.map_file).read_text(encoding="utf-8"))
    slices = mapping["slices"]

    requested = int(args.slice_number)
    last_item = max(slices, key=lambda x: int(x["slice_number"]))
    if requested == 0:
        item = last_item
    else:
        matches = [x for x in slices if int(x["slice_number"]) == requested]
        if not matches:
            raise RuntimeError(f"Slice {requested} non presente nel manifest")
        item = matches[0]

    target = work / item["name"]
    last_target = work / last_item["name"]

    # DAR direct-access first needs the final slice for its internal catalogue,
    # then asks data slices on demand. Keep the final slice plus the currently
    # requested slice and remove the others. This stays bounded to at most two
    # DAR slices while avoiding a second download of the catalogue/last slice.
    keep = {target.resolve(), last_target.resolve()}
    for p in work.glob("archive.*.dar"):
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        if resolved not in keep:
            p.unlink(missing_ok=True)

    if target.exists():
        if target.stat().st_size == int(item["size"]) and sha512_file(target) == item["sha512"]:
            return
        target.unlink(missing_ok=True)

    client = await make_client(cfg)
    try:
        channel = await resolve_channel(client, cfg)
        msg = await client.get_messages(channel, ids=int(item["message_id"]))
        if not msg:
            raise RuntimeError(f"Messaggio Telegram mancante: {item['message_id']}")

        workers = parse_download_workers(
            cfg.get("TELEGRAM_DOWNLOAD_WORKERS", "4")
        )
        last_pct = -10

        def progress(done: int, total: int) -> None:
            nonlocal last_pct
            if total:
                pct = done * 100 // total
                if pct >= 100 or pct >= last_pct + 10:
                    last_pct = pct
                    suffix = f" (parallel={workers})" if workers > 1 else ""
                    print(f"Download {target.name}: {pct:3d}%{suffix}", flush=True)

        result = await download_document_parallel(
            client,
            msg,
            target,
            size=int(item["size"]),
            workers=workers,
            progress_callback=progress,
        )
        if not result:
            raise RuntimeError(f"Download fallito: {target.name}")
    finally:
        await client.disconnect()

    if target.stat().st_size != int(item["size"]):
        target.unlink(missing_ok=True)
        raise RuntimeError(f"Dimensione errata: {target.name}")
    if sha512_file(target) != item["sha512"]:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-512 errato: {target.name}")


def main() -> None:
    p = argparse.ArgumentParser(description="Hook DAR restore: scarica e verifica la slice richiesta")
    p.add_argument("--config", required=True)
    p.add_argument("--restore-dir", required=True)
    p.add_argument("--map-file", required=True)
    p.add_argument("--slice-number", required=True)
    p.add_argument("--context", required=True)
    args = p.parse_args()
    asyncio.run(fetch(args))


if __name__ == "__main__":
    main()
