#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/lab-production}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/lab-production-backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${BACKUP_ROOT}/${TIMESTAMP}"

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

if [ ! -d "${APP_DIR}" ]; then
  echo "Application directory not found: ${APP_DIR}" >&2
  exit 1
fi

cd "${APP_DIR}"

DATABASE_URL=""
if [ -f ".env" ]; then
  cp .env "${BACKUP_DIR}/env"
  chmod 600 "${BACKUP_DIR}/env"
  DATABASE_URL="$(python3 - <<'PY'
from pathlib import Path

env_path = Path(".env")
value = ""
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key.strip() == "DATABASE_URL":
            value = raw_value.strip().strip('"').strip("'")
            break
print(value)
PY
)"
fi

if [ -n "${DATABASE_URL}" ] && [[ "${DATABASE_URL}" == postgres* ]]; then
  if ! command -v pg_dump >/dev/null 2>&1; then
    echo "pg_dump is required for PostgreSQL backups." >&2
    exit 1
  fi
  pg_dump "${DATABASE_URL}" --format=custom --file="${BACKUP_DIR}/database.dump"
  echo "database=postgresql" >> "${BACKUP_DIR}/manifest.txt"
elif [ -f "db.sqlite3" ]; then
  python3 - <<PY
import sqlite3
from pathlib import Path

source = Path("${APP_DIR}") / "db.sqlite3"
target = Path("${BACKUP_DIR}") / "db.sqlite3"
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
PY
  echo "database=sqlite" >> "${BACKUP_DIR}/manifest.txt"
else
  echo "database=missing" >> "${BACKUP_DIR}/manifest.txt"
fi

if [ -d "media" ]; then
  tar -czf "${BACKUP_DIR}/media.tar.gz" -C "${APP_DIR}" media
else
  echo "media=missing" >> "${BACKUP_DIR}/manifest.txt"
fi

tar -czf "${BACKUP_DIR}/code.tar.gz" \
  --exclude=".venv" \
  --exclude="db.sqlite3" \
  --exclude="media" \
  --exclude="staticfiles" \
  --exclude="*.tar.gz" \
  --exclude="__pycache__" \
  -C "${APP_DIR}" .

{
  echo "created_at=${TIMESTAMP}"
  echo "app_dir=${APP_DIR}"
  echo "backup_dir=${BACKUP_DIR}"
  echo "keep_days=${KEEP_DAYS}"
} >> "${BACKUP_DIR}/manifest.txt"

find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -mtime "+${KEEP_DAYS}" -exec rm -rf {} +

echo "Backup created: ${BACKUP_DIR}"
