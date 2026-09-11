# Runbook: запуск, деплой и rollback

## Локальный запуск Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py runserver 127.0.0.1:8000
```

Открыть:

```text
http://127.0.0.1:8000/
```

Локальный `.env`:

```env
DEBUG=True
SECRET_KEY=django-insecure-local-development-key-change-in-production
ALLOWED_HOSTS=127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
SECURE_SSL_REDIRECT=False
SECURE_HSTS_SECONDS=0
ENABLE_PAGE_TRANSITIONS=False
```

## Проверки перед изменениями и деплоем

```powershell
.\.venv\Scripts\python manage.py makemigrations --check --dry-run
.\.venv\Scripts\python manage.py check
.\.venv\Scripts\python manage.py test
```

Для static:

```powershell
.\.venv\Scripts\python manage.py collectstatic --noinput
```

## Production `.env`

```env
DEBUG=False
SECRET_KEY=replace-with-long-random-secret
ALLOWED_HOSTS=example.com,www.example.com
CSRF_TRUSTED_ORIGINS=https://example.com,https://www.example.com
DATABASE_URL=postgresql://lab_user:strong_password@127.0.0.1:5432/lab_production
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_SSL_REDIRECT=False
SECURE_HSTS_SECONDS=31536000
ENABLE_PAGE_TRANSITIONS=False
```

HTTPS-redirect обычно делает Nginx/Certbot. Django получает `X-Forwarded-Proto`.

`ENABLE_PAGE_TRANSITIONS=True` включает JS-анимацию безопасной внутренней GET-навигации. По умолчанию переходы отключены.

## Ubuntu VPS setup

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip postgresql nginx certbot python3-certbot-nginx
```

PostgreSQL:

```sql
CREATE DATABASE lab_production;
CREATE USER lab_user WITH PASSWORD 'strong_password';
ALTER ROLE lab_user SET client_encoding TO 'utf8';
ALTER ROLE lab_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE lab_user SET timezone TO 'Europe/Minsk';
GRANT ALL PRIVILEGES ON DATABASE lab_production TO lab_user;
```

Код:

```bash
sudo mkdir -p /opt/lab-production
sudo chown $USER:$USER /opt/lab-production
cd /opt/lab-production
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
```

Права:

```bash
sudo mkdir -p /opt/lab-production/media /opt/lab-production/staticfiles
sudo chown -R www-data:www-data /opt/lab-production/media /opt/lab-production/staticfiles
```

## systemd service

`/etc/systemd/system/lab-production.service`:

```ini
[Unit]
Description=Lab production Django app
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/lab-production
EnvironmentFile=/opt/lab-production/.env
ExecStart=/opt/lab-production/.venv/bin/gunicorn lab_production.wsgi:application --bind 127.0.0.1:8001
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lab-production
sudo systemctl status lab-production
```

## Nginx

`/etc/nginx/sites-available/lab-production`:

```nginx
server {
    server_name example.com www.example.com;

    client_max_body_size 100M;

    location /static/ {
        alias /opt/lab-production/staticfiles/;
    }

    location /media/ {
        alias /opt/lab-production/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/lab-production /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## HTTPS

DNS: `A`-запись домена должна указывать на IP сервера.

```bash
sudo certbot --nginx -d example.com -d www.example.com
sudo certbot renew --dry-run
```

## Deployment sequence

1. Сделать backup базы и media.
2. Загрузить код на сервер.
3. Установить зависимости при изменении `requirements.txt`.
4. Выполнить миграции.
5. Выполнить `collectstatic`.
6. Перезапустить сервис.
7. Проверить Nginx и страницу входа.

```bash
cd /opt/lab-production
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart lab-production
sudo systemctl reload nginx
```

## Backup

В проекте есть серверный скрипт `scripts/server_backup.sh`. Он создает отдельную директорию backup с:

- `db.sqlite3` для SQLite или `database.dump` для PostgreSQL;
- `media.tar.gz`;
- копией `.env` в файле `env`;
- `code.tar.gz` без `.venv`, `media`, `staticfiles` и локальной базы;
- `manifest.txt`.

Ручной запуск:

```bash
cd /opt/lab-production
sudo bash scripts/server_backup.sh
```

По умолчанию backup сохраняется в `/opt/lab-production-backups`, старые backup-директории старше 30 дней удаляются.

Параметры можно переопределить:

```bash
sudo APP_DIR=/opt/lab-production BACKUP_ROOT=/opt/lab-production-backups KEEP_DAYS=60 bash scripts/server_backup.sh
```

Проверка последнего backup:

```bash
ls -lah /opt/lab-production-backups
LATEST=$(find /opt/lab-production-backups -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)
cat "$LATEST/manifest.txt"
ls -lah "$LATEST"
```

### Автоматический backup через systemd timer

`/etc/systemd/system/lab-production-backup.service`:

```ini
[Unit]
Description=Backup Lab production Django app

[Service]
Type=oneshot
WorkingDirectory=/opt/lab-production
ExecStart=/bin/bash /opt/lab-production/scripts/server_backup.sh
```

`/etc/systemd/system/lab-production-backup.timer`:

```ini
[Unit]
Description=Daily backup for Lab production Django app

[Timer]
OnCalendar=*-*-* 03:20:00
Persistent=true

[Install]
WantedBy=timers.target
```

Включение:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lab-production-backup.timer
sudo systemctl list-timers lab-production-backup.timer
```

Тестовый запуск:

```bash
sudo systemctl start lab-production-backup.service
sudo journalctl -u lab-production-backup.service -n 80 --no-pager
```

### Восстановление SQLite backup

```bash
sudo systemctl stop lab-production

LATEST=/opt/lab-production-backups/YYYYMMDD-HHMMSS
cp /opt/lab-production/db.sqlite3 /opt/lab-production/db.sqlite3.before-restore-$(date +%Y%m%d-%H%M%S) 2>/dev/null || true
cp "$LATEST/db.sqlite3" /opt/lab-production/db.sqlite3

tar -xzf "$LATEST/media.tar.gz" -C /opt/lab-production

cd /opt/lab-production
source .venv/bin/activate
python manage.py migrate
python manage.py check

sudo systemctl start lab-production
```

### Восстановление PostgreSQL backup

```bash
sudo systemctl stop lab-production
pg_restore --clean --if-exists --dbname "$DATABASE_URL" /opt/lab-production-backups/YYYYMMDD-HHMMSS/database.dump
tar -xzf /opt/lab-production-backups/YYYYMMDD-HHMMSS/media.tar.gz -C /opt/lab-production
sudo systemctl start lab-production
```

PostgreSQL:

```bash
pg_dump -U lab_user -h 127.0.0.1 lab_production > backup.sql
```

Media:

```bash
tar -czf media-backup.tar.gz /opt/lab-production/media
```

## Rollback

1. Вернуть предыдущий код.
2. Если была миграция, оценить возможность reverse migration.
3. При необходимости восстановить DB backup.
4. Восстановить media backup.
5. Выполнить `collectstatic`.
6. Перезапустить сервис.

```bash
sudo systemctl stop lab-production
# restore code/db/media
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
sudo systemctl start lab-production
sudo systemctl reload nginx
```

Если миграция необратима, rollback выполняется восстановлением backup базы.
