# Telegram DAR Backup

Automated encrypted backups of a data directory or disk to a **private Telegram channel**, using:

- **DAR (Disk ARchive)** for FULL and differential backups, compression, encryption, and archive slicing;
- **1900 MiB slices** (`1900M`), kept below Telegram's standard per-file upload limit;
- **Telethon** as a headless Telegram client, so no graphical interface is required;
- progressive upload: as soon as DAR closes a slice, the script computes its **SHA-512**, uploads it to Telegram, and removes the local copy;
- one **FULL backup every 6 months**, with a **DIFF against the latest FULL** in the other months;
- safe rotation: the previous backup is deleted only after the new backup has published a `COMPLETE` manifest;
- progressive restore: slices are downloaded from Telegram one at a time and fed to DAR in sequential-read mode.

> **Important:** Telegram should be treated as an additional off-site copy, not as your only backup. Telegram is not a backup service with a storage SLA, and normal Telegram cloud messages are not end-to-end encrypted. This project therefore encrypts the DAR archives **before** uploading them.

---

## 1. How it works

### First backup

The first run creates a FULL backup:

```text
/mnt/data
   │
   ▼
DAR + zstd + AES
   │
   ├── archive.0001.dar  ── SHA-512 ── upload ── delete locally
   ├── archive.0002.dar  ── SHA-512 ── upload ── delete locally
   ├── archive.0003.dar  ── SHA-512 ── upload ── delete locally
   └── ...
```

The final slice is kept locally for a short time because DAR needs it to create an **isolated catalogue**. The catalogue is encrypted, uploaded to Telegram, and also kept locally. It is used as the reference catalogue for later differential backups.

At the end, a JSON file similar to the following is uploaded:

```text
manifest-20260822T153000Z_FULL_ab12cd.json
```

The manifest contains:

- backup ID;
- creation date;
- FULL/DIFF type;
- reference FULL backup;
- slice list;
- size of every slice;
- SHA-512 of every slice;
- Telegram message ID containing each slice;
- information about the FULL catalogue.

The manifest **does not contain the DAR password** and does not contain the list of backed-up files.

### Following months

With:

```dotenv
FULL_EVERY_MONTHS=6
```

the normal lifecycle is:

```text
Month 1  FULL A
Month 2  FULL A + DIFF B
Month 3  FULL A + DIFF C
Month 4  FULL A + DIFF D
Month 5  FULL A + DIFF E
Month 6  FULL A + DIFF F
Month 7  new FULL G
```

Every DIFF is calculated against the current FULL, not against the previous DIFF.

Therefore, restoring the state from month 5 requires only:

```text
FULL A
+
DIFF E
```

With:

```dotenv
KEEP_ONLY_LATEST_DIFF=true
```

the previous DIFF is deleted only after the new DIFF has completed successfully. Normally the channel therefore keeps:

```text
latest FULL
+
latest DIFF
```

When a new six-month FULL is created, the previous FULL and its latest DIFF are deleted **only after the new FULL has completed successfully**.

---

## 2. Requirements

A Debian-based NAS with:

- Python 3;
- `python3-venv`;
- DAR;
- Internet access;
- a dedicated Telegram account, or any Telegram account with access to the private backup channel;
- a Telegram API ID and API Hash obtained from `https://my.telegram.org`.

No GUI is required. The initial Telethon login works entirely over SSH or a terminal.

---

## 3. Project files

```text
telegram-dar-backup/
├── backup.py                  FULL/DIFF backup and rotation
├── restore.py                 list, verify, and restore backups
├── restore_fetch.py           DAR hook for progressive downloading
├── slice_upload.py            DAR hook for progressive uploading
├── telegram_login.py          initial Telegram login and channel listing
├── tdb_common.py              shared functions
├── config.env.example         example configuration
├── requirements.txt
├── install.sh
├── .gitignore
└── systemd/
    ├── telegram-dar-backup.service
    └── telegram-dar-backup.timer
```

---

## 4. Is this repository safe to publish publicly?

The repository is designed so that the tracked source files contain **no real credentials**.

The values in `config.env.example`, such as:

```dotenv
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
TELEGRAM_CHANNEL_ID=-1001234567890
```

are placeholders only.

### Never commit these files

The following files are secrets and must stay private:

```text
/etc/telegram-dar-backup/config.env
/etc/telegram-dar-backup/dar.pass
/var/lib/telegram-dar-backup/telegram.session
```

In particular:

- `config.env` may contain your real Telegram API ID, API Hash, channel ID, filesystem paths, and possibly a DAR passphrase;
- `dar.pass` contains the encryption password;
- `telegram.session` is an authenticated Telethon session and should be treated like an account credential.

The included `.gitignore` blocks common local copies of these files, but **do not rely on `.gitignore` as your only security control**. Always check what Git is about to commit:

```bash
git status
```

and, before pushing:

```bash
git diff --cached
```

A useful additional check is:

```bash
git ls-files
```

Only source code, documentation, example configuration, and systemd units should normally be tracked.

### If a secret was ever committed

Deleting it in a later commit is **not enough**, because it remains in Git history.

If you accidentally commit any of the following:

- DAR password;
- Telegram `.session` file;
- Telegram API Hash;
- other account credentials;

assume the secret is exposed, rotate/revoke it where possible, and remove it from the repository history before continuing to use the repository publicly.

---

## 5. Automatic installation

From the project directory:

```bash
sudo ./install.sh
```

The installer creates or installs:

```text
/opt/telegram-dar-backup/
/etc/telegram-dar-backup/config.env
/etc/telegram-dar-backup/dar.pass
/var/lib/telegram-dar-backup/
/etc/systemd/system/telegram-dar-backup.service
/etc/systemd/system/telegram-dar-backup.timer
```

If `/etc/telegram-dar-backup/dar.pass` does not already exist, the installer creates it with a **cryptographically random passphrase** and mode `0600`. Save that passphrase somewhere outside the NAS before relying on the backup. You may replace it with your own long passphrase before the first backup.

It also creates a Python virtual environment in:

```text
/opt/telegram-dar-backup/venv/
```

`cryptg` is attempted as an optional Telethon accelerator. If it cannot be installed, the system still works, but MTProto encryption/upload/download may be slower.

---

## 6. Create the private Telegram channel

Recommended setup:

1. create a dedicated **private Telegram channel** for backups;
2. add the Telegram account used by the NAS;
3. if the NAS uses a separate account, give it enough permissions to publish and delete its own backup messages;
4. do not use a Bot Token: this project uses a real Telegram MTProto client through Telethon.

The NAS may use your personal Telegram account, but a dedicated account keeps the backup system separated and easier to manage.

---

## 7. Telegram API ID and API Hash

Open:

```text
https://my.telegram.org
```

Create a Telegram application and obtain:

```text
api_id
api_hash
```

Then edit:

```bash
sudo nano /etc/telegram-dar-backup/config.env
```

Example:

```dotenv
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
TELEGRAM_CHANNEL_ID=-1001234567890
TELEGRAM_SESSION_FILE=/var/lib/telegram-dar-backup/telegram.session

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

Protect the configuration file:

```bash
sudo chown root:root /etc/telegram-dar-backup/config.env
sudo chmod 600 /etc/telegram-dar-backup/config.env
```

### `REQUIRE_SOURCE_MOUNT`

For a disk mounted directly at `/mnt/data`, the recommended setting is:

```dotenv
REQUIRE_SOURCE_MOUNT=true
```

The backup script refuses to run if `/mnt/data` exists but is not an actual mount point. This prevents a dangerous situation where the disk is not mounted and the script backs up an empty mount directory instead.

If `SOURCE` intentionally points to a subdirectory, for example:

```text
/mnt/storage/documents
```

use:

```dotenv
REQUIRE_SOURCE_MOUNT=false
```

---

## 8. DAR password

### Recommended method

The installer creates a random passphrase automatically on first installation. You can inspect it to store a secure external copy, or replace it with your own long passphrase:

```bash
sudo nano /etc/telegram-dar-backup/dar.pass
```

The file must contain **only the password**, on a single line.

Then protect it:

```bash
sudo chown root:root /etc/telegram-dar-backup/dar.pass
sudo chmod 600 /etc/telegram-dar-backup/dar.pass
```

In `config.env`:

```dotenv
DAR_PASSPHRASE_FILE=/etc/telegram-dar-backup/dar.pass
```

The scripts generate a temporary DAR command file (DCF) with mode `0600`. The password is therefore not passed directly as a visible `-K password` command-line argument.

### Alternative: password in `config.env`

This is also supported:

```dotenv
# DAR_PASSPHRASE_FILE=
DAR_PASSPHRASE=a-long-random-password
```

If you use this option, **`config.env` must remain mode `0600` and must never be committed to Git**.

### Avoid putting the password in systemd `ExecStart`

Do not use something such as:

```text
backup.py --password MyPassword
```

Command-line arguments can be exposed through process listings, `/proc`, logging, debugging tools, or shell history.

### Do not lose the password

If the DAR password is lost, the encrypted backup cannot be restored.

Keep at least one copy outside the NAS, preferably in a password manager or another secure offline location.

---

## 9. First headless Telegram login

After setting `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`, run:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/telegram_login.py \
  --config /etc/telegram-dar-backup/config.env
```

You will be prompted for something similar to:

```text
Telegram phone number: +39...
Telegram code: 12345
2FA password: ********
```

The login code and optional 2FA password are needed only when creating/authenticating the Telethon session.

Telethon stores the authenticated session at:

```text
/var/lib/telegram-dar-backup/telegram.session
```

At the end, the script lists the channels visible to the account, for example:

```text
-1001234567890  NAS Backup
```

Copy the correct channel ID into:

```dotenv
TELEGRAM_CHANNEL_ID=-1001234567890
```

Then make sure the session is protected:

```bash
sudo chmod 600 /var/lib/telegram-dar-backup/telegram.session
```

**The Telegram session file is a sensitive credential.** Anyone able to copy and use that session may potentially act through that authenticated Telegram session.

---

## 10. First manual backup

Before enabling the timer, run a manual backup:

```bash
sudo systemctl start telegram-dar-backup.service
```

Follow the log with:

```bash
sudo journalctl -fu telegram-dar-backup.service
```

Or run the script directly:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/backup.py \
  --config /etc/telegram-dar-backup/config.env
```

The first backup is automatically a FULL backup.

### Force a FULL backup

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/backup.py \
  --config /etc/telegram-dar-backup/config.env \
  --force full
```

### Force a DIFF backup

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/backup.py \
  --config /etc/telegram-dar-backup/config.env \
  --force diff
```

A DIFF requires an existing valid FULL backup.

---

## 11. Progressive upload

DAR is configured to create slices of:

```text
1900M
```

The files are named:

```text
archive.0001.dar
archive.0002.dar
archive.0003.dar
...
```

DAR runs `slice_upload.py` whenever a slice is closed.

For each slice:

```text
DAR closes slice
      ↓
SHA-512
      ↓
Telethon upload
      ↓
remote size check
      ↓
Telegram message_id saved to local journal
      ↓
local slice deleted
```

DAR waits while the current slice is being uploaded. This prevents hundreds of gigabytes from accumulating in `WORK_DIR`.

The final slice is temporarily kept on the NAS until the FULL catalogue has been isolated, then it is removed as well.

---

## 12. Temporary disk-space requirements

With:

```dotenv
SLICE_SIZE=1900M
```

only a few gigabytes of temporary space are normally needed.

Approximately:

```text
~2 GB for the current slice
+ DAR catalogue
+ small state/journal files
```

The DAR catalogue may grow when backing up millions of files, but it does not contain the file data themselves.

You therefore do **not** need 1 TB of free temporary space on the NAS to create or restore a 1 TB backup.

---

## 13. What happens if a backup fails?

The previous valid backup is **not deleted before the new one has committed successfully**.

The workflow is:

```text
previous valid backup
        ↓
create new backup
        ↓
upload slices
        ↓
upload catalogue if FULL
        ↓
upload COMPLETE manifest
        ↓
NEW BACKUP COMMITTED
        ↓
rotate previous backup
```

If an error occurs before the `COMPLETE` manifest is published:

- DAR exits with an error;
- the previous backup stays intact;
- the script attempts to delete the incomplete new backup slices from Telegram.

If the new manifest is already `COMPLETE` and only the rotation step fails, **the new backup is kept**. Both old and new backups may remain temporarily, which is the safer failure mode.

---

## 14. Monthly systemd timer

The included timer runs on the first day of each month at 03:15, with up to 20 minutes of randomized delay:

```ini
OnCalendar=*-*-01 03:15:00
Persistent=true
RandomizedDelaySec=20m
```

To change the schedule:

```bash
sudo systemctl edit --full telegram-dar-backup.timer
```

Then reload systemd:

```bash
sudo systemctl daemon-reload
```

Enable the timer:

```bash
sudo systemctl enable --now telegram-dar-backup.timer
```

Check the next run:

```bash
systemctl list-timers telegram-dar-backup.timer
```

View service logs:

```bash
sudo journalctl -u telegram-dar-backup.service
```

---

## 15. List available backups

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/restore.py \
  --config /etc/telegram-dar-backup/config.env \
  --list
```

Example:

```text
2026-08-22T15:30:00Z    FULL    20260822T153000Z_FULL_ab12cd    base=20260822T153000Z_FULL_ab12cd
2026-09-01T03:20:00Z    DIFF    20260901T032000Z_DIFF_ef34aa    base=20260822T153000Z_FULL_ab12cd
```

---

## 16. Verify a backup without restoring it

`restore.py --verify` progressively downloads the slices, checks SHA-512, and asks DAR to test the archive without extracting its contents.

Interactive mode:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/restore.py \
  --config /etc/telegram-dar-backup/config.env \
  --verify
```

Verify a specific backup:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/restore.py \
  --config /etc/telegram-dar-backup/config.env \
  --backup-id 20260901T032000Z_DIFF_ef34aa \
  --verify
```

If you select a DIFF, both its reference FULL and the selected DIFF are verified.

This is the recommended way to periodically verify that the cloud copy is genuinely downloadable, decryptable, and readable by DAR.

---

## 17. Full restore

Prepare an empty destination directory, for example a replacement HDD mounted at:

```text
/mnt/restore
```

Then run:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/restore.py \
  --config /etc/telegram-dar-backup/config.env \
  --dest /mnt/restore
```

If `--backup-id` is not specified, an interactive menu is displayed:

```text
Available backups:

  [1] 2026-09-01T03:20:00Z DIFF  20260901T032000Z_DIFF_ef34aa
  [2] 2026-08-22T15:30:00Z FULL  20260822T153000Z_FULL_ab12cd

Backup number to restore:
```

If a DIFF is selected, the script automatically finds its reference FULL and performs:

```text
FULL
 ↓
restore
 ↓
DIFF
 ↓
restore/update/remove files recorded as deleted
 ↓
final filesystem state
```

### Restore a specific backup

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/restore.py \
  --config /etc/telegram-dar-backup/config.env \
  --backup-id 20260901T032000Z_DIFF_ef34aa \
  --dest /mnt/restore
```

For safety, the destination must be empty by default.

`--allow-nonempty` is available, but should be used only when you deliberately want to restore into a non-empty destination:

```bash
... restore.py --backup-id ... --dest /mnt/restore --allow-nonempty
```

---

## 18. Progressive restore: no 1 TB temporary disk required

During restore, DAR is run with:

```text
--sequential-read
```

When DAR requests the next slice:

```text
restore_fetch.py
      ↓
download slice using Telegram message_id
      ↓
check size
      ↓
check SHA-512
      ↓
DAR reads the slice
      ↓
previous temporary slice is removed when the next one is needed
```

As a result, temporary space remains roughly on the order of one slice rather than the full backup size.

The destination disk must of course have enough free space for the restored data.

---

## 19. Restore after complete NAS loss

The restore process is designed not to depend on the original NAS state.

You need:

1. access to the Telegram account that can read the private backup channel;
2. the Telegram API ID and API Hash;
3. the DAR password;
4. a copy of this project;
5. a new Debian machine and replacement storage.

Install the project on the new machine, configure the Telegram API credentials, and authenticate again with:

```bash
telegram_login.py
```

The Telegram account can then read the channel history and `restore.py` can discover the uploaded manifests.

For disaster recovery, therefore keep the following **outside the NAS**:

```text
DAR password
Telegram API ID/API Hash, or a way to recover them
access to the Telegram account
copy or URL of this project
```

A public GitHub repository can satisfy the final item, but never put the first three secrets in that repository.

---

## 20. Changing the DAR password

DIFF backups must be able to read the catalogue belonging to their reference FULL.

For that reason, do not change the password between a FULL and the DIFF backups based on it.

To change the password safely:

1. update `/etc/telegram-dar-backup/dar.pass`;
2. force a new FULL backup:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/backup.py \
  --config /etc/telegram-dar-backup/config.env \
  --force full
```

3. wait for the new FULL to complete and verify successfully;
4. only then discard the old password and old backup generation.

Do not lose the old password before the new FULL has been completed and verified.

---

## 21. Compression

Default:

```dotenv
COMPRESSION=zstd:6
```

This is a reasonable compromise between CPU usage and compression ratio.

If the source mostly contains already-compressed data such as JPEG, MP4, MKV, ZIP, RAR, or 7z files, additional compression may be minimal. You may choose a lower level such as:

```dotenv
COMPRESSION=zstd:3
```

For highly compressible data, a higher level may reduce storage usage at the cost of more CPU time.

Before changing the compression algorithm or strategy, forcing a new FULL backup is recommended.

---

## 22. Files changing during backup

DAR can detect and retry files that change while they are being read, but this is **not a replacement for a filesystem snapshot**.

For ordinary documents, photos, and static archives this is usually acceptable.

For continuously-changing data such as:

- databases;
- running VM images;
- application files that are constantly modified;

prefer backing up an LVM/ZFS/Btrfs snapshot, or stop/quiesce the application before starting the backup.

---

## 23. Permissions and metadata

The systemd service runs as `root` so DAR can read and restore as much filesystem metadata as possible, including:

- owner/group;
- Unix permissions;
- hard links;
- symbolic links;
- extended attributes and ACLs when supported;
- sparse files.

For best Linux metadata preservation, restore to a filesystem that supports them, such as ext4, XFS, or Btrfs.

---

## 24. Security model

### Data stored on Telegram

The DAR slices are encrypted with DAR-managed symmetric AES encryption before upload.

Telegram therefore receives encrypted DAR files plus some non-secret operational metadata in captions/manifests, such as:

```text
backup ID
FULL/DIFF type
date
slice number
hash
message_id
```

The internal file listing stored in the FULL catalogue is encrypted as well.

### Sensitive files on the NAS

At minimum, protect:

```bash
chmod 600 /etc/telegram-dar-backup/config.env
chmod 600 /etc/telegram-dar-backup/dar.pass
chmod 600 /var/lib/telegram-dar-backup/telegram.session
```

The Telegram session and DAR password are both high-value credentials.

### Password in the timer

This project **does not put the DAR password in the systemd command line**.

An environment variable is technically possible, but a root-owned `0600` password file is simple to manage and reduces the chance of accidentally exposing the secret in process arguments, shell history, or logs.

### Public repository hygiene

Before every public push, confirm that no real secret has been copied into the repository:

```bash
git status
git diff --cached
git ls-files
```

Files matching common secret/runtime names are excluded by `.gitignore`, including:

```text
config.env
.env
*.pass
*.session
*.session-journal
```

Remember that `.gitignore` does not protect a file that was already tracked by Git. If a secret file was previously added, remove it from tracking and rotate the exposed credential if necessary.

---

## 25. Recommended periodic checks

At least every few months, perform a real cloud verification:

```bash
restore.py --verify
```

Even better, occasionally perform a test restore into an empty disk or directory and manually inspect a sample of restored files.

A backup that has never been restored is less trustworthy than a backup whose restore path has actually been tested.

---

## 26. Common problems

### `SOURCE is not a mount point`

If `/mnt/data` really is the intended mount point, verify it with:

```bash
mount | grep /mnt/data
```

If `SOURCE` is intentionally a subdirectory, use:

```dotenv
REQUIRE_SOURCE_MOUNT=false
```

### Telegram session is not authenticated

Run the login command again:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/telegram_login.py \
  --config /etc/telegram-dar-backup/config.env
```

### Channel not found

Run `telegram_login.py` again, inspect the channel list, and correct:

```dotenv
TELEGRAM_CHANNEL_ID=-100...
```

### Wrong DAR password

DAR cannot read the encrypted catalogue or archives. Check the file configured by:

```dotenv
DAR_PASSPHRASE_FILE=...
```

### Internet connection fails during upload

The current backup fails and the previous valid backup is not rotated away. A later run creates a new backup attempt.

### NAS powers off during a backup

The previous `COMPLETE` manifest remains valid. Some orphaned encrypted slices from the interrupted run may remain in the Telegram channel and consume storage. They can be removed manually by searching for the corresponding `TDB_SLICE_V1 backup=...` marker.

---

## 27. Upload verification details

During upload, the project records/checks:

1. local SHA-512 before upload;
2. successful creation of the Telegram message;
3. remote document size reported by Telegram;
4. SHA-512 and Telegram `message_id` in the backup manifest.

The strongest end-to-end check remains:

```bash
restore.py --verify
```

because it actually downloads the data again from Telegram, recalculates SHA-512, and asks DAR to test the encrypted archive.

---

## 28. Technical references

- DAR Debian manual: `https://manpages.debian.org/testing/dar/dar.1.en.html`
- DAR project: `https://dar.linux.free.fr/`
- Telethon documentation: `https://docs.telethon.dev/`
- Telegram API credentials: `https://my.telegram.org`

DAR provides native support for slicing, encryption, compression, differential backups, isolated catalogues, slice hashes, and commands executed between slices. Telethon is used for Telegram document upload/download and for reading the history of the private backup channel.

---

## 29. Production checklist

- [ ] create the private Telegram backup channel;
- [ ] create or choose the Telegram account used by the NAS;
- [ ] obtain the Telegram API ID and API Hash;
- [ ] run `install.sh`;
- [ ] edit `/etc/telegram-dar-backup/config.env`;
- [ ] replace the default value in `/etc/telegram-dar-backup/dar.pass`;
- [ ] apply `chmod 600` to sensitive files;
- [ ] run `telegram_login.py`;
- [ ] set the correct `TELEGRAM_CHANNEL_ID`;
- [ ] perform a manual FULL backup;
- [ ] run `restore.py --list`;
- [ ] run `restore.py --verify` on the FULL;
- [ ] ideally perform a small test restore;
- [ ] verify `git status` before publishing the repository;
- [ ] only then enable `telegram-dar-backup.timer`.

---

## 30. Suggested backup policy

A reasonable default policy for this project is:

```text
Local NAS data
   +
normal local/offline backup
   +
encrypted Telegram off-site copy
```

The Telegram copy is useful for geographic separation and disaster recovery, but it should not replace a conventional second backup when the data are important.
