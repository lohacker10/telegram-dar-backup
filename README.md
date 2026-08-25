![Telegram DAR Backup](./assets/telegram-dar-backup-icon.png)

# 📦 Telegram DAR Backup

Automated, encrypted, off-site backups from a Debian/Linux NAS to a **private Telegram channel**.

The project combines **DAR (Disk ARchive)** and **Telethon** to create encrypted FULL and differential backups, split them into Telegram-friendly slices, upload each slice progressively, and restore the data without requiring temporary space equal to the whole backup.

> [!IMPORTANT]
> Telegram should be treated as an **additional off-site backup copy**, not as the only backup of important data. The archives are encrypted locally before upload, but Telegram is not a dedicated backup service with a storage SLA.

---

## ✨ Features

- 🔐 encrypted DAR archives before upload
- 🗜️ Zstandard compression by default
- 🧩 1900 MiB slices, below Telegram's standard 2 GB file limit
- ☁️ progressive upload and progressive restore
- ⚡ configurable parallel MTProto upload and download for large slices
- 📆 automatic FULL + differential policy
- 📉 every DIFF references the current FULL directly, not the previous DIFF
- ✅ SHA-512 recorded and checked for every slice
- 🧾 JSON manifests stored in Telegram
- ♻️ safe rotation only after the replacement backup is committed
- 💾 temporary disk usage around one slice during normal backup/restore
- ⏰ included systemd service and monthly timer
- 🧪 `--verify` for end-to-end archive validation without extraction
- 🧪 isolated automated FULL/DIFF/restore/rotation self-test in the **same Telegram channel**
- 🚑 recovery possible without the original NAS state

---

# 🧠 How it works

A FULL backup is created the first time the project runs.

```text
Source data
    │
    ▼
DAR + compression + encryption
    │
    ├── archive.0001.dar ── SHA-512 ── Telegram ── delete local slice
    ├── archive.0002.dar ── SHA-512 ── Telegram ── delete local slice
    ├── archive.0003.dar ── SHA-512 ── Telegram ── delete local slice
    └── ...
```

After a FULL, an encrypted isolated DAR catalogue is stored locally and on Telegram. Later DIFF backups use that FULL catalogue as their reference.

With `FULL_EVERY_MONTHS=6` the normal policy is:

```text
Month 1  FULL A
Month 2  FULL A + DIFF B
Month 3  FULL A + DIFF C
Month 4  FULL A + DIFF D
Month 5  FULL A + DIFF E
Month 6  FULL A + DIFF F
Month 7  new FULL G
```

A DIFF always references the FULL directly, so restoring month 5 needs only:

```text
FULL A
+
DIFF E
```

not all intermediate DIFFs.

---

## 🧾 Manifests and backup namespaces

Every completed backup publishes a JSON manifest containing:

- backup ID and timestamp
- FULL or DIFF type
- reference FULL ID
- logical backup namespace
- slice names, sizes and SHA-512 hashes
- Telegram message IDs
- FULL catalogue metadata when applicable

The DAR passphrase is **not** stored in the manifest.

`TDB_NAMESPACE` logically separates independent backup sets stored in the same Telegram channel:

```dotenv
TDB_NAMESPACE=default
```

Normal installations should leave it at `default`.

> [!NOTE]
> Backups created before namespace support did not contain `ns=` metadata. They are automatically treated as belonging to the `default` namespace, so existing backups remain compatible.

Rotation is restricted to the active namespace. This is also what allows the automated self-test to create and rotate FULL/DIFF generations in the production Telegram channel without touching real backup messages.

---

## 📁 Project structure

```text
telegram-dar-backup/
├── assets/
│   ├── telegram-dar-backup-logo.jpg
│   └── telegram-dar-backup-icon.png
├── backup.py                  # FULL/DIFF backup and rotation
├── restore.py                 # list, verify and restore
├── restore_fetch.py           # progressive DAR download hook
├── slice_upload.py            # progressive DAR upload hook
├── telegram_parallel.py       # parallel MTProto transfer helpers
├── telegram_login.py          # initial Telegram authentication
├── selftest.py                # isolated reusable end-to-end test
├── tdb_common.py              # shared helpers
├── config.env.example
├── requirements.txt
├── install.sh
├── uninstall.sh
└── systemd/
    ├── telegram-dar-backup.service
    └── telegram-dar-backup.timer
```

---

# 🚀 Installation

Requirements: Debian-based Linux/NAS, Internet access, a Telegram account with access to a private channel, and Telegram API credentials.

```bash
git clone <repository-url>
cd telegram-dar-backup
sudo ./install.sh
```

The installer installs DAR, rsync, Python/venv dependencies and the project under:

```text
/opt/telegram-dar-backup/
/etc/telegram-dar-backup/
/var/lib/telegram-dar-backup/
```

If no passphrase exists, it creates:

```text
/etc/telegram-dar-backup/dar.pass
```

> [!CAUTION]
> Keep a secure copy of the DAR passphrase **outside the NAS**. Without it, encrypted backups cannot be restored.

---

# 🗑️ Uninstall

The project includes a safe uninstaller. You can run either the repository copy:

```bash
sudo ./uninstall.sh
```

or, after installation, the installed copy:

```bash
sudo /opt/telegram-dar-backup/uninstall.sh
```

The normal uninstall:

- stops and disables `telegram-dar-backup.timer`;
- stops `telegram-dar-backup.service` if it is running;
- removes the installed systemd unit files;
- removes `/opt/telegram-dar-backup`;
- **preserves** `/etc/telegram-dar-backup`;
- **preserves** `/var/lib/telegram-dar-backup`;
- does **not** remove DAR, rsync, Python or other apt packages that may be shared;
- does **not** delete any backup messages from Telegram.

Preserving `/etc` and `/var/lib` keeps the local configuration, DAR passphrase, Telegram session and catalogue/state data available for a later reinstall or recovery.

## Purge local data

To also remove the standard local configuration, secrets and state directories:

```bash
sudo ./uninstall.sh --purge-local-data
```

The script requires typing:

```text
DELETE LOCAL DATA
```

before proceeding.

For intentional non-interactive use:

```bash
sudo ./uninstall.sh --purge-local-data --yes
```

> [!CAUTION]
> `--purge-local-data` removes `/etc/telegram-dar-backup` and `/var/lib/telegram-dar-backup`, including `dar.pass`, `config.env`, the Telegram session and local catalogues. Telegram backup messages are still left untouched. If you do not have the DAR passphrase stored safely elsewhere, deleting it can make the encrypted Telegram backups impossible to restore.

Custom files or state configured outside those standard directories are not removed automatically.

---

# 🔄 Updating an existing installation

The Git clone and `/opt/telegram-dar-backup` are separate. Pulling commits does not update the installed scripts until `install.sh` is run again.

Check that a backup is not running:

```bash
systemctl is-active telegram-dar-backup.service
```

Then:

```bash
cd /path/to/telegram-dar-backup
git fetch origin
git switch main
git pull --ff-only
sudo ./install.sh
```

The installer intentionally preserves:

```text
/etc/telegram-dar-backup/config.env
/etc/telegram-dar-backup/dar.pass
/var/lib/telegram-dar-backup/telegram.session
/var/lib/telegram-dar-backup/catalogs/
```

Existing `config.env` files are not overwritten. Compare them with the current example after an update:

```bash
diff -u /etc/telegram-dar-backup/config.env ./config.env.example || true
```

The output may contain secrets; inspect it only in a trusted terminal.

## Testing a feature branch

```bash
cd /path/to/telegram-dar-backup
git fetch origin
git switch <branch-name>
git pull --ff-only origin <branch-name>
sudo ./install.sh
```

Return to main with:

```bash
git switch main
git pull --ff-only
sudo ./install.sh
```

The existing Telegram session and DAR password are preserved.

---

# 📡 Telegram setup

Create a dedicated private Telegram channel and obtain `api_id` and `api_hash` from `my.telegram.org`.

Initial login:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/telegram_login.py \
  --config /etc/telegram-dar-backup/config.env
```

The script authenticates Telethon, saves the session and lists visible channel IDs. Put the selected private-channel ID in:

```dotenv
TELEGRAM_CHANNEL_ID=-1001234567890
```

Protect the session:

```bash
sudo chmod 600 /var/lib/telegram-dar-backup/telegram.session
```

The session file is an authentication credential.

---

# ⚙️ Configuration

Edit:

```bash
sudo nano /etc/telegram-dar-backup/config.env
```

Typical configuration:

```dotenv
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
TELEGRAM_CHANNEL_ID=-1001234567890
TELEGRAM_SESSION_FILE=/var/lib/telegram-dar-backup/telegram.session
TELEGRAM_UPLOAD_WORKERS=4
TELEGRAM_DOWNLOAD_WORKERS=4
TDB_NAMESPACE=default

SOURCE=/mnt/data
SOURCE_LABEL=NAS-DATA
REQUIRE_SOURCE_MOUNT=true

WORK_DIR=/var/lib/telegram-dar-backup
SLICE_SIZE=1900M
COMPRESSION=zstd:6
FULL_EVERY_MONTHS=6
KEEP_ONLY_LATEST_DIFF=true

DAR_PASSPHRASE_FILE=/etc/telegram-dar-backup/dar.pass
```

| Variable | Purpose |
| --- | --- |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | Telegram API credentials |
| `TELEGRAM_CHANNEL_ID` | private channel used for backup storage |
| `TELEGRAM_SESSION_FILE` | persistent authenticated Telethon session |
| `TELEGRAM_UPLOAD_WORKERS` | parallel upload requests; default `4` |
| `TELEGRAM_DOWNLOAD_WORKERS` | independent MTProto download connections; default `4` |
| `TDB_NAMESPACE` | logical backup set inside the channel; normally `default` |
| `SOURCE` | source directory/filesystem |
| `SOURCE_LABEL` | descriptive label stored in manifests |
| `REQUIRE_SOURCE_MOUNT` | refuse backup if `SOURCE` is expected to be a mount but is not mounted |
| `WORK_DIR` | local runtime state and FULL catalogue cache |
| `SLICE_SIZE` | DAR slice size; default `1900M` |
| `COMPRESSION` | DAR compression; default `zstd:6` |
| `FULL_EVERY_MONTHS` | age at which auto mode starts a new FULL |
| `KEEP_ONLY_LATEST_DIFF` | keep only the newest DIFF for the current FULL |
| `DAR_PASSPHRASE_FILE` | root-only DAR encryption passphrase file |

## Source mount protection

For an actual mounted data filesystem:

```dotenv
SOURCE=/mnt/data
REQUIRE_SOURCE_MOUNT=true
```

For a normal directory/subdirectory:

```dotenv
SOURCE=/mnt/data/documents
REQUIRE_SOURCE_MOUNT=false
```

This prevents accidentally backing up an empty mount directory when the real disk is missing.

---

# 💾 Creating backups

Automatic decision (the mode used by systemd):

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/backup.py \
  --config /etc/telegram-dar-backup/config.env
```

Behavior:

```text
no FULL exists                    -> FULL
latest FULL age < N months        -> DIFF
latest FULL age >= N months       -> new FULL
```

Force a FULL:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/backup.py \
  --config /etc/telegram-dar-backup/config.env \
  --force full
```

Force a DIFF:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/backup.py \
  --config /etc/telegram-dar-backup/config.env \
  --force diff
```

A DIFF requires a valid FULL in the same namespace.

---

# ☁️ Progressive and parallel transfers

Defaults:

```dotenv
SLICE_SIZE=1900M
TELEGRAM_UPLOAD_WORKERS=4
TELEGRAM_DOWNLOAD_WORKERS=4
```

Upload workers keep multiple 512 KiB MTProto file-part requests in flight. Download workers use independent MTProto connections and interleaved 512 KiB offsets inside the **same** DAR slice.

Only one DAR slice is kept locally at a time, so download worker count does not multiply normal restore temporary-space requirements.

Set either worker value to `1` to use the normal Telethon path for comparison/compatibility.

Higher worker counts do not guarantee higher throughput; routing, the Telegram DC, account/server limits and the Internet connection may become the bottleneck.

Every downloaded slice is checked for expected size and SHA-512 before DAR consumes it.

---

# ♻️ Rotation and failure safety

A previous valid backup is not deleted until the replacement has completed and its `COMPLETE` manifest has been published.

```text
existing valid backup
        ↓
create replacement
        ↓
upload slices/catalogue
        ↓
publish COMPLETE manifest
        ↓
replacement committed
        ↓
rotate older backups in SAME namespace
```

If creation fails before commit, the previous generation remains intact. If cleanup fails after commit, both generations may temporarily remain, which is safer than deleting the valid copy too early.

With `KEEP_ONLY_LATEST_DIFF=true`, the previous DIFF for the same FULL is removed only after the new DIFF is valid.

---

# 🔎 Listing backups

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/restore.py \
  --config /etc/telegram-dar-backup/config.env \
  --list
```

Only manifests in the configured `TDB_NAMESPACE` are listed.

---

# ✅ What `--verify` guarantees

Verify a specific backup:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/restore.py \
  --config /etc/telegram-dar-backup/config.env \
  --backup-id BACKUP_ID \
  --verify
```

For each required archive it:

1. downloads the real slices from Telegram;
2. checks expected file size;
3. recalculates SHA-512 and compares it with the manifest;
4. decrypts/reads the archive using the configured passphrase;
5. runs DAR archive test (`dar -t`) without extracting files.

If the selected backup is a DIFF, the reference FULL is verified too.

A successful verify therefore demonstrates that the Telegram copy is present, byte-integral relative to the recorded hashes, decryptable and readable by DAR.

It does **not** replace a real restore test: extraction semantics and final filesystem state are tested by restoring and comparing the result.

---

# 🚑 Restoring

Restore a specific backup into an empty destination:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/restore.py \
  --config /etc/telegram-dar-backup/config.env \
  --backup-id BACKUP_ID \
  --dest /mnt/restore
```

For a DIFF the script automatically applies:

```text
reference FULL
      ↓
selected DIFF
      ↓
final filesystem state
```

The restore is progressive: DAR asks for slices one at a time and `restore_fetch.py` downloads/verifies them as needed.

The destination must have enough space for the recovered data.

---

# 🧪 Automated isolated end-to-end self-test

`selftest.py` is intended to be run after installation and again after meaningful code changes.

It uses the **same Telegram channel, authenticated session and DAR passphrase** as the real installation, but it does not use the real source data and does not share the real backup state.

The tester creates a unique namespace such as:

```text
selftest-a1b2c3d4e5f6
```

All self-test slices, catalogues, manifests and rotations are tagged with that namespace. The production namespace (`default` unless changed explicitly) is therefore invisible to self-test rotation.

The tester also takes the real `backup.lock` for its duration, so the systemd backup cannot start concurrently.

## What it creates

By default it creates approximately **2000 MiB of incompressible random data** plus small files, directories, a hardlink, a symlink and metadata fixtures.

It intentionally keeps:

```dotenv
SLICE_SIZE=1900M
```

so the first FULL must contain **at least two real DAR slices**.

Use a filesystem with roughly **7-8 GiB free** for the default test. `/var/tmp` is used by default; choose another filesystem with `--temp-root` if needed.

## What it tests

The complete sequence is automated:

```text
FULL A
  -> assert >=2 slices
  -> --verify
  -> real restore into empty WORK_DIR
  -> rsync dry-run comparison

modify + delete + add + rename
DIFF B -> FULL A
  -> --verify
  -> restore FULL A + DIFF B
  -> rsync comparison

delete local FULL catalogue
modify data
DIFF C -> FULL A
  -> catalogue must be recovered from Telegram
  -> previous DIFF B must rotate
  -> --verify
  -> restore and compare

FULL D
  -> new generation
  -> old FULL A/DIFF C generation must rotate
  -> --verify

modify/delete/add again
DIFF E -> FULL D
  -> --verify
  -> final restore and compare

auto mode
  -> recent FULL D must cause a new DIFF

final safety check
  -> real namespace manifest IDs must be exactly unchanged
```

Restore/verify operations use a separate, repeatedly emptied recovery `WORK_DIR`, so they cannot rely on the self-test backup catalogue cache or previous restore state. They must reconstruct what they need from Telegram.

The filesystem comparison is:

```bash
rsync -aHAXnci --delete SOURCE/ RESTORED/
```

The `-n` means **dry-run**: it does not delete or change the source or restore. `--delete` is useful here only to detect extra files that should not exist in the restored tree.

## Run it

Because the test performs several real Telegram transfers, running it inside `tmux` is recommended:

```bash
tmux new -s tdb-selftest
```

Then:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/selftest.py \
  --config /etc/telegram-dar-backup/config.env
```

To place temporary data on another filesystem:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/selftest.py \
  --config /etc/telegram-dar-backup/config.env \
  --temp-root /mnt/fast-disk/selftest
```

The normal test cleans both its local temporary directory and its Telegram `selftest-*` messages even when a test stage raises an ordinary Python error.

> [!IMPORTANT]
> No program can guarantee cleanup after `SIGKILL`, power loss or another abrupt process termination. If that happens, the orphan self-test messages are still isolated from real backups. The output prints the namespace; clean it later with:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/selftest.py \
  --config /etc/telegram-dar-backup/config.env \
  --cleanup-namespace selftest-XXXXXXXXXXXX
```

For safety, the cleanup command refuses namespaces that do not begin with `selftest-`.

## What this self-test does not prove

It strongly tests the real Telegram/DAR backup path on the current machine, including stateless local restore state, but it still reuses the existing authenticated Telegram session.

For the strongest disaster-recovery validation, perform at least one additional restore on a clean Debian machine/VM by authenticating Telegram again and supplying the independently stored DAR passphrase.

---

# ⏰ Automatic monthly backups

Enable the included timer:

```bash
sudo systemctl enable --now telegram-dar-backup.timer
```

Inspect it:

```bash
systemctl list-timers telegram-dar-backup.timer
```

Logs:

```bash
sudo journalctl -u telegram-dar-backup.service
```

---

# 🆘 Disaster recovery after losing the NAS

The restore path does not depend on the original `WORK_DIR`.

On a replacement Debian machine you need:

1. access to the Telegram account and backup channel;
2. Telegram API ID and API Hash;
3. the DAR encryption passphrase;
4. this project;
5. storage for the restored data.

Install the project, configure the Telegram credentials, authenticate again with `telegram_login.py`, set the same DAR passphrase and run `restore.py --list` / `restore.py --verify` / restore.

You do **not** need the old NAS's local catalogue cache, journals or old `telegram.session`; a new authorized session can be created.

Keep independently:

```text
DAR passphrase
Telegram account recovery access
Telegram API credentials
```

---

# 🔐 Encryption password changes

Do not change the DAR passphrase in the middle of a FULL/DIFF generation.

Safe procedure:

1. change the passphrase file;
2. force a new FULL;
3. verify/restore the new FULL;
4. only then retire the old passphrase/generation.

---

# 🗜️ Compression

Default:

```dotenv
COMPRESSION=zstd:6
```

Already-compressed media (JPEG, MP4, ZIP, etc.) usually gains little from stronger compression. `zstd:3` can reduce CPU use.

---

# 🗃️ Files changing during backup

DAR can detect/retry some files that change while being read, but that is not equivalent to a filesystem snapshot.

For databases, VM images or frequently changing application state, prefer a filesystem/LVM snapshot or quiesce the application first.

---

# 🧬 Metadata preservation

The systemd service runs as root so DAR can preserve, where supported:

- ownership and Unix permissions
- hard links and symbolic links
- ACLs
- extended attributes
- sparse files

For best results restore to a Linux filesystem supporting the same metadata.

---

# 🛡️ Security notes

DAR slices are encrypted before upload. Protect:

```text
/etc/telegram-dar-backup/config.env
/etc/telegram-dar-backup/dar.pass
/var/lib/telegram-dar-backup/telegram.session
```

Recommended permissions:

```bash
sudo chmod 600 /etc/telegram-dar-backup/config.env
sudo chmod 600 /etc/telegram-dar-backup/dar.pass
sudo chmod 600 /var/lib/telegram-dar-backup/telegram.session
```

---

# 🛠️ Troubleshooting

## `SOURCE` is not a mount point

If `SOURCE` is intentionally a subdirectory, use:

```dotenv
REQUIRE_SOURCE_MOUNT=false
```

Otherwise verify the expected disk is mounted before running a backup.

## Telegram session is not authenticated

Run `telegram_login.py` again.

## Wrong DAR password

Check `DAR_PASSPHRASE_FILE`. An incorrect passphrase prevents DAR from reading encrypted catalogues/archives.

## Telegram transfers become very slow

Upload and download throughput can vary substantially during long-running transfers. Slices may transfer quickly for a while, slow down significantly, and later recover without any local configuration change.

Possible bottlenecks include the Internet connection and routing, the selected Telegram data center, and Telegram account/server-side limits. The project cannot guarantee stable throughput, so a short speed sample is not a reliable estimate of the total backup or restore duration.

A slowdown by itself does **not** indicate archive corruption. Slice size and SHA-512 metadata are recorded by the backup, and downloaded slices are checked against that metadata before DAR consumes them. If the process is still making progress and no error is reported, it is generally better to let it continue rather than interrupt a valid backup or restore only because the transfer rate has changed.

## Internet or power failure during backup

Before the new `COMPLETE` manifest exists, the previous valid generation remains intact. Interrupted attempts can leave orphan encrypted Telegram messages, but they are not considered valid backups without a COMPLETE manifest.

---

# ✅ Setup checklist

- [ ] create a private Telegram backup channel
- [ ] obtain Telegram API ID and API Hash
- [ ] run `install.sh`
- [ ] configure `config.env`
- [ ] store the DAR passphrase outside the NAS
- [ ] run `telegram_login.py`
- [ ] set the correct channel ID
- [ ] run the isolated `selftest.py`
- [ ] run `restore.py --list`
- [ ] periodically run `restore.py --verify`
- [ ] occasionally perform a real restore
- [ ] perform at least one clean-machine disaster-recovery test for important data
- [ ] enable the monthly systemd timer

---

# 📚 References

- [DAR project](https://dar.linux.free.fr/)
- [DAR Debian manual](https://manpages.debian.org/testing/dar/dar.1.en.html)
- [Telethon documentation](https://docs.telethon.dev/)
- [Telegram file API](https://core.telegram.org/api/files)
- [Telegram API credentials](https://my.telegram.org)

---

# 🚦 Transfer speed and Telegram limits

Telegram is not a dedicated high-throughput backup transport. Even with a fast Internet connection, large uploads and downloads may run well below the available line rate, and throughput can vary substantially during the same backup or restore.

Telegram's MTProto file API can apply server-side rate limits to very large transfers. The official file API documentation describes `FLOOD_PREMIUM_WAIT_X` upload/download throttling after large transfer volumes; routing, the selected Telegram data center, account/server limits and normal Internet conditions can also become bottlenecks.

`TELEGRAM_UPLOAD_WORKERS` and `TELEGRAM_DOWNLOAD_WORKERS` use parallel requests/connections to improve throughput where possible, but they cannot guarantee full utilization of the local Internet connection or bypass Telegram-side limits. Increasing worker counts beyond the useful point may provide no additional speed.

Temporary slowdowns — including sharp changes from one slice to the next — do not by themselves indicate archive corruption. Each slice is recorded with its expected size and SHA-512, and downloaded slices are verified before DAR consumes them. If a backup is still progressing without errors, a temporary speed drop is normally a reason to let it continue rather than interrupt it.

This project is therefore intended to use Telegram as the **off-site "1" in a 3-2-1 backup strategy**, not as the fastest recovery tier. Keep normal local/on-site copies for routine restores; use the encrypted Telegram copy as an additional geographically separate recovery option.

---

# 💡 Backup strategy

For important data, use this project as part of a broader **3-2-1 strategy**:

- 3 copies of the data;
- on at least 2 different storage types/systems;
- with at least 1 off-site copy.

Telegram DAR Backup can provide the encrypted off-site copy; it should not be the only backup.