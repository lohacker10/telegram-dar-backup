#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from tdb_common import load_config, make_client, resolve_channel, sha512_file


SLICE_RE = re.compile(r"\.(\d+)\.dar$")


async def fetch(args) -> None:
    cfg = load_config(args.config)
    work = Path(args.restore_dir)
    mapping = json.loads(Path(args.map_file).read_text(encoding="utf-8"))
    slices = mapping["slices"]

    requested = int(args.slice_number)
    if requested == 0:
        item = max(slices, key=lambda x: int(x["slice_number"]))
    else:
        matches = [x for x in slices if int(x["slice_number"]) == requested]
        if not matches:
            raise RuntimeError(f"Slice {requested} non presente nel manifest")
        item = matches[0]

    # DAR ha chiuso la slice precedente prima di chiamare -E per la successiva.
    if requested > 0:
        for p in work.glob("archive.*.dar"):
            m = SLICE_RE.search(p.name)
            if m and int(m.group(1)) < requested:
                p.unlink(missing_ok=True)

    target = work / item["name"]
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

        last_pct = -10

        def progress(done: int, total: int) -> None:
            nonlocal last_pct
            if total:
                pct = done * 100 // total
                if pct >= 100 or pct >= last_pct + 10:
                    last_pct = pct
                    print(f"Download {target.name}: {pct:3d}%", flush=True)

        result = await client.download_media(msg, file=str(target), progress_callback=progress)
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
