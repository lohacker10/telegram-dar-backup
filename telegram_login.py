#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from tdb_common import get_session_file, load_config, cfg_required


async def main_async(args) -> None:
    from telethon import TelegramClient, utils

    cfg = load_config(args.config)
    api_id = int(cfg_required(cfg, "TELEGRAM_API_ID"))
    api_hash = cfg_required(cfg, "TELEGRAM_API_HASH")
    session = get_session_file(cfg)
    session.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(session), api_id, api_hash)
    await client.start(phone=lambda: input("Numero Telegram (es. +391234567890): ").strip())
    print(f"\nSessione salvata in: {session}")
    print("Proteggila con chmod 600: equivale, di fatto, a una credenziale di accesso.\n")

    print("Canali/gruppi visibili a questo account:")
    async for dialog in client.iter_dialogs():
        if getattr(dialog, "is_channel", False) or getattr(dialog, "is_group", False):
            print(f"  {utils.get_peer_id(dialog.entity):>16}  {dialog.name}")
    print("\nCopia l'ID del canale privato in TELEGRAM_CHANNEL_ID nel config.env.")
    await client.disconnect()
    if session.exists():
        os.chmod(session, 0o600)


def main() -> None:
    p = argparse.ArgumentParser(description="Primo login headless di Telethon e lista canali")
    p.add_argument("--config", default="/etc/telegram-dar-backup/config.env")
    p.add_argument("--list-channels", action="store_true", help="Mostra i canali dopo il login")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
