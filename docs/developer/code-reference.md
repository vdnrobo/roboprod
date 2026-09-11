# Справочник кода

## Изменения v0.6.18

- `production.models.OrderMessage` - модель диалога по заказу: хранит заказ, автора, текст, фото и дату создания.
- `production.models.OrderDraft.is_priority` - сохраняет флаг приоритетного заказа между шагом деталей и финальным подтверждением.
- `production.models.format_model_dimensions(width, depth, height)` - общий форматтер ориентировочных размеров для заказа и черновика.
- `production.forms.validate_image_size(upload, max_size)` - проверяет лимит изображения сообщения, по умолчанию 10 МБ.
- `production.forms.OrderMessageForm` - форма сообщения в диалоге; требует текст или фото и валидирует фото как изображение.
- `production.views.order_message_create(request, pk)` - принимает POST-сообщение, проверяет владельца/админа и закрывает отправку для неактивных статусов.
- `production.views.admin_order_quick_status(request, pk)` - мобильная быстрая смена статуса заказа с журналом статусов и аудитом.
- `production.views.system_health(request)` - админская страница здоровья системы: диск, `media`, `staticfiles`, БД и последний backup.
- `production.views.directory_stats(path)`, `human_size(size)`, `latest_backup_info()` - вспомогательные функции для страницы здоровья системы.
- `templates/production/order_wizard.html` - мастер заказа получил финальный шаг подтверждения; заказ создается только после POST на этом шаге.
- `templates/production/order_detail.html` - добавлен диалог по заказу под основными параметрами.
- `templates/production/admin_order_queue.html` - добавлены мобильные быстрые действия `В работу`, `Готов`, `Отклонить`.
- `templates/production/system_health.html` - новый шаблон страницы здоровья системы.

## `manage.py`

- `main()` - стандартная точка входа Django CLI. Устанавливает `DJANGO_SETTINGS_MODULE=lab_production.settings` и вызывает `execute_from_command_line`.

## `lab_production/settings.py`

- `env_bool(name, default=False)` - читает boolean-переменную окружения. Значения `1`, `true`, `yes`, `on` считаются истинными.

Файл также задает:

- загрузку `.env`;
- Django apps и middleware;
- SQLite/PostgreSQL через `DATABASE_URL`;
- static/media paths;
- upload limits;
- `ENABLE_PAGE_TRANSITIONS` - флаг подключения JS-переходов страниц, по умолчанию `False`;
- security flags для local/production режимов.

## `lab_production/urls.py`

- `urlpatterns` - подключает Django admin по `/django-admin/` и URL приложения `production`.
- В `DEBUG=True` добавляет отдачу `MEDIA_URL` через Django.

## `lab_production/wsgi.py` и `lab_production/asgi.py`

- Стандартные entrypoints для WSGI/ASGI.
- Production systemd/Gunicorn использует `lab_production.wsgi:application`.

## `production/apps.py`

- `ProductionConfig` - конфигурация Django-приложения `production`.

## `production/models.py`

### Enum-классы

- `ProductionType` - тип производства: `PRINT_3D`, `LASER_CUT`.
- `MaterialProductionType` - применимость материала: 3D, лазер или оба типа.
- `OrderStatus` - статусы заказа: `PENDING`, `IN_PROGRESS`, `READY`, `DONE`, `CANCELLED`, `REJECTED`.

### Константы и функции

- `VALID_EXTENSIONS` - допустимые расширения по типу производства.
- `MAX_UPLOAD_SIZE` - лимит файла 50 МБ.
- `ORDER_TITLE_MAX_LENGTH` - лимит названия заказа 30 символов.
- `PICKUP_CELL_MIN`, `PICKUP_CELL_MAX`, `PICKUP_CELL_CHOICES` - диапазон и варианты ячеек выдачи 1-20.
- `SEMI_PRINTER_GROUP` - имя Django-группы ограниченной роли полупечатника.
- `user_is_semi_printer(user)` - проверяет, что пользователь авторизован, не является администратором и состоит в группе `semi_printer`.
- `order_file_upload_to(instance, filename)` - временный путь для исходного производственного файла.
- `order_photo_upload_to(instance, filename)` - путь для фото модели.
- `order_draft_file_upload_to(instance, filename)` - путь файла черновика.
- `order_draft_photo_upload_to(instance, filename)` - путь фото черновика.

### `Material`

- Поля: `name`, `color`, `production_type`, `is_active`, `created_at`.
- `__str__()` - возвращает название материала.
- `supports(production_type)` - проверяет, подходит ли материал для типа производства.

### `RejectionReason`

- Поля: `text`, `is_active`, `created_at`.
- `__str__()` - возвращает текст стандартной причины.

### `Order`

- Поля: пользователь, название, тип производства, материал, количество, фото, файл, ориентировочные размеры модели, комментарий, admin comment, причина отклонения, ячейка выдачи, правило префикса, текст префикса, номер в префиксе, признак приоритетного заказа, остаток копий к запуску, статус, даты.
- `__str__()` - человекочитаемое имя `#{display_number} {title}`.
- `public_number` - публичный номер заказа.
- `display_number` - возвращает `prefix_text + prefix_number` или внутренний `pk`, если префикса нет.
- `file_suffix` - возвращает `3d` или `laser` для имени файла.
- `priority_started_quantity` - вычисляет, сколько копий приоритетного заказа уже поставлено на печать.
- `has_model_dimensions` - проверяет, есть ли сохраненные ориентировочные габариты модели.
- `model_dimensions_display` - форматирует габариты для админского интерфейса в виде `ширина x глубина x высота мм`; для DXF без высоты показывает две величины.
- `register_priority_print_batch(copies)` - списывает партию копий из остатка приоритетного заказа, переводит `pending` в `in_progress`, а при нулевом остатке переводит заказ в `done`.
- `assign_active_prefix()` - присваивает новому заказу активное правило префикса и следующий номер отдельной последовательности.
- `clean()` - валидирует количество, совместимость материала, размер и расширение файла, диапазон ячейки выдачи и правила приоритетных 3D-заказов.
- `save()` - вызывает `full_clean()`, сохраняет объект и переименовывает производственный файл.
- `_rename_production_file()` - переносит файл в `orders/files/{id}_{type}.{ext}` и удаляет исходный временный файл.

### `OrderStatusLog`

- Поля: заказ, номер заказа, название, кто изменил, старый статус, новый статус, причина, дата.
- `__str__()` - строка вида `#{order_number}: old -> new`.

### `OrderDraft`

- Поля: пользователь, тип, название, материал, количество, фото, файл, ориентировочные размеры модели, комментарий, текущий шаг, даты.
- Используется для сохранения незавершенного мастера заказа.

### `AuditLog`

- Поля: actor, action, target_type, target_id, target_label, changes, created_at.
- Хранит ключевые действия пользователей и администраторов.

### `PinnedAnnouncement`

- Поля: заголовок, текст, признак закрепления, даты создания и обновления.
- Используется для вывода одного активного объявления слева от основного окна сайта.

### `Countdown`

- Поля: название, дата окончания, активность, даты создания и обновления.
- `days_left` - количество дней от текущей локальной даты до даты окончания.
- Активные отсчеты выводятся справа от основного окна сайта.

### `ProductionSettings`

- Поля: `allow_self_pickup`, `updated_at`.
- `get_solo()` - возвращает единственную строку настроек производства, создавая ее при первом обращении.
- `save()` - принудительно сохраняет объект с `pk=1`, чтобы в базе оставалась одна запись настроек.
- Используется для включения и отключения пользовательского самосъёма 3D-заказов.

### `DutyPerson`

- Поля: опциональная связь `user` с аккаунтом Django, имя, фамилия, активность, даты создания и обновления.
- `full_name` - отображаемое ФИ дежурного.
- Используется как техническая прослойка для графиков и замен; записи автоматически синхронизируются из активных администраторов.
- `admin_duty_people_queryset()` - синхронизирует `DutyPerson` с активными `is_staff`-аккаунтами и возвращает queryset доступных администраторов-дежурных.

### `DutySchedule`

- Поля: название графика, активность, даты создания и обновления.
- Содержит строки `DutySlot` и используется правилами дней недели.

### `DutySlot`

- Поля: резервный день недели, ссылка на график, ссылка на дежурного, резервные имя/фамилия, время разгрузки очереди, активность, даты создания и обновления.
- `full_name`, `display_last_name`, `display_first_name` - отображают ФИ из `DutyPerson`, либо резервные поля старых записей.
- `clean()` требует выбранного дежурного или резервные ФИ.
- `save()` синхронизирует резервные ФИ из выбранного `DutyPerson`.
- Активные записи текущего дня выводятся слева от основного окна только администраторам.

### `DutyScheduleRule`

- Поля: день недели, график, активность, даты создания и обновления.
- Связывает день недели с именованным графиком, который используется по умолчанию в этот день.

### `DutySkip`

- Поля: дежурный, дата пропуска, время пропуска, причина, заменяющий из справочника, резервный заменяющий-строка графика, даты создания и обновления.
- `clean()` запрещает выбирать того же дежурного заменяющим и требует причину пропуска.
- `replacement_full_name` - отображаемое ФИ заменяющего.
- Используется для разовых замен в боковом графике дежурства.

### `OrderPrefixRule`

- Поля: префикс, начало периода, конец периода, следующий номер, активность, даты создания и обновления.
- `is_current` - показывает, действует ли правило на текущую локальную дату.
- Используется при создании заказа для отдельной публичной нумерации внутри периода.

### Служебные функции

- `prune_completed_orders(keep=30)` - оставляет только последние `keep` заказов в статусе `DONE`.
- `delete_order_files(sender, instance, **kwargs)` - post-delete signal, удаляет фото и производственный файл при удалении заказа.
- `delete_order_draft_files(sender, instance, **kwargs)` - удаляет файлы черновика при удалении черновика.

## `production/forms.py`

- `format_size(size)` - форматирует байты в мегабайты.
- `validate_file_size(upload)` - проверяет лимит 50 МБ.
- `RegisterForm` - регистрация пользователя с обязательными фамилией и именем.
- `OrderCreateForm` - legacy/model form для создания заказа и тестов; валидирует материал и файл.
- `OrderTitleStepForm` - шаг названия заказа.
- `OrderFileStepForm` - шаг производственного файла с фильтрацией расширений.
- `OrderImageStepForm` - шаг изображения модели: принимает ручное изображение или PNG, созданный браузерным рендерером.
- `OrderDetailsStepForm` - шаг материала, количества и комментария; для администратора при 3D-печати показывает флаг приоритетного заказа.
- `PriorityPrintBatchForm` - форма списания количества копий, поставленных на печать по приоритетному заказу.
- `OrderFilterForm` - фильтры страницы `Мои заказы`.
- `MultiFileInput` - file input с поддержкой multiple.
- `MultiFileField.clean()` - нормализует один или несколько файлов в список.
- `BulkOrderCreateForm.clean_files()` - валидирует пачку файлов массовой загрузки.
- `BulkOrderItemForm` - форма одной строки массовой загрузки.
- `BulkOrderItemForm.clean_title()` - чистит и валидирует название.
- `BulkOrderItemForm.clean_material()` - проверяет материал строки.
- `AdminOrderUpdateForm` - одиночное изменение заказа администратором.
- `AdminOrderUpdateForm.clean()` - требует стандартную или ручную причину отклонения, запрещает прямой перевод активного заказа в `DONE` и очищает ячейку вне статуса `READY`.
- `AdminBulkStatusUpdateForm` - массовое изменение статусов.
- `AdminBulkStatusUpdateForm.clean_rejection_reason()` - чистит причину отклонения.
- `AdminBulkStatusUpdateForm.clean()` - требует стандартную или ручную причину при статусе `REJECTED`.
- `SemiPrinterMarkReadyForm` - ограниченная форма полупечатника для выбора ячейки выдачи при переводе заказа в `READY`.
- `MaterialForm` - создание и редактирование материала, включая цвет.
- `RejectionReasonForm` - создание и редактирование стандартной причины отклонения.
- `PinnedAnnouncementForm` - создание и редактирование закрепленного объявления.
- `CountdownForm` - создание и редактирование отсчета в днях.
- `ProductionSettingsForm` - редактирование общей настройки самосъёма.
- `DutySlotForm` - создание и редактирование записи графика дежурства.
- `DutySlotScheduleFormSet` - редактирование строк выбранного графика.
- `DutyScheduleForm` - создание и редактирование именованного графика.
- `DutyScheduleRuleForm` - создание и редактирование правила `день недели -> график`.
- `DutyPersonForm` - legacy-форма ручного справочника; в рабочем интерфейсе больше не используется, дежурные берутся из администраторов.
- `DutySkipForm` - создание и редактирование пропуска дежурства с заменяющим; время пропуска автоматически берет из выбранного `DutySlot`.
- `OrderPrefixRuleForm` - создание и редактирование временного правила префикса заказа.
- `production_type_for_filename(filename)` - определяет тип производства по расширению файла.

## `production/views.py`

### Проверки и helpers

- `is_admin(user)` - проверяет `is_authenticated` и `is_staff`.
- `is_semi_printer(user)` - проверяет ограниченную роль через группу `semi_printer`.
- `can_access_production_queue(user)` - разрешает производственную очередь администраторам и полупечатникам.
- `can_mark_ready(order, user)` - проверяет право перевести заказ из `IN_PROGRESS` в `READY`.
- `material_groups()` - группирует материалы по типу применения.
- `first_user_id()` - возвращает id самого первого пользователя.
- `can_customer_cancel(order, user)` - можно ли заказчику отменить заказ.
- `can_customer_confirm_receipt(order, user)` - можно ли подтвердить получение.
- `can_customer_self_pickup(order, user, settings_obj=None)` - можно ли заказчику подтвердить самосъём: владелец заказа, 3D-печать, статус `IN_PROGRESS`, настройка включена.
- `log_status_change(...)` - создает запись `OrderStatusLog`.
- `log_audit(...)` - создает запись аудита.
- `local_datetime_start(day)` - возвращает начало локального дня как timezone-aware datetime.
- `parse_user_stats_period(params)` - разбирает период персональной статистики пользователя.
- `apply_period(qs, field_name, period)` - применяет datetime-границы периода к queryset.
- `apply_date_period(qs, field_name, period)` - применяет date-границы периода к queryset.
- `format_duration(seconds)` - форматирует среднюю длительность в дни/часы/минуты.
- `average_order_completion_seconds(orders)` - считает среднее время от создания заказа до первого перехода в `READY` или `DONE`.
- `parse_model_dimensions(post_data)` - принимает размеры автопревью из POST, проверяет конечные положительные числа и округляет их до десятых.
- `apply_model_dimensions(target, dimensions)` - переносит рассчитанные размеры в `Order` или `OrderDraft`.
- `clear_model_dimensions(target)` - очищает размеры при ручной замене изображения или производственного файла.
- `current_draft(request)` - возвращает текущий черновик мастера из session.
- `ensure_session_key(request)` - гарантирует наличие session key.
- `temp_dir(request, kind)` - директория временных файлов для session/kind.
- `temp_file_path(request, kind, stored_name)` - путь к временному файлу.
- `delete_temp_file(request, kind, file_info)` - удаляет один временный файл.
- `clear_temp_area(request, kind)` - удаляет временную директорию.
- `clear_order_wizard(request)` - очищает мастер заказа.
- `clear_bulk_order(request)` - очищает массовую загрузку.
- `save_temp_upload(request, kind, upload)` - сохраняет upload во временную директорию.
- `order_type_cards()` - данные карточек выбора типа производства.
- `next_order_wizard_step(wizard)` - определяет следующий незаполненный шаг мастера.
- `normalize_order_step(step, wizard)` - не дает перейти вперед, если предыдущие шаги не заполнены.
- `default_bulk_title(filename)` - берет имя файла без расширения и обрезает до 30 символов.
- `safe_filename_part(value)` - чистит часть имени скачиваемого файла.
- `order_export_filename(order)` - формирует имя скачиваемого производственного файла с ФИ заказчика и количеством изделий.

### Пользовательские views

- `order_wizard_photo_preview(request)` - отдает временное изображение мастера текущей session.
- `order_wizard_source_file(request)` - отдает текущий временный производственный файл мастера для браузерного рендера; доступен только владельцу session/черновика.
- `order_wizard_preview_save(request)` - принимает PNG/изображение автопревью мастера через POST с CSRF, проверяет его как изображение, сохраняет во временные файлы/черновик и записывает ориентировочные размеры модели.
- `order_file_download(request, pk)` - защищенное скачивание производственного файла.
- `register(request)` - регистрация; первый пользователь становится администратором.
- `order_list(request)` - список заказов пользователя и напоминания о готовых к выдаче.
- `order_detail(request, pk)` - страница заказа с правами доступа.
- `order_repeat(request, pk)` - создает повтор заказа с копией файлов.
- `order_cancel(request, pk)` - отмена собственного заказа в статусе `PENDING`.
- `order_cancel_confirm(request, pk)` - страница подтверждения отмены.
- `order_confirm_receipt(request, pk)` - подтверждение получения и перевод в `DONE`.
- `order_self_pickup(request, pk)` - подтверждение самосъёма 3D-заказа пользователем, перевод в `DONE`, запись журнала статусов и аудита.
- `order_create(request)` - пошаговый мастер создания заказа.
- `order_draft_continue(request, pk)` - продолжение черновика заказа.
- `order_draft_delete(request, pk)` - удаление черновика.

### Административные views

- `admin_order_queue(request)` - активная очередь; для администратора показывает `Ожидает` и `В работе`, для полупечатника только `В работе`.
- `admin_order_ready(request)` - отдельный список заказов в статусе `Готов к выдаче`.
- `admin_order_archive(request)` - архив закрытых заказов.
- `admin_order_list(request, list_kind="queue")` - общая реализация очереди, списка к выдаче и архива.
- `production_order_mark_ready(request, pk)` - ограниченный POST-view для полупечатника: переводит заказ `IN_PROGRESS` в `READY`, назначает ячейку выдачи, пишет журнал статуса и аудит.
- `admin_order_export(request, list_kind="queue")` - CSV-экспорт списка заказов.
- `admin_dashboard(request)` - dashboard администратора.
- `audit_log_list(request)` - список событий аудита.
- `admin_order_update(request, pk)` - одиночное обновление статуса/материала/причины/ячейки выдачи.
- `admin_order_priority_print_batch(request, pk)` - списывает поставленные на печать копии из остатка приоритетного заказа.
- `admin_orders_bulk_status_update(request)` - массовое изменение статусов, освобождает ячейки при уходе из статуса `READY`.
- `bulk_order_item_from_session(request, token)` - ищет строку массовой загрузки по токену во временной session.
- `bulk_order_source_file(request, token)` - отдает временный производственный файл строки массовой загрузки для браузерного рендера; доступен только администраторам.
- `bulk_order_preview_save(request, token)` - принимает PNG/изображение автопревью строки массовой загрузки и сохраняет его вместе с ориентировочными размерами во временную session.
- `bulk_order_preview_file(request, token)` - отдает сохраненное временное изображение строки массовой загрузки.
- `bulk_order_create(request)` - двухэтапная массовая загрузка с редактированием названия, изображения, материала и количества по каждой строке.
- `material_list(request)` - список материалов.
- `material_create(request)` - создание материала.
- `material_update(request, pk)` - редактирование материала.
- `material_delete(request, pk)` - полное удаление материала.
- `rejection_reason_list(request)` - список стандартных причин отклонения.
- `rejection_reason_create(request)` - создание причины отклонения.
- `rejection_reason_update(request, pk)` - редактирование причины отклонения.
- `rejection_reason_delete(request, pk)` - удаление причины отклонения.
- `announcement_list(request)` - список объявлений.
- `announcement_create(request)` - создание закрепляемого объявления; при активном объявлении отключает остальные.
- `announcement_update(request, pk)` - редактирование объявления; при активном объявлении отключает остальные.
- `announcement_delete(request, pk)` - удаление объявления.
- `info_panel(request)` - объединенная страница объявлений, отсчетов и общей настройки самосъёма.
- `countdown_list(request)` - список отсчетов.
- `countdown_create(request)` - создание отсчета.
- `countdown_update(request, pk)` - редактирование отсчета.
- `countdown_delete(request, pk)` - удаление отсчета.
- `duty_slot_list(request)` - список графиков, правил, дежурных и пропусков.
- `duty_weekday_or_404(weekday)` - валидирует номер дня недели.
- `duty_slot_day_update(request, weekday)` - legacy-переход к графику, назначенному правилом для дня.
- `duty_schedule_create(request)` - создание именованного графика.
- `duty_schedule_update(request, pk)` - редактирование настроек графика.
- `duty_schedule_delete(request, pk)` - удаление графика, если он не защищен правилами.
- `duty_schedule_edit(request, pk)` - редактирование строк выбранного графика.
- `duty_schedule_rule_create(request)` - создание правила применения графика.
- `duty_schedule_rule_update(request, pk)` - редактирование правила применения графика.
- `duty_schedule_rule_delete(request, pk)` - удаление правила применения графика.
- `duty_person_create(request)` - legacy URL, синхронизирует администраторов-дeжурных и возвращает в раздел графика.
- `duty_person_update(request, pk)` - legacy URL, сообщает, что ФИ меняются через аккаунт пользователя.
- `duty_person_delete(request, pk)` - legacy URL, сообщает, что дежурный удаляется снятием прав администратора.
- `duty_slot_create(request)` - создание записи графика.
- `duty_slot_update(request, pk)` - редактирование записи графика.
- `duty_slot_delete(request, pk)` - удаление записи графика.
- `duty_skip_create(request)` - создание пропуска дежурства.
- `duty_skip_update(request, pk)` - редактирование пропуска дежурства.
- `duty_skip_delete(request, pk)` - удаление пропуска дежурства.
- `prefix_rule_list(request)` - список правил префиксов заказов.
- `prefix_rule_create(request)` - создание правила префикса.
- `prefix_rule_update(request, pk)` - редактирование правила префикса.
- `prefix_rule_delete(request, pk)` - удаление правила префикса.
- `prefix_rule_delete_orders(request, pk)` - удаляет все заказы с `prefix_text`, равным префиксу правила.
- `user_stats(request, pk)` - персональная статистика аккаунта для администраторов: заказы, самосъемы, подтверждения, повторы, материалы, типы производства, админские действия, аудит и дежурства за выбранный период.
- `user_list(request)` - список пользователей, отсортированный по `date_joined` от новых к старым.
- `user_admin_toggle(request, pk)` - назначение или снятие администратора.
- `user_semi_printer_toggle(request, pk)` - назначение или снятие роли полупечатника через группу `semi_printer`; запрещает назначение первому пользователю и администраторам.
- `user_delete(request, pk)` - удаление пользователя.
- `user_bulk_delete(request)` - массовое удаление выбранных пользователей, кроме первого пользователя и текущей учетной записи.
- `status_log_list(request)` - журнал последних изменений статусов.

## `production/urls.py`

`urlpatterns` содержит маршруты регистрации, входа, заказов, скачивания файлов, временных файлов автопревью, подтверждения получения, самосъёма и административных разделов.

Ключевые URL:

- `/orders/`
- `/orders/new/`
- `/orders/new/source-file/`
- `/orders/new/preview/`
- `/orders/drafts/<pk>/continue/`
- `/orders/drafts/<pk>/delete/`
- `/orders/<pk>/repeat/`
- `/orders/<pk>/file/`
- `/orders/<pk>/cancel/confirm/`
- `/orders/<pk>/confirm-receipt/`
- `/orders/<pk>/self-pickup/`
- `/production/orders/<pk>/mark-ready/`
- `/admin-panel/dashboard/`
- `/admin-panel/orders/`
- `/admin-panel/orders/export/`
- `/admin-panel/orders/ready/export/`
- `/admin-panel/orders/archive/export/`
- `/admin-panel/bulk/`
- `/admin-panel/bulk/files/<token>/`
- `/admin-panel/bulk/previews/<token>/`
- `/admin-panel/bulk/previews/<token>/image/`
- `/admin-panel/announcements/`
- `/admin-panel/info/`
- `/admin-panel/countdowns/`
- `/admin-panel/duty/`
- `/admin-panel/duty/day/<weekday>/`
- `/admin-panel/duty/schedules/new/`
- `/admin-panel/duty/schedules/<pk>/`
- `/admin-panel/duty/schedules/<pk>/edit/`
- `/admin-panel/duty/schedules/<pk>/delete/`
- `/admin-panel/duty/rules/new/`
- `/admin-panel/duty/rules/<pk>/`
- `/admin-panel/duty/rules/<pk>/delete/`
- `/admin-panel/duty/people/new/`
- `/admin-panel/duty/people/<pk>/`
- `/admin-panel/duty/people/<pk>/delete/`
- `/admin-panel/duty/skips/new/`
- `/admin-panel/duty/skips/<pk>/`
- `/admin-panel/duty/skips/<pk>/delete/`
- `/admin-panel/prefix-rules/`
- `/admin-panel/prefix-rules/<pk>/delete-orders/`
- `/admin-panel/orders/archive/`
- `/admin-panel/orders/bulk-status/`
- `/admin-panel/orders/<pk>/priority-print/`
- `/admin-panel/materials/`
- `/admin-panel/rejection-reasons/`
- `/admin-panel/users/`
- `/admin-panel/users/bulk-delete/`
- `/admin-panel/users/<pk>/semi-printer/`
- `/admin-panel/users/<pk>/stats/`
- `/admin-panel/status-log/`
- `/admin-panel/audit/`

## `static/js` и `static/vendor`

- `static/js/nav.js` - мобильное меню, сохранение состояния свернутых боковых блоков и переключение темы.
- `static/js/page-transitions.js` - опциональный скрипт сдержанных CSS-переходов для безопасных внутренних GET-ссылок; подключается только при `ENABLE_PAGE_TRANSITIONS=True`.
- `static/js/bulk-order-edit.js` - быстро проставляет выбранный материал во все совместимые строки массовой загрузки.
- `static/js/photo-paste.js` - ручная вставка изображения через `Ctrl+V` в мастере заказа и строках массовой загрузки.
- `static/js/model-render-worker.js` - чтение временного производственного файла в Web Worker, чтобы тяжелая загрузка не блокировала интерфейс.
- `static/js/model-renderer.js` - рендерит `.stl`, `.stp` и `.dxf` в PNG 1024 x 768, считает ориентировочные габариты по bounding box, сохраняет результат через защищенный endpoint и показывает fallback к ручной загрузке.
- `static/vendor/three/` - локально отдаваемые Three.js 0.127.0 и STLLoader.
- `static/vendor/occt/` - локально отдаваемые occt-import-js 0.0.23 и WASM.

## `production/admin.py`

- `MaterialAdmin` - настройки стандартной Django-админки для материалов.
- `RejectionReasonAdmin` - настройки Django-админки для стандартных причин отклонения.
- `PinnedAnnouncementAdmin` - настройки Django-админки для закрепленных объявлений.
- `CountdownAdmin` - настройки Django-админки для отсчетов.
- `ProductionSettingsAdmin` - настройки Django-админки для общей singleton-настройки производства.
- `DutyPersonAdmin` - настройки Django-админки для справочника дежурных.
- `DutyScheduleAdmin` - настройки Django-админки для именованных графиков.
- `DutyScheduleRuleAdmin` - настройки Django-админки для правил применения графиков.
- `DutySlotAdmin` - настройки Django-админки для графика дежурства.
- `DutySkipAdmin` - настройки Django-админки для пропусков и замен дежурства.
- `OrderPrefixRuleAdmin` - настройки Django-админки для правил префиксов.
- `OrderAdmin` - настройки Django-админки для заказов.
- `OrderStatusLogAdmin` - read-only представление журнала статусов.
- `OrderDraftAdmin` - представление черновиков.
- `AuditLogAdmin` - read-only представление аудита.

## `production/context_processors.py`

- `pinned_announcement(request)` - добавляет в шаблоны флаг `page_transitions_enabled`, последнее активное закрепленное объявление, список активных отсчетов и активный график дежурства текущего дня для админов; выбирает график через правило дня недели, применяет разовые пропуски и замены.

## `production/middleware.py`

- `SecurityHeadersMiddleware.__init__(get_response)` - сохраняет следующий middleware/view.
- `SecurityHeadersMiddleware.__call__(request)` - добавляет security headers и CSP для всех путей, кроме `/django-admin/`.

## `scripts/server_backup.sh`

- Серверный bash-скрипт backup.
- Делает консистентную копию SQLite через Python `sqlite3.backup()` или PostgreSQL через `pg_dump`.
- Архивирует `media/`, `.env` и код без `.venv`, `db.sqlite3`, `media/`, `staticfiles/`.
- Складывает результат в `/opt/lab-production-backups/{YYYYMMDD-HHMMSS}`.
- Удаляет backup-директории старше `KEEP_DAYS`, по умолчанию 30 дней.

## `production/tests.py`

- `upload(...)` - тестовый production-файл.
- `upload_with_size(...)` - upload с заданным размером.
- `image_upload()` - минимальное GIF-изображение для тестов.
- `ProductionTests` - интеграционный набор тестов приложения.
- `tearDownClass()` - очищает временный `MEDIA_ROOT`.
- `setUp()` - создает тестовые материалы.

Тестовые методы покрывают регистрацию, роли, пользователей, материалы, создание заказов, очереди, архив, массовые операции, безопасность и жизненный цикл статусов.

## Миграции

- `0001_initial.py` - базовые модели.
- `0002_seed_materials.py` - стартовые материалы.
  - `seed_materials(apps, schema_editor)` - создает начальные материалы PLA, PETG, ABS, фанеру, акрил и картон.
- `0003_order_rejection_reason_alter_order_status_and_more.py` - отклонение и журнал статусов.
- `0004_alter_order_material.py` - материал заказа становится nullable при удалении материала.
- `0005_limit_order_title_length.py` - лимит названия 30 символов с предварительным обрезанием старых данных.
  - `truncate_existing_titles(apps, schema_editor)` - обрезает существующие названия заказов и записей журнала до 30 символов перед изменением схемы.
- `0006_order_ready_status.py` - статус `ready` и переименование отображения `done`.
- `0007_rejectionreason.py` - справочник стандартных причин отклонения.
- `0008_material_color_order_admin_comment_auditlog_and_more.py` - цвет материалов, admin comment, черновики и аудит.
- `0009_order_pickup_cell_and_more.py` - ячейка выдачи 1-20 и изначальное ограничение уникальности среди готовых заказов.
- `0010_remove_order_unique_ready_order_pickup_cell.py` - снимает уникальность ячейки выдачи, чтобы в одной ячейке могли лежать несколько заказов.
- `0011_order_is_priority_order_priority_remaining_quantity.py` - добавляет приоритетные 3D-заказы и остаток копий к запуску.
- `0012_pinnedannouncement.py` - добавляет закрепленные объявления.
- `0013_countdown.py` - добавляет отсчеты в днях.
- `0014_orderprefixrule_order_prefix_number_and_more.py` - добавляет временные правила префикса и поля публичного номера заказа.
- `0015_dutyslot.py` - добавляет график дежурства.
- `0016_alter_dutyslot_options_dutyslot_weekday_dutyskip.py` - добавляет дни недели в график и пропуски дежурства с заменами.
- `0017_dutyperson_alter_dutyslot_options_and_more.py` - добавляет справочник дежурных, переносит существующие ФИ в него и переводит расписание/замены на выбор из справочника.
- `0018_dutyschedule_alter_dutyslot_options_and_more.py` - добавляет именованные графики, правила применения по дням недели и переносит старые weekday-строки в графики с правилами.
- `0019_productionsettings.py` - добавляет singleton-модель общих настроек производства с флагом самосъёма.
- `0020_dutyperson_user.py` - добавляет связь дежурного с аккаунтом пользователя для персональной статистики пропусков и замен.
- `0021_order_model_depth_order_model_height_and_more.py` - добавляет ориентировочные размеры модели/чертежа к заказу и черновику.
- `0022_orderdraft_is_priority_ordermessage.py` - добавляет признак приоритетного черновика и сообщения диалога по заказу.
- `0023_seed_semi_printer_group.py` - создает Django-группу `semi_printer` для ограниченной роли полупечатника.

## Тестовые функции `production/tests.py`

- `test_first_registered_user_becomes_admin()` - проверяет автоматическое назначение первого администратора.
- `test_registration_requires_first_and_last_name()` - проверяет обязательность фамилии и имени.
- `test_admin_can_promote_user()` - проверяет назначение администратора.
- `test_admin_can_toggle_semi_printer_role()` - проверяет назначение/снятие роли полупечатника и снятие этой роли при повышении до администратора.
- `test_user_list_orders_newest_accounts_first()` - проверяет сортировку пользователей от новых к старым.
- `test_user_stats_accessible_only_to_admins()` - проверяет доступ к персональной статистике только для администраторов.
- `test_user_stats_counts_user_metrics_and_period()` - проверяет пользовательские метрики статистики и фильтр периода.
- `test_admin_stats_counts_status_audit_and_duty_metrics()` - проверяет админские, аудиторские и дежурные метрики статистики.
- `test_admin_can_manage_pinned_announcement()` - проверяет создание закрепленного объявления и автоматическое отключение предыдущего активного.
- `test_first_user_admin_rights_cannot_be_removed()` - проверяет защиту первого администратора.
- `test_admin_can_delete_user_but_not_first_or_self()` - проверяет удаление пользователя и запреты для первого/текущего.
- `test_admin_can_bulk_delete_users_but_not_first_or_self()` - проверяет массовое удаление пользователей с защитой первого и текущего аккаунта.
- `test_order_form_accepts_only_matching_extensions()` - проверяет расширения и лимит названия.
- `test_order_create_starts_with_type_choice_and_filters_materials()` - проверяет мастер заказа, фильтр материалов, preview и пустой материал.
- `test_only_admin_can_mark_new_3d_order_as_priority()` - проверяет, что приоритетный заказ может отметить только администратор.
- `test_admin_can_create_priority_order_from_wizard()` - проверяет создание приоритетного заказа через мастер.
- `test_priority_print_batch_reduces_remaining_quantity()` - проверяет списание копий из остатка приоритетного заказа.
- `test_priority_order_done_when_remaining_quantity_reaches_zero()` - проверяет автоматический перевод приоритетного заказа в `DONE` при нулевом остатке.
- `test_active_prefix_rule_assigns_separate_public_numbers_to_new_orders()` - проверяет активное правило префикса и отдельный счетчик.
- `test_admin_can_delete_all_orders_with_prefix()` - проверяет удаление всех заказов с выбранным префиксом.
- `test_admin_can_manage_countdown_and_layout_shows_it()` - проверяет создание отсчета и вывод правой панели в layout.
- `test_admin_can_manage_duty_schedule_and_admin_layout_shows_it()` - проверяет график дежурства и его вывод только администраторам.
- `test_order_file_size_limit_is_50_mb()` - проверяет лимит размера файла.
- `test_order_file_is_renamed_after_save()` - проверяет переименование файла после сохранения.
- `test_admin_order_detail_shows_customer_full_name()` - проверяет ФИ заказчика на странице заказа для администратора.
- `test_order_file_download_name_includes_customer_full_name()` - проверяет имя скачиваемого файла и запрет чужого доступа.
- `test_customer_can_cancel_own_pending_order()` - проверяет отмену собственного ожидающего заказа.
- `test_customer_cannot_cancel_other_in_progress_or_finished_order()` - проверяет запреты отмены.
- `test_admin_queue_defaults_to_oldest_active_orders_and_archive_is_separate()` - проверяет очередь, архив, сортировку и фильтр заказчика.
- `test_semi_printer_sees_only_in_progress_queue()` - проверяет ограниченный интерфейс очереди полупечатника.
- `test_semi_printer_can_only_mark_in_progress_order_ready()` - проверяет серверную защиту перевода заказа полупечатником в `READY`.
- `test_admin_navigation_is_grouped()` - проверяет группировку админской навигации.
- `test_admin_can_bulk_update_order_statuses()` - проверяет массовое изменение статусов.
- `test_admin_can_bulk_reject_with_standard_reason()` - проверяет массовое отклонение со стандартной причиной.
- `test_bulk_upload_creates_orders()` - проверяет двухэтапную массовую загрузку.
- `test_bulk_item_form_rejects_wrong_material_and_long_title()` - проверяет валидацию строк массовой загрузки.
- `test_admin_can_delete_material_without_deleting_orders()` - проверяет удаление материала без удаления заказов.
- `test_admin_can_manage_rejection_reasons()` - проверяет создание, отключение и удаление стандартных причин.
- `test_security_headers_are_set()` - проверяет security headers и CSP.
- `test_admin_can_reject_order_with_reason_and_log_status_change()` - проверяет отклонение с причиной и логирование.
- `test_admin_can_reject_order_with_standard_reason()` - проверяет одиночное отклонение со стандартной причиной.
- `test_customer_order_list_shows_fresh_queue_ahead_count()` - проверяет счетчик свежих заказов впереди.
- `test_ready_order_reminder_stays_until_customer_confirms_receipt()` - проверяет напоминание о готовом заказе и подтверждение получения.
- `test_admin_can_assign_pickup_cell_to_ready_order()` - проверяет назначение ячейки и отображение пользователю.
- `test_ready_orders_can_share_pickup_cell()` - проверяет возможность назначить одну ячейку двум готовым заказам.
- `test_pickup_cell_clears_after_receipt_confirmation()` - проверяет освобождение ячейки после подтверждения получения.
- `test_admin_cannot_mark_order_done_without_customer_confirmation()` - проверяет запрет прямого `DONE` администратором.
- `test_only_last_30_done_orders_are_kept_after_customer_confirms_receipt()` - проверяет очистку старых выполненных заказов.
