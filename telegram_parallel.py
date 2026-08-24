#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import copy
import secrets
from pathlib import Path
from typing import Callable

from telethon import errors, functions, types, utils
from telethon.network import MTProtoSender
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InvokeWithLayerRequest

from tdb_common import upload_document

PART_SIZE = 512 * 1024
BIG_FILE_THRESHOLD = 10 * 1024 * 1024


class _ParallelDownloadFallback(RuntimeError):
    """Signal that this transfer should retry through Telethon's normal path."""


def _parse_workers(
    value: str | int | None,
    *,
    key: str,
    default: int = 4,
) -> int:
    raw = default if value is None else value
    try:
        workers = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{key} non valido: {raw!r}") from exc
    if workers < 1 or workers > 32:
        raise RuntimeError(f"{key} deve essere compreso tra 1 e 32")
    return workers


def parse_upload_workers(value: str | int | None, default: int = 4) -> int:
    return _parse_workers(value, key="TELEGRAM_UPLOAD_WORKERS", default=default)


def parse_download_workers(value: str | int | None, default: int = 4) -> int:
    return _parse_workers(value, key="TELEGRAM_DOWNLOAD_WORKERS", default=default)


async def upload_document_parallel(
    client,
    channel,
    path: Path,
    caption: str,
    *,
    workers: int = 4,
):
    """Upload one document with multiple MTProto file-part requests in flight.

    workers=1 intentionally falls back to Telethon's normal send_file path so it
    can be used as a performance/control baseline. Files <=10 MiB also use the
    normal path because the backup slices are the only transfers worth
    parallelizing here.
    """
    workers = parse_upload_workers(workers)
    path = Path(path)
    size = path.stat().st_size

    if workers == 1 or size <= BIG_FILE_THRESHOLD:
        return await upload_document(client, channel, path, caption)

    part_count = (size + PART_SIZE - 1) // PART_SIZE
    file_id = secrets.randbits(63)
    pending: set[asyncio.Task] = set()
    completed_bytes = 0
    last_pct = -10

    async def send_part(part_index: int, data: bytes) -> int:
        ok = await client(
            functions.upload.SaveBigFilePartRequest(
                file_id=file_id,
                file_part=part_index,
                file_total_parts=part_count,
                bytes=data,
            )
        )
        if not ok:
            raise RuntimeError(f"Telegram ha rifiutato la parte {part_index + 1}/{part_count}")
        return len(data)

    async def consume(done: set[asyncio.Task]) -> None:
        nonlocal completed_bytes, last_pct
        completed_bytes += sum(await asyncio.gather(*done))
        pct = min(100, completed_bytes * 100 // size)
        if pct >= 100 or pct >= last_pct + 10:
            last_pct = pct
            print(f"Upload {path.name}: {pct:3d}% (parallel={workers})", flush=True)

    print(
        f"Upload parallelo {path.name}: {workers} richieste in-flight, "
        f"{part_count} parti da {PART_SIZE // 1024} KiB",
        flush=True,
    )

    try:
        with path.open("rb") as src:
            for part_index in range(part_count):
                data = src.read(PART_SIZE)
                if not data:
                    raise RuntimeError(
                        f"EOF inatteso durante upload: parte {part_index + 1}/{part_count}"
                    )
                pending.add(asyncio.create_task(send_part(part_index, data)))

                if len(pending) >= workers:
                    done, pending = await asyncio.wait(
                        pending, return_when=asyncio.FIRST_COMPLETED
                    )
                    await consume(done)

        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            await consume(done)
    except Exception:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        raise

    uploaded = types.InputFileBig(
        id=file_id,
        parts=part_count,
        name=path.name,
    )
    msg = await client.send_file(
        channel,
        file=uploaded,
        caption=caption,
        force_document=True,
    )
    print(flush=True)

    remote_size = getattr(getattr(msg, "file", None), "size", None)
    if remote_size is not None and int(remote_size) != size:
        raise RuntimeError(
            f"Dimensione remota diversa per {path.name}: locale={size}, remoto={remote_size}"
        )
    return msg


class _ParallelDownloadPool:
    """Own independent MTProto senders for one file data center.

    This follows the same approach used by FastTelethon-style transfer helpers:
    every worker owns a distinct network connection. For the client's home DC
    the existing authorization key can be reused. For a different media DC the
    first sender exports/imports authorization and the resulting auth key is
    reused by the other senders.
    """

    def __init__(self, client, dc_id: int) -> None:
        self.client = client
        self.dc_id = int(dc_id or client.session.dc_id)
        self.auth_key = (
            client.session.auth_key
            if self.dc_id == int(client.session.dc_id)
            else None
        )
        self.senders: list[MTProtoSender] = []

    async def _create_sender(self) -> MTProtoSender:
        dc = await self.client._get_dc(self.dc_id)
        sender = MTProtoSender(self.auth_key, loggers=self.client._log)
        await sender.connect(
            self.client._connection(
                dc.ip_address,
                dc.port,
                dc.id,
                loggers=self.client._log,
                proxy=self.client._proxy,
                local_addr=self.client._local_addr,
            )
        )

        if self.auth_key is None:
            # Only the first cross-DC sender reaches this branch. It must finish
            # before the remaining senders are created, otherwise several
            # concurrent ExportAuthorization requests would be unnecessary and
            # more likely to hit flood limits.
            auth = await self.client(
                functions.auth.ExportAuthorizationRequest(self.dc_id)
            )
            init_request = copy.copy(self.client._init_request)
            init_request.query = functions.auth.ImportAuthorizationRequest(
                id=auth.id,
                bytes=auth.bytes,
            )
            request = InvokeWithLayerRequest(LAYER, init_request)
            await sender.send(request)
            self.auth_key = sender.auth_key

        return sender

    async def start(self, count: int) -> list[MTProtoSender]:
        if count < 1:
            return []

        # Create the first sender before the others so a cross-DC authorization
        # is exported/imported exactly once. Keeping setup sequential also makes
        # cleanup deterministic if one connection fails during initialization.
        for _ in range(count):
            self.senders.append(await self._create_sender())
        return self.senders

    async def close(self) -> None:
        if not self.senders:
            return
        await asyncio.gather(
            *(sender.disconnect() for sender in self.senders),
            return_exceptions=True,
        )
        self.senders.clear()


async def download_document_parallel(
    client,
    message,
    target: Path,
    *,
    size: int,
    workers: int = 4,
    progress_callback: Callable[[int, int], object] | None = None,
):
    """Download one Telegram document over multiple MTProto connections.

    Each worker owns a distinct MTProtoSender/network connection and requests
    512 KiB chunks at interleaved offsets. Workers write disjoint regions of one
    pre-sized local DAR slice, so temporary storage remains roughly one slice.

    workers=1 intentionally uses Telethon's normal ``download_media`` path as a
    baseline and compatibility fallback. CDN redirects, expired file references
    and DC migration surprises also fall back to the standard Telethon path.
    """
    workers = parse_download_workers(workers)
    target = Path(target)
    size = int(size)
    if size < 0:
        raise RuntimeError(f"Dimensione download non valida: {size}")

    target.parent.mkdir(parents=True, exist_ok=True)

    if workers == 1 or size <= BIG_FILE_THRESHOLD:
        return await client.download_media(
            message,
            file=str(target),
            progress_callback=progress_callback,
        )

    document = getattr(message, "document", None)
    if document is None:
        return await client.download_media(
            message,
            file=str(target),
            progress_callback=progress_callback,
        )

    dc_id, location = utils.get_input_location(document)
    dc_id = int(dc_id or client.session.dc_id)

    part_count = (size + PART_SIZE - 1) // PART_SIZE
    active_workers = min(workers, max(1, part_count))
    stride = active_workers * PART_SIZE
    completed_bytes = 0
    pool = _ParallelDownloadPool(client, dc_id)

    # Pre-size the file. Every worker opens its own descriptor and writes only
    # its assigned, non-overlapping offsets.
    with target.open("wb") as out:
        out.truncate(size)

    print(
        f"Download parallelo {target.name}: {active_workers} connessioni MTProto, "
        f"DC {dc_id}, {part_count} parti da {PART_SIZE // 1024} KiB",
        flush=True,
    )

    async def report(delta: int) -> None:
        nonlocal completed_bytes
        completed_bytes += delta
        if progress_callback:
            result = progress_callback(completed_bytes, size)
            if asyncio.iscoroutine(result):
                await result

    async def worker(worker_index: int, sender: MTProtoSender) -> None:
        current_offset = worker_index * PART_SIZE
        if current_offset >= size:
            return

        with target.open("r+b", buffering=0) as out:
            while current_offset < size:
                request = functions.upload.GetFileRequest(
                    location=location,
                    offset=current_offset,
                    limit=PART_SIZE,
                )
                result = await client._call(sender, request)

                if isinstance(result, types.upload.FileCdnRedirect):
                    raise _ParallelDownloadFallback(
                        "Telegram ha richiesto un CDN"
                    )

                data = bytes(result.bytes)
                expected_size = min(PART_SIZE, size - current_offset)
                if len(data) != expected_size:
                    raise RuntimeError(
                        f"Chunk Telegram incompleto a offset {current_offset}: "
                        f"attesi={expected_size}, ricevuti={len(data)}"
                    )

                out.seek(current_offset)
                written = out.write(data)
                if written != len(data):
                    raise RuntimeError(
                        f"Scrittura locale incompleta a offset {current_offset}: "
                        f"attesi={len(data)}, scritti={written}"
                    )

                await report(len(data))
                current_offset += stride

    try:
        senders = await pool.start(active_workers)
        tasks = [
            asyncio.create_task(worker(index, sender))
            for index, sender in enumerate(senders)
        ]
        try:
            await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        if completed_bytes != size:
            raise RuntimeError(
                f"Download incompleto per {target.name}: "
                f"attesi={size}, ricevuti={completed_bytes}"
            )
    except (
        _ParallelDownloadFallback,
        errors.FileReferenceExpiredError,
        errors.FilerefUpgradeNeededError,
        errors.FileMigrateError,
    ) as exc:
        await pool.close()
        target.unlink(missing_ok=True)
        print(
            f"Download parallelo non utilizzabile ({exc}); "
            "riprovo con Telethon standard.",
            flush=True,
        )
        return await client.download_media(
            message,
            file=str(target),
            progress_callback=progress_callback,
        )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await pool.close()

    return str(target)
