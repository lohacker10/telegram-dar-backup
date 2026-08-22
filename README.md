# 📦 Telegram DAR Backup

Automated, encrypted, off-site backups from a Debian/Linux NAS to a **private Telegram channel**.

The project combines **DAR (Disk ARchive)** and **Telethon** to create encrypted FULL and differential backups, split them into Telegram-friendly slices, upload each slice progressively, and restore the data later without requiring a full backup-sized temporary disk.

> [!IMPORTANT]
> Telegram should be treated as an **additional off-site backup copy**, not as the only backup of important data. The archives are encrypted locally before upload, but Telegram itself is not a dedicated backup service with a storage SLA.

---

## ✨ Features

- 🔐 **Encrypted DAR archives** before anything is uploaded
- 🗜️ **Zstandard compression** by default
- 🧩 **1900 MiB slices**, suitable for standard Telegram file limits
- ☁️ **Progressive upload**: each completed slice is hashed, uploaded, then removed locally
- 📆 **Automatic FULL + differential strategy**
- 🔄 **New FULL every 6 months** by default
- 📉 Monthly **DIFF backups against the latest FULL**
- ✅ **SHA-512 verification** for every slice
- 🧾 JSON **manifests** containing the information required for recovery
- ♻️ **Safe rotation**: older backups are removed only after the new backup is complete
- 💾 **Low temporary-space requirements** during backup and restore
- 🛠️ **Headless operation** through SSH/terminal; no graphical interface required
- ⏰ Included **systemd service and timer** for monthly automation
- 🧪 Backup verification without extracting files
- 🚑 Disaster recovery even if the original NAS is lost

---

## 🧠 How it works

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

Each slice is uploaded as soon as DAR finishes writing it. This means the NAS does **not** need enough temporary space to hold the entire backup.

After a FULL backup, an encrypted isolated DAR catalogue is created and stored both locally and on Telegram. That catalogue becomes the reference for later differential backups.

With the default policy:

```text
Month 1  FULL A
Month 2  FULL A + DIFF B
Month 3  FULL A + DIFF C
Month 4  FULL A + DIFF D
Month 5  FULL A + DIFF E
Month 6  FULL A + DIFF F
Month 7  new FULL G
```

Each DIFF is calculated against the current FULL, not against the previous DIFF.

Therefore, restoring month 5 requires only:

```text
FULL A
+
DIFF E
```

rather than replaying every monthly backup in between.

---

## 🧾 Backup manifests

Every completed backup publishes a JSON manifest to the Telegram channel.

A manifest contains information such as:

- backup ID
- creation timestamp
- FULL or DIFF type
- reference FULL backup
- slice names
- slice sizes
- SHA-512 hashes
- Telegram message IDs
- FULL catalogue information when applicable

The manifest does **not** contain the DAR encryption password.

This allows the restore script to reconstruct the backup set from Telegram even if the original NAS no longer exists.

---

## 📁 Project structure

```text
telegram-dar-backup/
├── backup.py                  # FULL/DIFF backup and rotation
├── restore.py                 # list, verify and restore backups
├── restore_fetch.py           # progressive download hook for DAR
├── slice_upload.py            # progressive upload hook for DAR
├── telegram_login.py          # initial Telegram authentication
├── tdb_common.py              # shared helpers
├── config.env.example         # example configuration
├── requirements.txt
├── install.sh
├── .gitignore
└── systemd/
    ├── telegram-dar-backup.service
    └── telegram-dar-backup.timer
```

---

# 🚀 Installation

## 1. Requirements

A Debian-based Linux system or NAS with:

- Python 3
- `python3-venv`
- DAR
- Internet access
- enough free temporary space for at least a few backup slices
- a Telegram account with access to a private backup channel
- a Telegram API ID and API Hash

No desktop environment is required.

The entire setup can be performed through SSH.

---

## 2. Download the project

Clone the repository and enter the project directory:

```bash
git clone <repository-url>
cd telegram-dar-backup
```

Then run the installer:

```bash
sudo ./install.sh
```

The installer creates the main directories and installs the project under:

```text
/opt/telegram-dar-backup/
/etc/telegram-dar-backup/
/var/lib/telegram-dar-backup/
```

It also installs:

```text
/etc/systemd/system/telegram-dar-backup.service
/etc/systemd/system/telegram-dar-backup.timer
```

A Python virtual environment is created at:

```text
/opt/telegram-dar-backup/venv/
```

If no DAR password already exists, the installer generates a random passphrase and stores it in:

```text
/etc/telegram-dar-backup/dar.pass
```

with restrictive permissions.

> [!CAUTION]
> Keep a secure copy of the DAR password somewhere **outside the NAS**. Without it, encrypted backups cannot be restored.

---

# 📡 Telegram setup

## 3. Create a private channel

Create a dedicated **private Telegram channel** for backups.

A dedicated Telegram account for the NAS is optional, but can be useful to keep automated activity separate from a personal account.

The Telegram account used by this project must be able to:

- access the private channel
- publish files/messages
- read the channel history
- delete old backup messages when rotation is enabled

This project uses **Telethon / MTProto**, not a Telegram Bot Token.

---

## 4. Create Telegram API credentials

Open:

<https://my.telegram.org>

Create an application and obtain:

```text
api_id
api_hash
```

These values are used by Telethon to authenticate the Telegram client.

---

# ⚙️ Configuration

Edit:

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

Protect the configuration:

```bash
sudo chown root:root /etc/telegram-dar-backup/config.env
sudo chmod 600 /etc/telegram-dar-backup/config.env
```

---

## 🔒 Source mount protection

If the source is a mounted disk such as:

```text
/mnt/data
```

use:

```dotenv
REQUIRE_SOURCE_MOUNT=true
```

The backup will refuse to start if the path exists but is not an actual mount point. This helps prevent accidentally backing up an empty mount directory when the source disk is missing.

If `SOURCE` intentionally points to a subdirectory, for example:

```text
/mnt/storage/documents
```

use:

```dotenv
REQUIRE_SOURCE_MOUNT=false
```

---

# 🔐 Encryption password

The recommended setup uses:

```dotenv
DAR_PASSPHRASE_FILE=/etc/telegram-dar-backup/dar.pass
```

The password file should contain only the passphrase on a single line.

Protect it with:

```bash
sudo chown root:root /etc/telegram-dar-backup/dar.pass
sudo chmod 600 /etc/telegram-dar-backup/dar.pass
```

An alternative is to store the password directly in `config.env`:

```dotenv
DAR_PASSPHRASE=a-long-random-password
```

Using a separate password file is generally cleaner.

The password is not passed directly as a visible DAR command-line argument. The scripts generate a temporary DAR command file with restrictive permissions instead.

> [!WARNING]
> Do not lose the encryption password. There is no recovery mechanism for an unknown DAR passphrase.

---

# 🔑 First Telegram login

Run:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/telegram_login.py \
  --config /etc/telegram-dar-backup/config.env
```

Telethon may ask for:

```text
Telegram phone number: +...
Telegram code: 12345
2FA password: ********
```

This is required only when creating or re-authenticating the Telethon session.

After login, the script lists the Telegram channels visible to the account, for example:

```text
-1001234567890  Backup Channel
```

Copy the desired ID into:

```dotenv
TELEGRAM_CHANNEL_ID=-1001234567890
```

The authenticated Telethon session is stored at:

```text
/var/lib/telegram-dar-backup/telegram.session
```

Protect it:

```bash
sudo chmod 600 /var/lib/telegram-dar-backup/telegram.session
```

> [!CAUTION]
> The Telethon session is an authentication credential. Protect it like a password.

---

# 💾 Creating backups

## First manual backup

Before enabling automation, start a backup manually:

```bash
sudo systemctl start telegram-dar-backup.service
```

Follow its log:

```bash
sudo journalctl -fu telegram-dar-backup.service
```

Or run the Python script directly:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/backup.py \
  --config /etc/telegram-dar-backup/config.env
```

The first backup is automatically a **FULL** backup.

---

## Force a FULL backup

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/backup.py \
  --config /etc/telegram-dar-backup/config.env \
  --force full
```

---

## Force a differential backup

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/backup.py \
  --config /etc/telegram-dar-backup/config.env \
  --force diff
```

A DIFF requires an existing valid FULL backup.

---

# ☁️ Progressive upload

The default slice size is:

```dotenv
SLICE_SIZE=1900M
```

DAR produces files such as:

```text
archive.0001.dar
archive.0002.dar
archive.0003.dar
...
```

For every completed slice:

```text
DAR closes slice
      ↓
calculate SHA-512
      ↓
upload with Telethon
      ↓
verify remote size
      ↓
record Telegram message ID
      ↓
delete local slice
```

DAR waits for the upload hook before continuing, so the NAS does not accumulate hundreds of gigabytes of temporary archive data.

The final slice of a FULL may be retained briefly while the isolated catalogue is created, then removed as well.

---

# 📦 Temporary disk-space usage

With 1900 MiB slices, only a small amount of temporary disk space is normally required:

```text
~2 GB current slice
+ DAR catalogue
+ small state/journal files
```

The exact catalogue size depends mostly on the number of files being backed up.

A 1 TB source therefore does **not** require 1 TB of free temporary space.

---

# ♻️ Backup rotation and failure safety

Older valid backups are never removed before the replacement backup has completed.

The workflow is:

```text
existing valid backup
        ↓
create new backup
        ↓
upload all slices
        ↓
upload catalogue if FULL
        ↓
publish COMPLETE manifest
        ↓
new backup committed
        ↓
rotate old backup generation
```

If the backup fails before the `COMPLETE` manifest is published, the previous valid backup remains untouched.

If the new backup is already complete but cleanup of the previous generation fails, both generations may remain temporarily. This is intentionally safer than deleting a valid backup too early.

With:

```dotenv
KEEP_ONLY_LATEST_DIFF=true
```

the previous differential is removed only after the new differential is valid.

When a new FULL is created, the previous FULL generation is rotated only after the replacement FULL succeeds.

---

# ⏰ Automatic monthly backups

The supplied systemd timer runs on the first day of every month at 03:15, with a randomized delay of up to 20 minutes.

Enable it with:

```bash
sudo systemctl enable --now telegram-dar-backup.timer
```

Check the next scheduled run:

```bash
systemctl list-timers telegram-dar-backup.timer
```

View logs:

```bash
sudo journalctl -u telegram-dar-backup.service
```

To change the schedule:

```bash
sudo systemctl edit --full telegram-dar-backup.timer
```

Then reload systemd:

```bash
sudo systemctl daemon-reload
```

---

# 🔎 Listing available backups

Run:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/restore.py \
  --config /etc/telegram-dar-backup/config.env \
  --list
```

Example output:

```text
2026-08-01T03:20:00Z  FULL  20260801T032000Z_FULL_ab12cd
2026-09-01T03:20:00Z  DIFF  20260901T032000Z_DIFF_ef34aa  base=20260801T032000Z_FULL_ab12cd
```

---

# ✅ Verifying a backup

A backup can be tested without extracting it.

Interactive verification:

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

Verification progressively:

1. downloads each slice from Telegram;
2. checks its size;
3. recalculates SHA-512;
4. asks DAR to test the encrypted archive.

If a DIFF is selected, its reference FULL is verified too.

Regular verification is strongly recommended because it tests the actual cloud copy rather than only local metadata.

---

# 🚑 Restoring a backup

Prepare an empty destination directory or mount point, for example:

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

If no backup ID is specified, an interactive menu is displayed.

Example:

```text
Available backups:

  [1] 2026-09-01T03:20:00Z DIFF  20260901T032000Z_DIFF_ef34aa
  [2] 2026-08-01T03:20:00Z FULL  20260801T032000Z_FULL_ab12cd

Backup number to restore:
```

When restoring a DIFF, the script automatically restores:

```text
reference FULL
      ↓
selected DIFF
      ↓
final filesystem state
```

This includes changes recorded by DAR, including deletions represented by the differential archive.

---

## Restore a specific backup

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/restore.py \
  --config /etc/telegram-dar-backup/config.env \
  --backup-id 20260901T032000Z_DIFF_ef34aa \
  --dest /mnt/restore
```

For safety, the destination is expected to be empty.

To deliberately restore into a non-empty destination:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/restore.py \
  --config /etc/telegram-dar-backup/config.env \
  --backup-id 20260901T032000Z_DIFF_ef34aa \
  --dest /mnt/restore \
  --allow-nonempty
```

Use `--allow-nonempty` carefully.

---

# 📥 Progressive restore

The restore process does not download the whole backup before extraction.

DAR runs in sequential-read mode and obtains slices one at a time:

```text
restore_fetch.py
      ↓
download requested Telegram slice
      ↓
verify size
      ↓
verify SHA-512
      ↓
DAR reads the slice
      ↓
move to the next slice
```

Temporary storage therefore stays roughly around the size of one slice rather than the size of the entire archive.

The restore destination must of course have enough free space for the recovered data.

---

# 🆘 Disaster recovery after losing the NAS

The restore design intentionally avoids depending on the original NAS state.

To recover on a new Debian machine, you need:

1. access to the Telegram account/channel containing the backups;
2. the Telegram API ID and API Hash;
3. the DAR encryption password;
4. this project;
5. enough storage for the restored data.

Install the project on the replacement machine, configure Telegram credentials, authenticate Telethon again with `telegram_login.py`, and run `restore.py`.

The backup manifests stored in Telegram are used to discover the available FULL and DIFF generations.

For disaster recovery, keep the following somewhere independent from the NAS:

```text
DAR password
Telegram account recovery access
Telegram API credentials
```

---

# 🔄 Changing the encryption password

Differential backups depend on the catalogue of their reference FULL.

Do not change the encryption password in the middle of a FULL/DIFF generation.

A safe password-change procedure is:

1. change `/etc/telegram-dar-backup/dar.pass`;
2. force a new FULL backup;
3. verify the new FULL;
4. only then retire the old password and old backup generation.

Force the new FULL with:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/backup.py \
  --config /etc/telegram-dar-backup/config.env \
  --force full
```

---

# 🗜️ Compression

Default configuration:

```dotenv
COMPRESSION=zstd:6
```

This provides a reasonable balance between CPU usage and compression ratio.

For data that is already compressed, such as:

- JPEG photos
- MP4/MKV video
- MP3/AAC audio
- ZIP/RAR/7z archives

additional compression may be minimal. A lower setting such as:

```dotenv
COMPRESSION=zstd:3
```

may reduce CPU usage.

For highly compressible data, higher levels may save more space at the cost of additional CPU time.

---

# 🗃️ Files that change during backup

DAR can detect and retry files that change while they are being read, but this is not equivalent to taking a filesystem snapshot.

For mostly static data such as documents, media libraries, and archives, this is often sufficient.

For frequently changing data such as:

- databases
- virtual machine images
- application state
- continuously written files

prefer backing up a filesystem/LVM snapshot or quiescing the application before the backup starts.

---

# 🧬 Metadata preservation

The systemd service runs as `root` so DAR can preserve as much filesystem metadata as possible, including where supported:

- ownership
- Unix permissions
- hard links
- symbolic links
- ACLs
- extended attributes
- sparse files

For best results, restore onto a Linux filesystem that supports the same metadata, such as ext4, XFS, or Btrfs.

---

# 🛡️ Security notes

The uploaded DAR slices are encrypted **before** they reach Telegram.

Telegram therefore stores encrypted archive data plus limited operational metadata such as:

```text
backup ID
FULL/DIFF type
timestamp
slice number
hash
Telegram message ID
```

Protect these local files carefully:

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

The Telegram session and DAR passphrase are both sensitive credentials.

The project intentionally avoids placing the DAR password directly in the systemd command line.

---

# 🧪 Recommended backup checks

A backup strategy should be tested, not merely assumed to work.

Recommended routine:

- run `restore.py --verify` periodically;
- occasionally perform a real restore into an empty directory or spare disk;
- inspect several restored files manually;
- keep an independent copy of the DAR password;
- check systemd logs after scheduled runs.

A backup whose restore path has been tested is far more trustworthy than one that has only been uploaded successfully.

---

# 🛠️ Troubleshooting

## `SOURCE is not a mount point`

If the source should be a mounted disk, verify it:

```bash
mount | grep /mnt/data
```

If `SOURCE` intentionally points to a subdirectory, use:

```dotenv
REQUIRE_SOURCE_MOUNT=false
```

---

## Telegram session is not authenticated

Run the login process again:

```bash
sudo /opt/telegram-dar-backup/venv/bin/python \
  /opt/telegram-dar-backup/telegram_login.py \
  --config /etc/telegram-dar-backup/config.env
```

---

## Channel not found

Run `telegram_login.py`, inspect the listed channels, and update:

```dotenv
TELEGRAM_CHANNEL_ID=-100...
```

---

## Wrong DAR password

Check the value configured by:

```dotenv
DAR_PASSPHRASE_FILE=...
```

If the password is incorrect, DAR will not be able to read the encrypted catalogue or archive.

---

## Internet connection fails during upload

The current backup attempt fails and the previously valid backup generation remains intact.

A later run can create a new backup attempt.

---

## The NAS powers off during a backup

Any previously published `COMPLETE` backup remains valid.

Encrypted orphan slices from the interrupted backup may remain in the Telegram channel and can be cleaned up later.

---

# 🔬 Upload verification

For every slice, the project records or checks:

1. local SHA-512 before upload;
2. successful Telegram message creation;
3. remote document size;
4. Telegram message ID;
5. SHA-512 stored in the final manifest.

The strongest end-to-end test is still:

```bash
restore.py --verify
```

because it downloads the data again from Telegram, recalculates SHA-512, decrypts the archive and asks DAR to test it.

---

# ✅ Setup checklist

- [ ] Create a private Telegram backup channel
- [ ] Choose the Telegram account used for backups
- [ ] Obtain Telegram API ID and API Hash
- [ ] Run `install.sh`
- [ ] Configure `/etc/telegram-dar-backup/config.env`
- [ ] Store the generated DAR password somewhere safe outside the NAS
- [ ] Run `telegram_login.py`
- [ ] Set the correct `TELEGRAM_CHANNEL_ID`
- [ ] Run a manual FULL backup
- [ ] Run `restore.py --list`
- [ ] Run `restore.py --verify`
- [ ] Perform at least one test restore
- [ ] Enable `telegram-dar-backup.timer`

---

# 📚 References

- [DAR project](https://dar.linux.free.fr/)
- [DAR Debian manual](https://manpages.debian.org/testing/dar/dar.1.en.html)
- [Telethon documentation](https://docs.telethon.dev/)
- [Telegram API credentials](https://my.telegram.org)

---

# 💡 Recommended backup strategy

A sensible setup is:

```text
Primary NAS data
      +
local/offline backup
      +
encrypted Telegram off-site backup
```

This project is intended to provide the **off-site encrypted copy** in that strategy.
