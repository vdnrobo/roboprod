# Архитектура и сопровождение

## Технологии

- Python 3.13 в локальной среде.
- Django 5.x.
- SQLite для локальной разработки.
- PostgreSQL для production.
- Gunicorn + Nginx для deployment.
- WhiteNoise для static в production.
- Pillow для проверки изображений.
- Three.js 0.127.0 и STLLoader для браузерного рендера `.stl`.
- occt-import-js 0.0.23 с WASM для браузерного разбора `.stp`.
- Локальный canvas-рендерер DXF-примитивов для изображений `.dxf`.

## Структура проекта

```text
lab_production/        Django project: настройки, корневые URL, WSGI/ASGI.
production/            Основное Django-приложение производства.
templates/             HTML-шаблоны.
static/                CSS, JS, favicon и локально отдаваемые vendor-зависимости.
media/                 Локальные загруженные файлы.
docs/                  Документация проекта.
requirements.txt       Python-зависимости.
README.md              Краткая точка входа.
```

## Файлы верхнего уровня

- `.env` - локальные переменные окружения; не должен попадать в публичный репозиторий.
- `.env.example` - пример production-переменных.
- `.gitignore` - исключения для окружения, базы, media, staticfiles, кешей и логов.
- `db.sqlite3` - локальная база разработки.
- `manage.py` - Django CLI entrypoint.
- `requirements.txt` - зависимости Python.
- `runserver.out.log`, `runserver.err.log` - локальные логи фонового `runserver`.
- `README.md` - краткое описание и навигация по документации.
- `docs/` - документация пользователя, администратора, разработчика, deployment и changelog.

Сгенерированные директории:

- `.venv/` - локальное виртуальное окружение.
- `media/` - загруженные пользователями файлы.
- `staticfiles/` - результат `collectstatic`.
- `__pycache__/` - Python bytecode cache.

## Данные и модели

Главные сущности:

- `Material` - справочник материалов.
- `RejectionReason` - справочник стандартных причин отклонения.
- `Order` - заказ пользователя, включая номер ячейки выдачи для готовых заказов и признаки приоритетного 3D-заказа.
- `OrderDraft` - черновик незавершенного заказа.
- `OrderStatusLog` - журнал изменения статусов.
- `AuditLog` - аудит ключевых действий.
- `DutyPerson` - техническая модель дежурных, автоматически синхронизируемая с активными администраторами; связь с `User` позволяет считать персональные пропуски и замены.

Персональная статистика пользователей не хранится отдельными агрегатами. Страница `/admin-panel/users/<pk>/stats/` считает показатели из `Order`, `OrderStatusLog`, `AuditLog`, `DutyPerson` и `DutySkip` за выбранный период.

Ключевые enum-классы:

- `ProductionType`: `3d_print`, `laser_cut`.
- `MaterialProductionType`: `3d_print`, `laser_cut`, `both`.
- `OrderStatus`: `pending`, `in_progress`, `ready`, `done`, `cancelled`, `rejected`.

## Жизненный цикл заказа

1. Пользователь создает заказ через мастер.
2. Заказ получает статус `pending`.
3. Администратор переводит заказ в `in_progress`.
4. После изготовления администратор ставит `ready`.
5. Администратор при необходимости назначает ячейку выдачи 1-20. Один номер ячейки может быть указан у нескольких готовых заказов.
6. Пользователь подтверждает получение.
7. Система переводит заказ в `done`, очищает ячейку выдачи, логирует изменение и переносит заказ в архив.

Администратор не ставит `done` напрямую.

Приоритетный заказ - отдельный вариант 3D-заказа, который создает только администратор. У него есть общее `quantity` и `priority_remaining_quantity`. Когда администратор указывает, сколько копий поставлено на печать, система уменьшает `priority_remaining_quantity`; при первом списании ожидающий заказ переводится в `in_progress`.

## Файлы

Производственный файл сначала сохраняется через стандартный Django `FileField`, затем модель переименовывает его в:

```text
orders/files/{order_id}_{type}.{ext}
```

Примеры:

```text
orders/files/12_3d.stl
orders/files/13_laser.dxf
```

При скачивании используется отдельный view, который выставляет пользовательское имя:

```text
{order_id}_{type}_{last_name}_{first_name}_x{quantity}.{ext}
```

Изображение модели сохраняется в `orders/photos/`. Оно может быть загружено пользователем вручную или создано браузером из производственного файла в PNG 1024 x 768 на светлом фоне.

Временные файлы мастера заказа и массовой загрузки лежат в:

```text
media/tmp/order_wizard/{session_key}/
media/tmp/bulk_order/{session_key}/
```

Временные изображения автопревью лежат рядом с временными производственными файлами. Защищенные Django view отдают исходный файл только владельцу черновика или администратору, а PNG-превью принимается только через POST с CSRF и серверной проверкой изображения.

## Формы

Формы разделены по сценариям:

- регистрация;
- пошаговый мастер заказа;
- массовая загрузка;
- редактирование строк массовой загрузки;
- одиночное обновление заказа администратором;
- массовое обновление статусов;
- материалы.
- стандартные причины отклонения.
- фильтры пользовательских заказов;
- CSV-экспорт;
- черновики заказа.

Валидация форм дублируется модельной валидацией там, где это критично для целостности.

## Views и доступ

Все пользовательские страницы требуют авторизации, кроме регистрации и входа.

Административные страницы защищены `user_passes_test(is_admin)`. Исключение - производственная очередь: ее может открыть администратор или пользователь из Django-группы `semi_printer`. Для полупечатника queryset дополнительно ограничивается статусом `in_progress`, а мутирующее действие вынесено в отдельный POST-view перевода заказа в `ready`.

Доступ к файлам через download-view:

- владелец заказа может скачать свой файл;
- администратор может скачать любой файл;
- полупечатник может скачать файл только у заказа в статусе `В работе`;
- чужой пользователь получает `403`.

## Шаблоны и frontend

Базовый шаблон `templates/base.html` задает topbar, desktop-nav, mobile-nav, широкий центральный layout, левую колонку с графиком дежурства и закрепленным объявлением, правую панель отсчетов, footer и подключает CSS/JS.

На desktop админские разделы сгруппированы в dropdown-меню `Производство`, `Создание`, `Управление` и `Отчетность`. Для маленьких экранов используется выпадающий список разделов.

### Шаблоны

- `templates/base.html` - общий layout, favicon, brand, desktop/mobile navigation, pinned announcement, countdown panel, footer, messages и blocks.
- `templates/registration/login.html` - страница входа.
- `templates/registration/register.html` - регистрация пользователя.
- `templates/production/order_list.html` - главный экран пользователя, список заказов и напоминания о готовых к выдаче.
- `templates/production/order_type_select.html` - выбор типа производства.
- `templates/production/order_wizard.html` - пошаговое создание заказа: тип, название, производственный файл, изображение, материал/количество.
- `templates/production/order_form.html` - legacy-шаблон формы заказа, оставлен для совместимости.
- `templates/production/order_detail.html` - карточка заказа, файлы, статус, админская форма и история статусов.
- `templates/production/order_cancel_confirm.html` - подтверждение отмены заказа.
- `templates/production/admin_order_queue.html` - очередь, заказы к выдаче, архив, фильтры, одиночное и массовое изменение статусов.
- `templates/production/admin_dashboard.html` - dashboard администратора.
- `templates/production/audit_log_list.html` - аудит ключевых действий.
- `templates/production/bulk_order_form.html` - первый шаг массовой загрузки.
- `templates/production/bulk_order_edit.html` - редактирование строк массовой загрузки, включая изображение для каждой строки.
- `templates/production/material_list.html` - список групп материалов.
- `templates/production/material_table.html` - таблица материалов для одной группы.
- `templates/production/material_form.html` - создание/редактирование материала.
- `templates/production/material_confirm_delete.html` - подтверждение удаления материала.
- `templates/production/rejection_reason_list.html` - справочник стандартных причин отклонения.
- `templates/production/rejection_reason_form.html` - создание/редактирование причины отклонения.
- `templates/production/rejection_reason_confirm_delete.html` - подтверждение удаления причины.
- `templates/production/announcement_list.html` - список закрепляемых объявлений.
- `templates/production/announcement_form.html` - создание/редактирование объявления.
- `templates/production/announcement_confirm_delete.html` - подтверждение удаления объявления.
- `templates/production/info_panel.html` - объединенная страница объявлений и отсчетов.
- `templates/production/countdown_list.html` - список отсчетов.
- `templates/production/countdown_form.html` - создание/редактирование отсчета.
- `templates/production/countdown_confirm_delete.html` - подтверждение удаления отсчета.
- `templates/production/duty_slot_list.html` - список графиков, правил дней недели, справочник дежурных и список пропусков.
- `templates/production/duty_schedule_edit.html` - редактирование строк выбранного графика.
- `templates/production/duty_schedule_form.html` - создание/редактирование именованного графика.
- `templates/production/duty_schedule_confirm_delete.html` - подтверждение удаления графика.
- `templates/production/duty_schedule_rule_form.html` - создание/редактирование правила применения графика.
- `templates/production/duty_schedule_rule_confirm_delete.html` - подтверждение удаления правила применения графика.
- `templates/production/duty_slot_day_form.html` - legacy-шаблон редактирования расписания выбранного дня.
- `templates/production/duty_person_form.html` - legacy-шаблон ручного справочника дежурных, в рабочем интерфейсе больше не используется.
- `templates/production/duty_person_confirm_delete.html` - legacy-шаблон подтверждения удаления дежурного, в рабочем интерфейсе больше не используется.
- `templates/production/duty_slot_form.html` - создание/редактирование записи графика.
- `templates/production/duty_slot_confirm_delete.html` - подтверждение удаления записи графика.
- `templates/production/duty_skip_form.html` - создание/редактирование пропуска дежурства.
- `templates/production/duty_skip_confirm_delete.html` - подтверждение удаления пропуска дежурства.
- `templates/production/prefix_rule_list.html` - правила временных префиксов заказов.
- `templates/production/prefix_rule_form.html` - создание/редактирование правила префикса.
- `templates/production/prefix_rule_confirm_delete.html` - подтверждение удаления правила префикса.
- `templates/production/user_list.html` - пользователи, роли, дата регистрации, переход к статистике и удаление; данные выводятся от новых аккаунтов к старым.
- `templates/production/user_stats.html` - персональная статистика пользователя с фильтром периода.
- `templates/production/status_log_list.html` - журнал статусов.

JS:

- `static/js/nav.js` - переход по мобильному select и переключение светлой/темной темы.
- `static/js/photo-paste.js` - вставка фото из буфера обмена и предпросмотр.
- `static/js/model-render-worker.js` - загрузка временного производственного файла в Web Worker с тайм-аутом на стороне основного скрипта.
- `static/js/model-renderer.js` - рендер `.stl`, `.stp` и `.dxf` в PNG, расчет ориентировочных габаритов модели/чертежа, сохранение результата в защищенные preview endpoint и fallback к ручной загрузке.

### Static-файлы

- `static/css/app.css` - основной плоский дизайн без градиентов, светлая/темная темы, адаптивность, таблицы, формы, статусы, бренд и панели действий.
- `static/js/nav.js` - обработчик мобильного выпадающего меню и переключателя темы.
- `static/js/photo-paste.js` - обработчик `Ctrl+V` для фото и предпросмотра через blob URL.
- `static/js/model-render-worker.js` - worker для чтения временных файлов без блокировки интерфейса.
- `static/js/model-renderer.js` - браузерный рендерер автопревью заказов.
- `static/vendor/three/` - локальная копия Three.js и STLLoader.
- `static/vendor/occt/` - локальная копия occt-import-js и WASM.
- `static/favicon.ico` - favicon сайта и иконка рядом с брендом.

## Безопасность

Используются стандартные механизмы Django:

- CSRF protection;
- session auth;
- password validators;
- `login_required`;
- `user_passes_test`;
- model/form validation.

Дополнительно `SecurityHeadersMiddleware` выставляет:

- `X-Content-Type-Options`;
- `Referrer-Policy`;
- `Permissions-Policy`;
- `Content-Security-Policy`;
- `frame-ancestors 'none'`.

CSP разрешает только локальные скрипты, локальные worker и локальные fetch-запросы. Для `occt-import-js` добавлен `wasm-unsafe-eval`, иначе браузер не сможет инициализировать WASM-модуль.

Локальный режим HTTP задается через `.env` с `DEBUG=True`.

Production должен работать с `DEBUG=False`, secure-cookie и HTTPS.

## Тестирование

Основной набор тестов находится в `production/tests.py`.

Покрываются:

- регистрация и первый администратор;
- права администраторов;
- удаление пользователей;
- создание заказов;
- валидация файлов и размера;
- массовая загрузка;
- очередь и архив;
- dashboard и CSV-экспорт;
- черновики и повтор заказа;
- аудит действий;
- массовая смена статусов;
- отклонение с причиной;
- подтверждение получения;
- хранение последних 30 выполненных заказов;
- security headers.

Команды:

```powershell
.\.venv\Scripts\python manage.py check
.\.venv\Scripts\python manage.py test
```
