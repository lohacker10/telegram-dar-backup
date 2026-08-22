#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo ./install.sh" >&2
  exit 1
fi

SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP_DIR=/opt/telegram-dar-backup
ETC_DIR=/etc/telegram-dar-backup
STATE_DIR=/var/lib/telegram-dar-backup

apt-get update
apt-get install -y dar python3 python3-venv python3-pip

install -d -m 0755 "$APP_DIR" "$ETC_DIR"
install -d -m 0700 "$STATE_DIR"

for f in backup.py restore.py restore_fetch.py slice_upload.py telegram_login.py tdb_common.py requirements.txt; do
  install -m 0755 "$SRC_DIR/$f" "$APP_DIR/$f"
done
chmod 0644 "$APP_DIR/requirements.txt"

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"
# cryptg accelerates Telethon MTProto encryption, but it is optional.
"$APP_DIR/venv/bin/pip" install "cryptg>=0.4,<1" || echo "Note: cryptg was not installed; Telethon will still work."

if [ ! -f "$ETC_DIR/config.env" ]; then
  install -m 0600 "$SRC_DIR/config.env.example" "$ETC_DIR/config.env"
  echo "Created $ETC_DIR/config.env: edit it before running the first backup."
fi

if [ ! -f "$ETC_DIR/dar.pass" ]; then
  umask 077
  python3 - <<'PYSECRET' > "$ETC_DIR/dar.pass"
import secrets
print(secrets.token_urlsafe(48))
PYSECRET
  chmod 0600 "$ETC_DIR/dar.pass"
  echo "Created $ETC_DIR/dar.pass with a random passphrase."
  echo "IMPORTANT: store a copy of this passphrase outside the NAS before relying on the backup."
fi

install -m 0644 "$SRC_DIR/systemd/telegram-dar-backup.service" /etc/systemd/system/telegram-dar-backup.service
install -m 0644 "$SRC_DIR/systemd/telegram-dar-backup.timer" /etc/systemd/system/telegram-dar-backup.timer
systemctl daemon-reload

echo
echo "Installation complete. Next steps:"
echo "  1) nano $ETC_DIR/config.env"
echo "  2) securely save the generated password (or replace it): nano $ETC_DIR/dar.pass"
echo "  3) $APP_DIR/venv/bin/python $APP_DIR/telegram_login.py --config $ETC_DIR/config.env"
echo "  4) test: systemctl start telegram-dar-backup.service"
echo "  5) enable: systemctl enable --now telegram-dar-backup.timer"
