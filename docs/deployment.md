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
ACCOUNT_INACTIVITY_DAYS=90
ACCOUNT_ACTIVITY_UPDATE_INTERVAL_SECONDS=3600
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
ACCOUNT_INACTIVITY_DAYS=90
ACCOUNT_ACTIVITY_UPDATE_INTERVAL_SECONDS=3600
```

HTTPS-redirect обычно делает Nginx/Certbot. Django получает `X-Forwarded-Proto`.

`ENABLE_PAGE_TRANSITIONS=True` включает JS-анимацию безопасной внутренней GET-навигации. По умолчанию переходы отключены.

`ACCOUNT_INACTIVITY_DAYS=90` задает срок удаления обычных аккаунтов без активности. Первый пользователь и staff/superuser аккаунты защищены от автоматического удаления по умолчанию.

## Ubuntu 26.04 LTS: установка на пустой сервер

Ниже используется PostgreSQL, Nginx и Gunicorn. Домен `example.com` замените своим; его DNS-запись должна указывать на сервер, порты 80 и 443 должны быть доступны. Команды выполняются от root (`sudo -i`).

Ubuntu 26.04 использует Python 3.14; проект требует Django не ниже 5.2.8. Конфигурации для установки находятся в `deploy/`.

```bash
umask 022
apt update
apt install -y git python3-venv python3-pip postgresql nginx certbot python3-certbot-nginx openssl nano
systemctl enable --now postgresql nginx
git clone --branch v1.3.2 https://github.com/vdnrobo/roboprod.git /opt/lab-production
cd /opt/lab-production
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo -u postgres createuser --pwprompt lab_user
sudo -u postgres createdb --owner=lab_user lab_production
```

`createuser` запросит пароль. Используйте пароль, безопасный для URL (например, случайный hex, сгенерированный `openssl rand -hex 24`); либо закодируйте специальные символы для `DATABASE_URL`.

```bash
install -o root -g www-data -m 640 .env.example .env
openssl rand -hex 32
nano .env
```

В `.env` замените `SECRET_KEY` на полученный секрет, домены в `ALLOWED_HOSTS` (без схемы) и `CSRF_TRUSTED_ORIGINS` (с `https://`), пароль в `DATABASE_URL` на пароль `lab_user`. Для одного домена уберите `www.example.com`. Сохраните `DEBUG=False`: сборка статики должна выполняться уже с этим значением, иначе production-манифест не будет подготовлен.

```bash
install -d -o www-data -g www-data media staticfiles
sudo -u www-data .venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput
chown -R www-data:www-data staticfiles
sudo -u www-data .venv/bin/python manage.py createsuperuser
sudo -u www-data .venv/bin/python manage.py check --deploy
```

Администратора создайте до публикации сайта, чтобы первым пользователем был владелец сайта. HTTPS-redirect выполняет Nginx/Certbot, поэтому Django может выдать предупреждение об отключенном `SECURE_SSL_REDIRECT`; не отключайте secure cookies для публичного сайта.

```bash
cp deploy/*.service deploy/*.timer /etc/systemd/system/
cp deploy/nginx.conf /etc/nginx/sites-available/lab-production
nano /etc/nginx/sites-available/lab-production
ln -s /etc/nginx/sites-available/lab-production /etc/nginx/sites-enabled/lab-production
systemctl daemon-reload
systemctl enable --now lab-production
nginx -t
systemctl reload nginx
certbot --nginx --redirect -d example.com
certbot renew --dry-run
systemctl enable --now lab-production-backup.timer lab-production-prune-inactive-accounts.timer
systemctl start lab-production-backup.service
systemctl status lab-production --no-pager
```

В конфигурации Nginx замените `server_name example.com` своим доменом; такой же домен укажите в команде Certbot. При использовании `www` добавьте его и в Nginx, и в `.env`, и вторым параметром `-d` при выпуске сертификата. Расписание systemd-таймеров использует часовой пояс сервера.

После установки проверьте вход, 404 по несуществующему адресу и переключатель темы. Настройте строки расписания резки через «Объявления и отсчеты». Бэкапы находятся в `/opt/lab-production-backups`; таймер очистки использует `ACCOUNT_INACTIVITY_DAYS` из `.env`.

Если запрос возвращает 500 после переключения `DEBUG=False`, повторите `collectstatic` с этим значением и перезапустите `lab-production`. Для диагностики используйте `journalctl -u lab-production -n 100 --no-pager`.

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

### Автоматическая очистка неактивных аккаунтов

Команда очистки:

```bash
cd /opt/lab-production
sudo -u www-data .venv/bin/python manage.py prune_inactive_accounts --days 90 --dry-run
sudo -u www-data .venv/bin/python manage.py prune_inactive_accounts --days 90
```

По умолчанию удаляются только обычные пользователи, чья последняя активность старше 90 дней. Первый пользователь и staff/superuser аккаунты не удаляются.

`/etc/systemd/system/lab-production-prune-inactive-accounts.service`:

```ini
[Unit]
Description=Prune inactive Lab production accounts

[Service]
Type=oneshot
WorkingDirectory=/opt/lab-production
User=www-data
Group=www-data
ExecStart=/opt/lab-production/.venv/bin/python manage.py prune_inactive_accounts --days 90
```

`/etc/systemd/system/lab-production-prune-inactive-accounts.timer`:

```ini
[Unit]
Description=Daily inactive account cleanup for Lab production

[Timer]
OnCalendar=*-*-* 03:40:00
Persistent=true

[Install]
WantedBy=timers.target
```

Включение:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lab-production-prune-inactive-accounts.timer
sudo systemctl list-timers lab-production-prune-inactive-accounts.timer
```

Тестовый запуск:

```bash
sudo systemctl start lab-production-prune-inactive-accounts.service
sudo journalctl -u lab-production-prune-inactive-accounts.service -n 80 --no-pager
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
