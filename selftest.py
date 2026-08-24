#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from tdb_common import (
    CATALOG_TAG,
    MANIFEST_TAG,
    SLICE_TAG,
    cfg_required,
    delete_message_ids,
    discover_manifest_summaries,
    download_manifest,
    get_namespace,
    get_session_file,
    get_work_dir,
    load_config,
    make_client,
    parse_caption,
    read_passphrase,
    resolve_channel,
)

MIB = 1024 * 1024
SELFTEST_PREFIX = "selftest-"
CONFIG_OVERRIDE_KEYS = {
    "SOURCE", "SOURCE_LABEL", "WORK_DIR", "SLICE_SIZE", "COMPRESSION",
    "FULL_EVERY_MONTHS", "REQUIRE_SOURCE_MOUNT", "KEEP_ONLY_LATEST_DIFF",
}


def log(message: str) -> None:
    print(f"\n=== {message} ===", flush=True)


def quote_env(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@+,-]+", value):
        return value
    return json.dumps(value, ensure_ascii=False)


def write_config(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(f"{key}={quote_env(str(value))}" for key, value in values.items()) + "\n"
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)


def sanitized_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("TELEGRAM_", "DAR_", "TDB_")) or key in CONFIG_OVERRIDE_KEYS:
            env.pop(key, None)
    return env


def run_command(args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    print("+ " + " ".join(str(x) for x in args), flush=True)
    proc = subprocess.run(
        args,
        env=sanitized_subprocess_env(),
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if proc.returncode != 0:
        detail = ""
        if capture:
            detail = f"\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        raise RuntimeError(f"Comando fallito ({proc.returncode}): {args[0]}{detail}")
    return proc


def compare_trees(source: Path, restored: Path) -> None:
    proc = run_command([
        "rsync", "-aHAXnci", "--delete",
        str(source) + "/", str(restored) + "/",
    ], capture=True)
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]

    # The restore destination directory is created by restore.py and is only a
    # container for the archived tree. Its own mtime naturally changes while
    # children are extracted, so it is not meaningful archive payload. Keep
    # checking directory timestamps everywhere below the root; ignore only the
    # exact rsync itemized root-directory mtime difference.
    ignored_root_mtime = ".d..t...... ./"
    differences = [line for line in lines if line.strip() != ignored_root_mtime]
    if differences:
        preview = "\n".join(differences[:40])
        raise RuntimeError(
            "Il restore differisce dalla sorgente secondo rsync -aHAXnci --delete:\n" + preview
        )
    if lines:
        print("Confronto rsync: OK (ignorato solo mtime della directory radice di destinazione)", flush=True)
    else:
        print("Confronto rsync: OK (nessuna differenza)", flush=True)


def create_fixture(source: Path, data_mib: int) -> None:
    source.mkdir(parents=True, exist_ok=False)
    (source / "nested").mkdir()
    (source / "nested" / "keep.txt").write_text("telegram-dar-backup selftest\n", encoding="utf-8")
    (source / "modified.txt").write_text("versione A\n", encoding="utf-8")
    (source / "deleted.txt").write_text("sarò eliminato nel DIFF\n", encoding="utf-8")
    (source / "rename-me.txt").write_text("sarò rinominato\n", encoding="utf-8")
    (source / "unicode-è.txt").write_text("unicode ✓\n", encoding="utf-8")
    (source / "hardlink.bin").write_bytes(os.urandom(1024 * 1024))
    os.link(source / "hardlink.bin", source / "hardlink-copy.bin")
    os.symlink("nested/keep.txt", source / "keep-link")
    os.chmod(source / "modified.txt", 0o640)
    try:
        os.setxattr(source / "nested" / "keep.txt", b"user.tdb_selftest", b"ok")
    except (AttributeError, OSError):
        print("Nota: filesystem senza user xattr; il test continua senza xattr fixture.", flush=True)

    bulk = source / "bulk-random.bin"
    log(f"Creo {data_mib} MiB di dati incomprimibili per forzare >=2 slice da 1900M")
    run_command([
        "dd", "if=/dev/urandom", f"of={bulk}", "bs=1M", f"count={data_mib}", "status=progress"
    ])


def mutate_for_diff_b(source: Path) -> None:
    with (source / "modified.txt").open("a", encoding="utf-8") as f:
        f.write("versione B\n")
    (source / "deleted.txt").unlink()
    (source / "new-file.txt").write_text("nuovo nel DIFF B\n", encoding="utf-8")
    (source / "rename-me.txt").rename(source / "renamed.txt")


def mutate_for_diff_c(source: Path) -> None:
    with (source / "modified.txt").open("a", encoding="utf-8") as f:
        f.write("versione C - catalog recovery\n")
    (source / "nested" / "second-new.txt").write_text("aggiunto nel DIFF C\n", encoding="utf-8")


def mutate_for_diff_e(source: Path) -> None:
    with (source / "new-file.txt").open("a", encoding="utf-8") as f:
        f.write("modificato dopo FULL D\n")
    (source / "unicode-è.txt").unlink()
    (source / "final.txt").write_text("DIFF E\n", encoding="utf-8")


async def summaries_for_cfg(cfg: dict[str, str]):
    namespace = get_namespace(cfg)
    client = await make_client(cfg)
    try:
        channel = await resolve_channel(client, cfg)
        return await discover_manifest_summaries(client, channel, namespace=namespace)
    finally:
        await client.disconnect()


async def manifest_for_summary(cfg: dict[str, str], summary, dest: Path) -> dict:
    client = await make_client(cfg)
    try:
        channel = await resolve_channel(client, cfg)
        return await download_manifest(client, channel, summary, dest)
    finally:
        await client.disconnect()


async def cleanup_namespace_messages(cfg: dict[str, str], namespace: str) -> int:
    if not namespace.startswith(SELFTEST_PREFIX):
        raise RuntimeError("Per sicurezza il cleanup accetta solo namespace selftest-*")
    client = await make_client(cfg)
    try:
        channel = await resolve_channel(client, cfg)
        ids: set[int] = set()
        seen: set[int] = set()

        async def consume(iterator) -> None:
            async for msg in iterator:
                mid = int(msg.id)
                if mid in seen:
                    continue
                seen.add(mid)
                caption = getattr(msg, "message", None)
                for tag in (MANIFEST_TAG, SLICE_TAG, CATALOG_TAG):
                    fields = parse_caption(caption, tag)
                    if fields and fields.get("ns") == namespace:
                        ids.add(mid)
                        break

        try:
            await consume(client.iter_messages(channel, search=namespace, limit=2000))
        except Exception:
            pass
        # Fallback also catches messages not indexed by Telegram search.
        await consume(client.iter_messages(channel, limit=10000))
        if ids:
            await delete_message_ids(client, channel, sorted(ids))
        return len(ids)
    finally:
        await client.disconnect()


def assert_state(summaries, *, full_id: str, diff_id: str | None = None) -> None:
    fulls = [s for s in summaries if s.kind == "FULL"]
    diffs = [s for s in summaries if s.kind == "DIFF"]
    if len(fulls) != 1 or fulls[0].backup_id != full_id:
        raise RuntimeError(
            f"Stato namespace inatteso: FULL={[s.backup_id for s in fulls]}, atteso={full_id}"
        )
    if diff_id is None:
        if diffs:
            raise RuntimeError(f"DIFF inattesi: {[s.backup_id for s in diffs]}")
    else:
        if len(diffs) != 1 or diffs[0].backup_id != diff_id:
            raise RuntimeError(
                f"Stato namespace inatteso: DIFF={[s.backup_id for s in diffs]}, atteso={diff_id}"
            )
        if diffs[0].base_full_id != full_id:
            raise RuntimeError(
                f"DIFF {diff_id} usa base={diffs[0].base_full_id}, atteso={full_id}"
            )


def latest_of_kind(summaries, kind: str):
    return next((s for s in summaries if s.kind == kind), None)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Self-test end-to-end isolato: FULL/DIFF/verify/restore/rotazione/catalog recovery"
    )
    parser.add_argument("--config", default="/etc/telegram-dar-backup/config.env")
    parser.add_argument(
        "--temp-root", default="/var/tmp",
        help="Filesystem per fixture/stato/restore temporanei (servono circa 7-8 GiB liberi con i default)",
    )
    parser.add_argument(
        "--data-mib", type=int, default=2000,
        help="Dimensione del file random principale. Default 2000 MiB, > SLICE_SIZE=1900M.",
    )
    parser.add_argument(
        "--cleanup-namespace",
        help="Rimuove dal canale un namespace selftest-* rimasto da un test interrotto e termina.",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    backup_script = script_dir / "backup.py"
    restore_script = script_dir / "restore.py"
    prod_cfg_path = Path(args.config).expanduser().resolve()
    prod_cfg = load_config(prod_cfg_path)

    # Resolve and validate all credentials before creating any data.
    cfg_required(prod_cfg, "TELEGRAM_API_ID")
    cfg_required(prod_cfg, "TELEGRAM_API_HASH")
    cfg_required(prod_cfg, "TELEGRAM_CHANNEL_ID")
    read_passphrase(prod_cfg, interactive=False)
    session_file = get_session_file(prod_cfg)
    if not session_file.exists():
        raise RuntimeError(f"Sessione Telegram non trovata: {session_file}")

    if args.cleanup_namespace:
        removed = asyncio.run(cleanup_namespace_messages(prod_cfg, args.cleanup_namespace))
        print(f"Cleanup completato: rimossi {removed} messaggi per {args.cleanup_namespace}")
        return

    if args.data_mib <= 1900:
        raise RuntimeError("--data-mib deve essere >1900 per testare almeno due slice DAR da 1900M")
    for cmd in ("dar", "rsync", "dd"):
        if not shutil.which(cmd):
            raise RuntimeError(f"Comando richiesto non trovato: {cmd}")

    temp_parent = Path(args.temp_root).expanduser().resolve()
    temp_parent.mkdir(parents=True, exist_ok=True)
    required_free = (2 * args.data_mib + 3500) * MIB
    free = shutil.disk_usage(temp_parent).free
    if free < required_free:
        raise RuntimeError(
            f"Spazio insufficiente su {temp_parent}: liberi={free / MIB:.0f} MiB, "
            f"richiesti circa={required_free / MIB:.0f} MiB"
        )

    namespace = SELFTEST_PREFIX + uuid.uuid4().hex[:12]
    root = temp_parent / f"telegram-dar-backup-{namespace}"
    source = root / "source"
    backup_work = root / "backup-work"
    recovery_work = root / "recovery-work"
    restore_dest = root / "restored"
    test_cfg_path = root / "test.env"
    recovery_cfg_path = root / "recovery.env"
    temp_pass = root / "dar.pass"

    prod_work = get_work_dir(prod_cfg)
    prod_work.mkdir(parents=True, exist_ok=True)
    prod_lock = (prod_work / "backup.lock").open("a+")
    try:
        fcntl.flock(prod_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        prod_lock.close()
        raise RuntimeError("Un backup reale è in esecuzione: self-test annullato") from exc

    baseline_ids: set[str] = set()
    test_cfg: dict[str, str] | None = None
    success = False
    try:
        baseline = asyncio.run(summaries_for_cfg(prod_cfg))
        baseline_ids = {s.backup_id for s in baseline}
        prod_namespace = get_namespace(prod_cfg)
        log(f"Backup reali protetti: namespace={prod_namespace!r}, manifest={len(baseline_ids)}")

        root.mkdir(mode=0o700)
        passphrase = read_passphrase(prod_cfg, interactive=False)
        temp_pass.write_text(passphrase + "\n", encoding="utf-8")
        os.chmod(temp_pass, 0o600)

        common_values = {
            "TELEGRAM_API_ID": cfg_required(prod_cfg, "TELEGRAM_API_ID"),
            "TELEGRAM_API_HASH": cfg_required(prod_cfg, "TELEGRAM_API_HASH"),
            "TELEGRAM_CHANNEL_ID": cfg_required(prod_cfg, "TELEGRAM_CHANNEL_ID"),
            "TELEGRAM_SESSION_FILE": str(session_file),
            "TELEGRAM_UPLOAD_WORKERS": prod_cfg.get("TELEGRAM_UPLOAD_WORKERS", "4"),
            "TELEGRAM_DOWNLOAD_WORKERS": prod_cfg.get("TELEGRAM_DOWNLOAD_WORKERS", "4"),
            "DAR_PASSPHRASE_FILE": str(temp_pass),
            "COMPRESSION": prod_cfg.get("COMPRESSION", "zstd:6"),
            "SLICE_SIZE": "1900M",
            "FULL_EVERY_MONTHS": "6",
            "KEEP_ONLY_LATEST_DIFF": "true",
            "REQUIRE_SOURCE_MOUNT": "false",
            "SOURCE": str(source),
            "SOURCE_LABEL": "TDB-SELFTEST",
            "TDB_NAMESPACE": namespace,
        }
        test_values = dict(common_values, WORK_DIR=str(backup_work))
        recovery_values = dict(common_values, WORK_DIR=str(recovery_work))
        write_config(test_cfg_path, test_values)
        write_config(recovery_cfg_path, recovery_values)
        test_cfg = load_config(test_cfg_path)
        # Environment may override load_config in this process; pin the intended namespace/work dir.
        test_cfg.update(test_values)

        create_fixture(source, args.data_mib)

        def run_backup(force: str | None) -> None:
            cmd = [sys.executable, str(backup_script), "--config", str(test_cfg_path)]
            if force:
                cmd += ["--force", force]
            run_command(cmd)

        def reset_recovery() -> None:
            shutil.rmtree(recovery_work, ignore_errors=True)
            shutil.rmtree(restore_dest, ignore_errors=True)

        def run_verify(backup_id: str) -> None:
            reset_recovery()
            run_command([
                sys.executable, str(restore_script), "--config", str(recovery_cfg_path),
                "--backup-id", backup_id, "--verify",
            ])

        def run_restore_and_compare(backup_id: str) -> None:
            reset_recovery()
            run_command([
                sys.executable, str(restore_script), "--config", str(recovery_cfg_path),
                "--backup-id", backup_id, "--dest", str(restore_dest),
            ])
            compare_trees(source, restore_dest)
            reset_recovery()

        log("1/8 FULL A: creazione, almeno due slice, verify e restore")
        run_backup("full")
        state = asyncio.run(summaries_for_cfg(test_cfg))
        full_a = latest_of_kind(state, "FULL")
        if not full_a:
            raise RuntimeError("FULL A non trovato nel namespace self-test")
        assert_state(state, full_id=full_a.backup_id)
        manifest_a = asyncio.run(manifest_for_summary(test_cfg, full_a, root / "manifest-check"))
        if len(manifest_a.get("slices", [])) < 2:
            raise RuntimeError(
                f"FULL A ha solo {len(manifest_a.get('slices', []))} slice: il test richiede >=2"
            )
        run_verify(full_a.backup_id)
        run_restore_and_compare(full_a.backup_id)

        log("2/8 DIFF B: modifica + delete + add + rename, verify e restore")
        mutate_for_diff_b(source)
        run_backup("diff")
        state = asyncio.run(summaries_for_cfg(test_cfg))
        diff_b = latest_of_kind(state, "DIFF")
        if not diff_b:
            raise RuntimeError("DIFF B non trovato")
        assert_state(state, full_id=full_a.backup_id, diff_id=diff_b.backup_id)
        run_verify(diff_b.backup_id)
        run_restore_and_compare(diff_b.backup_id)

        log("3/8 Catalog recovery + DIFF C + rotazione DIFF B")
        local_catalog = backup_work / "catalogs" / f"{full_a.backup_id}.1.dar"
        if not local_catalog.exists():
            raise RuntimeError(f"Catalogo FULL locale atteso non trovato: {local_catalog}")
        local_catalog.unlink()
        mutate_for_diff_c(source)
        run_backup("diff")
        if not local_catalog.exists():
            raise RuntimeError("Il catalogo FULL non è stato recuperato da Telegram")
        state = asyncio.run(summaries_for_cfg(test_cfg))
        diff_c = latest_of_kind(state, "DIFF")
        if not diff_c or diff_c.backup_id == diff_b.backup_id:
            raise RuntimeError("DIFF C non trovato")
        assert_state(state, full_id=full_a.backup_id, diff_id=diff_c.backup_id)
        if any(s.backup_id == diff_b.backup_id for s in state):
            raise RuntimeError("DIFF B non è stato ruotato dopo DIFF C")
        run_verify(diff_c.backup_id)
        run_restore_and_compare(diff_c.backup_id)

        log("4/8 FULL D: nuova generazione e rotazione completa della vecchia")
        run_backup("full")
        state = asyncio.run(summaries_for_cfg(test_cfg))
        full_d = latest_of_kind(state, "FULL")
        if not full_d or full_d.backup_id == full_a.backup_id:
            raise RuntimeError("FULL D non trovato")
        assert_state(state, full_id=full_d.backup_id)
        if any(s.backup_id in {full_a.backup_id, diff_c.backup_id} for s in state):
            raise RuntimeError("La vecchia generazione non è stata ruotata dal FULL D")
        run_verify(full_d.backup_id)

        log("5/8 DIFF E sulla nuova generazione: verify e restore finale")
        mutate_for_diff_e(source)
        run_backup("diff")
        state = asyncio.run(summaries_for_cfg(test_cfg))
        diff_e = latest_of_kind(state, "DIFF")
        if not diff_e:
            raise RuntimeError("DIFF E non trovato")
        assert_state(state, full_id=full_d.backup_id, diff_id=diff_e.backup_id)
        run_verify(diff_e.backup_id)
        run_restore_and_compare(diff_e.backup_id)

        log("6/8 Modalità auto: con FULL recente deve creare un DIFF")
        run_backup(None)
        state = asyncio.run(summaries_for_cfg(test_cfg))
        auto_diff = latest_of_kind(state, "DIFF")
        if not auto_diff or auto_diff.backup_id == diff_e.backup_id:
            raise RuntimeError("La modalità auto non ha creato un nuovo DIFF")
        assert_state(state, full_id=full_d.backup_id, diff_id=auto_diff.backup_id)

        log("7/8 Controllo isolamento: i manifest reali devono essere invariati")
        after_real = asyncio.run(summaries_for_cfg(prod_cfg))
        after_real_ids = {s.backup_id for s in after_real}
        if after_real_ids != baseline_ids:
            raise RuntimeError(
                "I manifest del namespace reale sono cambiati durante il self-test: "
                f"prima={sorted(baseline_ids)}, dopo={sorted(after_real_ids)}"
            )

        success = True
        log("8/8 SELF-TEST SUPERATO")
        print(
            "Coperti: FULL multi-slice, verify, restore+rsync, DIFF add/modify/delete/rename, "
            "catalog recovery, rotazione DIFF, nuova generazione FULL, nuova base DIFF e auto mode.",
            flush=True,
        )
    finally:
        log(f"Cleanup namespace Telegram {namespace}")
        try:
            removed = asyncio.run(cleanup_namespace_messages(prod_cfg, namespace))
            print(f"Messaggi self-test rimossi: {removed}", flush=True)
        except Exception as exc:
            print(
                f"ATTENZIONE: cleanup Telegram fallito per {namespace}: {exc}\n"
                f"Riprova con: {sys.executable} {Path(__file__).resolve()} "
                f"--config {prod_cfg_path} --cleanup-namespace {namespace}",
                file=sys.stderr,
            )
        shutil.rmtree(root, ignore_errors=True)
        fcntl.flock(prod_lock, fcntl.LOCK_UN)
        prod_lock.close()

    if success:
        print("\nSelf-test completato e dati temporanei rimossi.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERRORE SELF-TEST: {exc}", file=sys.stderr)
        raise SystemExit(1)
