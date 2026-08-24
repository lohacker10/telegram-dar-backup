#!/bin/sh
set -eu

APP_DIR=/opt/telegram-dar-backup
ETC_DIR=/etc/telegram-dar-backup
STATE_DIR=/var/lib/telegram-dar-backup
SERVICE=telegram-dar-backup.service
TIMER=telegram-dar-backup.timer
PURGE_LOCAL_DATA=0
ASSUME_YES=0

usage() {
  cat <<'EOF'
Usage: sudo ./uninstall.sh [--purge-local-data] [--yes]

Default uninstall:
  - stops/disables Telegram DAR Backup systemd units
  - removes installed code under /opt/telegram-dar-backup
  - removes the systemd unit files
  - PRESERVES /etc/telegram-dar-backup and /var/lib/telegram-dar-backup

Options:
  --purge-local-data  Also remove local config, DAR passphrase, Telegram session,
                      catalogues and runtime state.
  --yes               Skip the destructive purge confirmation. Has effect only
                      together with --purge-local-data.
  -h, --help          Show this help.

This script never deletes backup messages stored in Telegram.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --purge-local-data)
      PURGE_LOCAL_DATA=1
      ;;
    --yes)
      ASSUME_YES=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo ./uninstall.sh" >&2
  exit 1
fi

if [ "$PURGE_LOCAL_DATA" -eq 1 ] && [ "$ASSUME_YES" -ne 1 ]; then
  cat >&2 <<EOF
WARNING: --purge-local-data will permanently remove:
  $ETC_DIR
  $STATE_DIR

This includes config.env, dar.pass, the Telegram session and local catalogues.
Telegram backup messages will NOT be deleted.

If dar.pass is not stored safely elsewhere, deleting it can make encrypted
Telegram backups impossible to restore.
EOF
  printf "Type DELETE LOCAL DATA to continue: " >&2
  IFS= read -r answer
  if [ "$answer" != "DELETE LOCAL DATA" ]; then
    echo "Purge cancelled; nothing has been removed." >&2
    exit 1
  fi
fi

echo "Stopping Telegram DAR Backup services..."
if command -v systemctl >/dev/null 2>&1; then
  systemctl disable --now "$TIMER" >/dev/null 2>&1 || true
  systemctl stop "$SERVICE" >/dev/null 2>&1 || true
fi

echo "Removing systemd unit files..."
rm -f "/etc/systemd/system/$SERVICE" "/etc/systemd/system/$TIMER"
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload
  systemctl reset-failed "$SERVICE" "$TIMER" >/dev/null 2>&1 || true
fi

echo "Removing installed application code..."
rm -rf "$APP_DIR"

if [ "$PURGE_LOCAL_DATA" -eq 1 ]; then
  echo "Removing local configuration, secrets and state..."
  rm -rf "$ETC_DIR" "$STATE_DIR"
  echo "Local data purged. Telegram backup messages were not touched."
else
  echo
  echo "Uninstall complete. Local recovery material was preserved:"
  echo "  $ETC_DIR"
  echo "  $STATE_DIR"
  echo
  echo "To remove it later, run the repository copy with:"
  echo "  sudo ./uninstall.sh --purge-local-data"
fi

echo
echo "Note: dar, rsync, Python and other apt packages were left installed because"
echo "they may be used by other applications."
