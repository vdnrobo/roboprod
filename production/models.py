from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone


class ProductionType(models.TextChoices):
    PRINT_3D = "3d_print", "3D-печать"
    LASER_CUT = "laser_cut", "Лазерная резка"


class MaterialProductionType(models.TextChoices):
    PRINT_3D = ProductionType.PRINT_3D, "3D-печать"
    LASER_CUT = ProductionType.LASER_CUT, "Лазерная резка"
    BOTH = "both", "Оба типа"


class OrderStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    IN_PROGRESS = "in_progress", "В работе"
    READY = "ready", "Готов к выдаче"
    DONE = "done", "Выполнен"
    CANCELLED = "cancelled", "Отменено"
    REJECTED = "rejected", "Отклонено"


VALID_EXTENSIONS = {
    ProductionType.PRINT_3D: {".stl", ".stp"},
    ProductionType.LASER_CUT: {".dxf"},
}

MAX_UPLOAD_SIZE = 50 * 1024 * 1024
ORDER_MESSAGE_PHOTO_MAX_SIZE = 10 * 1024 * 1024
ORDER_TITLE_MAX_LENGTH = 30
PICKUP_CELL_MIN = 1
PICKUP_CELL_MAX = 20
PICKUP_CELL_CHOICES = tuple((number, str(number)) for number in range(PICKUP_CELL_MIN, PICKUP_CELL_MAX + 1))
SEMI_PRINTER_GROUP = "semi_printer"


def user_is_semi_printer(user):
    return (
        getattr(user, "is_authenticated", False)
        and not getattr(user, "is_staff", False)
        and user.groups.filter(name=SEMI_PRINTER_GROUP).exists()
    )


class Weekday(models.IntegerChoices):
    MONDAY = 0, "Понедельник"
    TUESDAY = 1, "Вторник"
    WEDNESDAY = 2, "Среда"
    THURSDAY = 3, "Четверг"
    FRIDAY = 4, "Пятница"
    SATURDAY = 5, "Суббота"
    SUNDAY = 6, "Воскресенье"


def order_file_upload_to(instance, filename):
    return f"orders/source/{filename}"


def order_photo_upload_to(instance, filename):
    return f"orders/photos/{filename}"


def order_draft_file_upload_to(instance, filename):
    return f"drafts/{instance.user_id}/files/{filename}"


def order_draft_photo_upload_to(instance, filename):
    return f"drafts/{instance.user_id}/photos/{filename}"


def order_message_photo_upload_to(instance, filename):
    return f"orders/messages/{instance.order_id}/{filename}"


def format_model_dimensions(width, depth, height=None):
    if width is None or depth is None:
        return ""

    def format_dimension(value):
        rounded = round(float(value), 1)
        if rounded.is_integer():
            return str(int(rounded))
        return f"{rounded:.1f}"

    formatted_width = format_dimension(width)
    formatted_depth = format_dimension(depth)
    if height is None or round(float(height), 1) == 0:
        return f"{formatted_width} x {formatted_depth} мм"
    return f"{formatted_width} x {formatted_depth} x {format_dimension(height)} мм"


class Material(models.Model):
    name = models.CharField("Название", max_length=120, unique=True)
    color = models.CharField("Цвет", max_length=40, default="Любой цвет")
    production_type = models.CharField(
        "Тип производства",
        max_length=20,
        choices=MaterialProductionType.choices,
        default=MaterialProductionType.BOTH,
    )
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Материал"
        verbose_name_plural = "Материалы"

    def __str__(self):
        return self.name

    def supports(self, production_type):
        return self.production_type in {MaterialProductionType.BOTH, production_type}


class RejectionReason(models.Model):
    text = models.CharField("Причина", max_length=255, unique=True)
    is_active = models.BooleanField("Активна", default=True)
    created_at = models.DateTimeField("Создана", auto_now_add=True)

    class Meta:
        ordering = ["text"]
        verbose_name = "Причина отклонения"
        verbose_name_plural = "Причины отклонения"

    def __str__(self):
        return self.text


class PinnedAnnouncement(models.Model):
    title = models.CharField("Заголовок", max_length=80)
    body = models.TextField("Текст объявления")
    is_active = models.BooleanField("Закреплено", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        ordering = ["-is_active", "-updated_at", "-id"]
        verbose_name = "Закрепленное объявление"
        verbose_name_plural = "Закрепленные объявления"

    def __str__(self):
        return self.title


class Countdown(models.Model):
    title = models.CharField("Название", max_length=80)
    target_date = models.DateField("Дата окончания")
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлен", auto_now=True)

    class Meta:
        ordering = ["target_date", "title", "id"]
        verbose_name = "Отсчет"
        verbose_name_plural = "Отсчеты"

    def __str__(self):
        return self.title

    @property
    def days_left(self):
        return (self.target_date - timezone.localdate()).days

    @property
    def days_left_word(self):
        value = abs(self.days_left)
        if value % 100 in {11, 12, 13, 14}:
            return "дней"
        if value % 10 == 1:
            return "день"
        if value % 10 in {2, 3, 4}:
            return "дня"
        return "дней"


class ProductionSettings(models.Model):
    allow_self_pickup = models.BooleanField("Разрешить самосъём заказов", default=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Настройка производства"
        verbose_name_plural = "Настройки производства"

    def __str__(self):
        return "Настройки производства"

    @classmethod
    def get_solo(cls):
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)


class DutyPerson(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="Аккаунт",
        on_delete=models.SET_NULL,
        related_name="duty_person",
        null=True,
        blank=True,
    )
    first_name = models.CharField("Имя", max_length=80)
    last_name = models.CharField("Фамилия", max_length=80)
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name", "id"]
        verbose_name = "Дежурный"
        verbose_name_plural = "Дежурные"

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.last_name} {self.first_name}".strip()


def admin_duty_people_queryset():
    User = get_user_model()
    admin_users = User.objects.filter(is_staff=True, is_active=True).order_by("last_name", "first_name", "username", "id")
    active_user_ids = []
    for user in admin_users:
        last_name = (user.last_name or "").strip()
        first_name = (user.first_name or "").strip() or user.username
        person, _created = DutyPerson.objects.get_or_create(
            user=user,
            defaults={
                "last_name": last_name,
                "first_name": first_name,
                "is_active": True,
            },
        )
        update_fields = []
        if person.last_name != last_name:
            person.last_name = last_name
            update_fields.append("last_name")
        if person.first_name != first_name:
            person.first_name = first_name
            update_fields.append("first_name")
        if not person.is_active:
            person.is_active = True
            update_fields.append("is_active")
        if update_fields:
            update_fields.append("updated_at")
            person.save(update_fields=update_fields)
            person.slots.update(first_name=person.first_name, last_name=person.last_name)
        active_user_ids.append(user.pk)
    if active_user_ids:
        DutyPerson.objects.filter(user__isnull=False).exclude(user_id__in=active_user_ids).update(is_active=False)
    else:
        DutyPerson.objects.filter(user__isnull=False).update(is_active=False)
    return DutyPerson.objects.filter(user_id__in=active_user_ids, is_active=True).order_by(
        "last_name", "first_name", "user__username", "id"
    )


class DutySchedule(models.Model):
    name = models.CharField("Название графика", max_length=100, unique=True)
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        verbose_name = "График дежурства"
        verbose_name_plural = "Графики дежурства"

    def __str__(self):
        return self.name


class DutySlot(models.Model):
    weekday = models.PositiveSmallIntegerField("День недели", choices=Weekday.choices, default=Weekday.MONDAY)
    schedule = models.ForeignKey(
        DutySchedule,
        verbose_name="График",
        on_delete=models.CASCADE,
        related_name="slots",
        null=True,
        blank=True,
    )
    person = models.ForeignKey(
        DutyPerson,
        verbose_name="Дежурный",
        on_delete=models.SET_NULL,
        related_name="slots",
        null=True,
        blank=True,
    )
    first_name = models.CharField("Имя", max_length=80, blank=True)
    last_name = models.CharField("Фамилия", max_length=80, blank=True)
    unload_time = models.TimeField("Время разгрузки очереди")
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        ordering = [
            "schedule__name",
            "weekday",
            "unload_time",
            "person__last_name",
            "person__first_name",
            "last_name",
            "first_name",
            "id",
        ]
        verbose_name = "Дежурство"
        verbose_name_plural = "График дежурства"

    def __str__(self):
        schedule = self.schedule.name if self.schedule_id else self.get_weekday_display()
        return f"{schedule}: {self.full_name} - {self.unload_time:%H:%M}"

    @property
    def full_name(self):
        if self.person_id:
            return self.person.full_name
        return f"{self.last_name} {self.first_name}".strip()

    @property
    def display_last_name(self):
        return self.person.last_name if self.person_id else self.last_name

    @property
    def display_first_name(self):
        return self.person.first_name if self.person_id else self.first_name

    def clean(self):
        super().clean()
        if not self.person_id and not (self.first_name and self.last_name):
            raise ValidationError({"person": "Выберите дежурного."})

    def save(self, *args, **kwargs):
        if self.person_id:
            self.first_name = self.person.first_name
            self.last_name = self.person.last_name
        self.full_clean()
        super().save(*args, **kwargs)


class DutyScheduleRule(models.Model):
    weekday = models.PositiveSmallIntegerField("День недели", choices=Weekday.choices, unique=True)
    schedule = models.ForeignKey(
        DutySchedule,
        verbose_name="График",
        on_delete=models.PROTECT,
        related_name="weekday_rules",
    )
    is_active = models.BooleanField("Активно", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        ordering = ["weekday", "id"]
        verbose_name = "Правило графика"
        verbose_name_plural = "Правила графиков"

    def __str__(self):
        return f"{self.get_weekday_display()} -> {self.schedule.name}"


class DutySkip(models.Model):
    duty_slot = models.ForeignKey(
        DutySlot,
        verbose_name="Дежурный",
        on_delete=models.CASCADE,
        related_name="skips",
    )
    unavailable_date = models.DateField("Дата пропуска")
    unavailable_time = models.TimeField("Время пропуска")
    reason = models.TextField("Причина")
    replacement_slot = models.ForeignKey(
        DutySlot,
        verbose_name="Заменяющий",
        on_delete=models.PROTECT,
        related_name="replacements",
        null=True,
        blank=True,
    )
    replacement_person = models.ForeignKey(
        DutyPerson,
        verbose_name="Заменяющий",
        on_delete=models.PROTECT,
        related_name="replacement_skips",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        ordering = ["-unavailable_date", "unavailable_time", "duty_slot_id", "id"]
        verbose_name = "Пропуск дежурства"
        verbose_name_plural = "Пропуски дежурства"

    def __str__(self):
        return (
            f"{self.duty_slot.full_name} "
            f"{self.unavailable_date:%d.%m.%Y} {self.unavailable_time:%H:%M}"
        )

    def clean(self):
        super().clean()
        if not self.replacement_person_id and not self.replacement_slot_id:
            raise ValidationError({"replacement_person": "Выберите заменяющего."})
        if self.duty_slot_id and self.replacement_person_id and self.duty_slot.person_id == self.replacement_person_id:
            raise ValidationError({"replacement_person": "Заменяющий должен отличаться от дежурного."})
        if self.duty_slot_id and self.replacement_slot_id and self.duty_slot_id == self.replacement_slot_id:
            raise ValidationError({"replacement_person": "Заменяющий должен отличаться от дежурного."})
        if not (self.reason or "").strip():
            raise ValidationError({"reason": "Укажите причину пропуска."})

    @property
    def replacement_full_name(self):
        if self.replacement_person_id:
            return self.replacement_person.full_name
        if self.replacement_slot_id:
            return self.replacement_slot.full_name
        return ""


class OrderPrefixRule(models.Model):
    prefix = models.CharField("Префикс", max_length=20)
    starts_on = models.DateField("Начало периода")
    ends_on = models.DateField("Конец периода")
    next_number = models.PositiveIntegerField("Следующий номер", default=1)
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        ordering = ["-starts_on", "-id"]
        verbose_name = "Правило префикса заказа"
        verbose_name_plural = "Правила префиксов заказов"

    def __str__(self):
        return f"{self.prefix} ({self.starts_on:%d.%m.%Y}-{self.ends_on:%d.%m.%Y})"

    @property
    def is_current(self):
        today = timezone.localdate()
        return self.is_active and self.starts_on <= today <= self.ends_on


class Order(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Пользователь",
        on_delete=models.CASCADE,
        related_name="orders",
    )
    title = models.CharField("Название заказа", max_length=ORDER_TITLE_MAX_LENGTH)
    production_type = models.CharField(
        "Тип производства",
        max_length=20,
        choices=ProductionType.choices,
    )
    material = models.ForeignKey(
        Material,
        verbose_name="Материал",
        on_delete=models.SET_NULL,
        related_name="orders",
        null=True,
        blank=True,
    )
    quantity = models.PositiveIntegerField("Количество", default=1)
    photo = models.ImageField("Фото модели", upload_to=order_photo_upload_to, blank=True)
    production_file = models.FileField(
        "Производственный файл",
        upload_to=order_file_upload_to,
    )
    model_width = models.FloatField("Ширина модели", null=True, blank=True)
    model_depth = models.FloatField("Глубина модели", null=True, blank=True)
    model_height = models.FloatField("Высота модели", null=True, blank=True)
    comment = models.TextField("Комментарий", blank=True)
    admin_comment = models.TextField("Комментарий администратора", blank=True)
    rejection_reason = models.TextField("Причина отклонения", blank=True)
    pickup_cell = models.PositiveSmallIntegerField("Ячейка выдачи", null=True, blank=True)
    prefix_rule = models.ForeignKey(
        OrderPrefixRule,
        verbose_name="Правило префикса",
        on_delete=models.SET_NULL,
        related_name="orders",
        null=True,
        blank=True,
    )
    prefix_text = models.CharField("Префикс номера", max_length=20, blank=True)
    prefix_number = models.PositiveIntegerField("Номер в префиксе", null=True, blank=True)
    is_priority = models.BooleanField("Приоритетный заказ", default=False)
    priority_remaining_quantity = models.PositiveIntegerField(
        "Осталось копий к запуску",
        null=True,
        blank=True,
    )
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлен", auto_now=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"

    def __str__(self):
        return f"#{self.display_number} {self.title}"

    @property
    def public_number(self):
        return self.display_number

    @property
    def display_number(self):
        if self.prefix_text and self.prefix_number:
            return f"{self.prefix_text}{self.prefix_number}"
        return str(self.pk or "")

    @property
    def file_suffix(self):
        return "3d" if self.production_type == ProductionType.PRINT_3D else "laser"

    @property
    def priority_started_quantity(self):
        if not self.is_priority or self.priority_remaining_quantity is None:
            return 0
        return max(self.quantity - self.priority_remaining_quantity, 0)

    @property
    def has_model_dimensions(self):
        return self.model_width is not None and self.model_depth is not None

    @property
    def model_dimensions_display(self):
        return format_model_dimensions(self.model_width, self.model_depth, self.model_height)

    def register_priority_print_batch(self, copies):
        if not self.is_priority:
            raise ValidationError("Заказ не является приоритетным.")
        if self.status not in {OrderStatus.PENDING, OrderStatus.IN_PROGRESS}:
            raise ValidationError("Запуск копий можно отмечать только для активного заказа.")
        if copies < 1:
            raise ValidationError("Количество копий должно быть больше нуля.")
        if self.priority_remaining_quantity is None:
            self.priority_remaining_quantity = self.quantity
        if copies > self.priority_remaining_quantity:
            raise ValidationError("Нельзя поставить на печать больше копий, чем осталось.")
        self.priority_remaining_quantity -= copies
        if self.status == OrderStatus.PENDING:
            self.status = OrderStatus.IN_PROGRESS
        if self.priority_remaining_quantity == 0:
            self.status = OrderStatus.DONE
            self.pickup_cell = None

    def clean(self):
        super().clean()
        if self.quantity < 1:
            raise ValidationError({"quantity": "Количество должно быть больше нуля."})
        if self.material_id and not self.material.supports(self.production_type):
            raise ValidationError({"material": "Материал не подходит для выбранного типа производства."})
        if self.is_priority:
            if self.production_type != ProductionType.PRINT_3D:
                raise ValidationError({"is_priority": "Приоритетным может быть только заказ на 3D-печать."})
            if self.priority_remaining_quantity is None:
                self.priority_remaining_quantity = self.quantity
            if self.priority_remaining_quantity > self.quantity:
                raise ValidationError(
                    {"priority_remaining_quantity": "Остаток не может быть больше общего количества."}
                )
        else:
            self.priority_remaining_quantity = None
        if self.pickup_cell is not None:
            if not PICKUP_CELL_MIN <= self.pickup_cell <= PICKUP_CELL_MAX:
                raise ValidationError({"pickup_cell": "Номер ячейки должен быть от 1 до 20."})
            if self.status != OrderStatus.READY:
                raise ValidationError({"pickup_cell": "Ячейку можно назначить только заказу, готовому к выдаче."})
        if self.production_file:
            try:
                if self.production_file.size > MAX_UPLOAD_SIZE:
                    raise ValidationError(
                        {"production_file": "Размер файла не должен превышать 50 МБ."}
                    )
            except (FileNotFoundError, OSError):
                pass
            ext = Path(self.production_file.name).suffix.lower()
            allowed = VALID_EXTENSIONS.get(self.production_type, set())
            if ext not in allowed:
                allowed_text = ", ".join(sorted(allowed))
                raise ValidationError(
                    {"production_file": f"Для выбранного типа допустимы только: {allowed_text}."}
                )

    def assign_active_prefix(self):
        if self.prefix_number:
            return
        today = timezone.localdate()
        rule = (
            OrderPrefixRule.objects.select_for_update()
            .filter(is_active=True, starts_on__lte=today, ends_on__gte=today)
            .order_by("-starts_on", "-id")
            .first()
        )
        if not rule:
            return
        self.prefix_rule = rule
        self.prefix_text = rule.prefix
        self.prefix_number = rule.next_number
        rule.next_number += 1
        rule.save(update_fields=["next_number", "updated_at"])

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        if is_new and not self.prefix_number:
            with transaction.atomic():
                self.assign_active_prefix()
                self.full_clean()
                super().save(*args, **kwargs)
                self._rename_production_file()
            return
        self.full_clean()
        super().save(*args, **kwargs)
        self._rename_production_file()

    def _rename_production_file(self):
        if not self.production_file:
            return

        ext = Path(self.production_file.name).suffix.lower()
        target_name = f"orders/files/{self.pk}_{self.file_suffix}{ext}"
        if self.production_file.name == target_name:
            return

        storage = self.production_file.storage
        source_name = self.production_file.name
        with storage.open(source_name, "rb") as source_file:
            if storage.exists(target_name):
                storage.delete(target_name)
            storage.save(target_name, source_file)

        if storage.exists(source_name):
            storage.delete(source_name)

        type(self).objects.filter(pk=self.pk).update(production_file=target_name)
        self.production_file.name = target_name


class OrderStatusLog(models.Model):
    order = models.ForeignKey(
        Order,
        verbose_name="Заказ",
        on_delete=models.SET_NULL,
        related_name="status_logs",
        null=True,
        blank=True,
    )
    order_number = models.PositiveIntegerField("Номер заказа")
    order_title = models.CharField("Название заказа", max_length=ORDER_TITLE_MAX_LENGTH)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Администратор",
        on_delete=models.SET_NULL,
        related_name="order_status_changes",
        null=True,
        blank=True,
    )
    old_status = models.CharField("Старый статус", max_length=20, choices=OrderStatus.choices)
    new_status = models.CharField("Новый статус", max_length=20, choices=OrderStatus.choices)
    reason = models.TextField("Причина", blank=True)
    created_at = models.DateTimeField("Дата изменения", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Изменение статуса"
        verbose_name_plural = "Изменения статусов"

    def __str__(self):
        return f"#{self.order_number}: {self.old_status} -> {self.new_status}"


class OrderMessage(models.Model):
    order = models.ForeignKey(
        Order,
        verbose_name="Заказ",
        on_delete=models.CASCADE,
        related_name="messages",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Автор",
        on_delete=models.SET_NULL,
        related_name="order_messages",
        null=True,
        blank=True,
    )
    text = models.TextField("Сообщение", blank=True)
    photo = models.ImageField("Фото", upload_to=order_message_photo_upload_to, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Сообщение по заказу"
        verbose_name_plural = "Сообщения по заказам"

    def __str__(self):
        author = self.author.username if self.author_id else "system"
        return f"#{self.order_id} {author}: {self.created_at:%d.%m.%Y %H:%M}"


class OrderDraft(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Пользователь",
        on_delete=models.CASCADE,
        related_name="order_drafts",
    )
    production_type = models.CharField(
        "Тип производства",
        max_length=20,
        choices=ProductionType.choices,
    )
    title = models.CharField("Название заказа", max_length=ORDER_TITLE_MAX_LENGTH, blank=True)
    material = models.ForeignKey(
        Material,
        verbose_name="Материал",
        on_delete=models.SET_NULL,
        related_name="order_drafts",
        null=True,
        blank=True,
    )
    quantity = models.PositiveIntegerField("Количество", default=1)
    photo = models.ImageField("Фото модели", upload_to=order_draft_photo_upload_to, blank=True)
    production_file = models.FileField(
        "Производственный файл",
        upload_to=order_draft_file_upload_to,
        blank=True,
    )
    model_width = models.FloatField("Ширина модели", null=True, blank=True)
    model_depth = models.FloatField("Глубина модели", null=True, blank=True)
    model_height = models.FloatField("Высота модели", null=True, blank=True)
    comment = models.TextField("Комментарий", blank=True)
    is_priority = models.BooleanField("Приоритетный заказ", default=False)
    current_step = models.CharField("Текущий шаг", max_length=20, default="title")
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлен", auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        verbose_name = "Черновик заказа"
        verbose_name_plural = "Черновики заказов"

    def __str__(self):
        return self.title or f"Черновик {self.get_production_type_display()}"

    @property
    def has_model_dimensions(self):
        return self.model_width is not None and self.model_depth is not None

    @property
    def model_dimensions_display(self):
        return format_model_dimensions(self.model_width, self.model_depth, self.model_height)


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Пользователь",
        on_delete=models.SET_NULL,
        related_name="audit_events",
        null=True,
        blank=True,
    )
    action = models.CharField("Действие", max_length=80)
    target_type = models.CharField("Тип объекта", max_length=80)
    target_id = models.PositiveIntegerField("ID объекта", null=True, blank=True)
    target_label = models.CharField("Объект", max_length=255, blank=True)
    changes = models.TextField("Изменения", blank=True)
    created_at = models.DateTimeField("Дата", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Событие аудита"
        verbose_name_plural = "Аудит"

    def __str__(self):
        return f"{self.action}: {self.target_label}"


def prune_completed_orders(keep=30):
    keep_ids = list(
        Order.objects.filter(status=OrderStatus.DONE)
        .order_by("-updated_at", "-pk")
        .values_list("pk", flat=True)[:keep]
    )
    old_done_orders = Order.objects.filter(status=OrderStatus.DONE).exclude(pk__in=keep_ids)
    deleted_count, _ = old_done_orders.delete()
    return deleted_count


@receiver(post_delete, sender=Order)
def delete_order_files(sender, instance, **kwargs):
    for field in (instance.photo, instance.production_file):
        if field:
            try:
                field.delete(save=False)
            except OSError:
                pass


@receiver(post_delete, sender=OrderDraft)
def delete_order_draft_files(sender, instance, **kwargs):
    for field in (instance.photo, instance.production_file):
        if field:
            try:
                field.delete(save=False)
            except OSError:
                pass


@receiver(post_delete, sender=OrderMessage)
def delete_order_message_files(sender, instance, **kwargs):
    if instance.photo:
        try:
            instance.photo.delete(save=False)
        except OSError:
            pass
