# RoboProd

Django-приложение для управления очередью производства в робототехнической лаборатории: заказы на 3D-печать и лазерную резку, материалы, статусы, выдача, архив, пользователи, роли и аудит.

Репозиторий: <https://github.com/vdnrobo/roboprod>

Текущая версия: `v1.0.0`.

## Возможности

- Пошаговое создание заказа пользователем.
- Черновики заказов и повтор заказа с копированием файлов.
- Отдельные очереди: активные заказы, готовые к выдаче, архив.
- Массовая загрузка заказов администратором.
- Роли: пользователь, администратор, полупечатник.
- Материалы для 3D-печати и лазерной резки.
- Стандартные причины отклонения.
- Ячейки выдачи.
- CSV-экспорт, dashboard, журнал статусов и аудит.
- Закрепленные объявления, отсчеты и график дежурства.
- Опциональные переходы между страницами через `ENABLE_PAGE_TRANSITIONS`.

## Быстрый запуск

```powershell
git clone https://github.com/vdnrobo/roboprod.git
cd roboprod

python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt

Copy-Item .env.example .env
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py runserver 127.0.0.1:8000
```

Откройте:

```text
http://127.0.0.1:8000/
```

Первый зарегистрированный пользователь автоматически получает права администратора.

## Переменные окружения

Пример находится в [.env.example](.env.example).

Основные параметры:

- `DEBUG` - режим разработки.
- `SECRET_KEY` - секрет Django, в production должен быть уникальным.
- `ALLOWED_HOSTS` - домены и IP приложения.
- `CSRF_TRUSTED_ORIGINS` - доверенные HTTPS origins.
- `DATABASE_URL` - PostgreSQL DSN для production. Если не задан, используется SQLite.
- `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` - secure-cookie для HTTPS.
- `ENABLE_PAGE_TRANSITIONS` - включает JS-переходы страниц. По умолчанию `False`.

Не коммитьте реальный `.env`, базу, uploads и backup-архивы. Они исключены в [.gitignore](.gitignore).

## Проверки

```powershell
.\.venv\Scripts\python manage.py makemigrations --check --dry-run
.\.venv\Scripts\python manage.py check
.\.venv\Scripts\python manage.py test
```

GitHub Actions запускает эти проверки на `push` и `pull_request`.

## Основные маршруты

- `/register/` - регистрация.
- `/login/` - вход.
- `/orders/` - заказы пользователя.
- `/orders/new/` - создание заказа.
- `/admin-panel/orders/` - активная очередь производства.
- `/admin-panel/orders/ready/` - готовые к выдаче.
- `/admin-panel/orders/archive/` - архив.
- `/admin-panel/bulk/` - массовая загрузка.
- `/admin-panel/materials/` - материалы.
- `/admin-panel/users/` - пользователи и роли.
- `/admin-panel/status-log/` - журнал статусов.
- `/admin-panel/audit/` - аудит.
- `/django-admin/` - стандартная Django-админка.

## Структура проекта

```text
lab_production/       Django settings, urls, wsgi/asgi
production/           основное приложение: models, forms, views, migrations, tests
templates/            Django-шаблоны
static/               CSS, JS и локальные frontend vendor-файлы
scripts/              server-side утилиты, включая backup
docs/                 пользовательская, админская и developer-документация
```

## Документация

- [Оглавление](docs/index.md)
- [Инструкция пользователя](docs/user-guide.md)
- [Инструкция администратора](docs/admin-guide.md)
- [Архитектура для разработчиков](docs/developer/architecture.md)
- [Справочник кода](docs/developer/code-reference.md)
- [Runbook деплоя и rollback](docs/deployment.md)
- [Changelog](docs/changelog.md)

## Production

Кратко:

- `DEBUG=False`;
- PostgreSQL через `DATABASE_URL`;
- Gunicorn systemd service;
- Nginx reverse proxy;
- HTTPS через Certbot;
- static в `STATIC_ROOT`;
- uploads в `MEDIA_ROOT`;
- регулярный backup через `scripts/server_backup.sh`.

Подробный порядок: [docs/deployment.md](docs/deployment.md).

## Публикация на GitHub

Если remote еще не задан:

```powershell
git remote add origin https://github.com/vdnrobo/roboprod.git
```

Первый push:

```powershell
git add .
git commit -m "Release v1.0.0"
git branch -M main
git push -u origin main
git tag v1.0.0
git push origin v1.0.0
```

Перед push проверьте:

```powershell
git status --short
git check-ignore .env db.sqlite3 media staticfiles *.tar.gz
```

## License

Лицензия пока не указана. Добавьте `LICENSE`, если проект должен быть публично переиспользуемым.
