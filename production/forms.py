from pathlib import Path

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (
    MAX_UPLOAD_SIZE,
    ORDER_MESSAGE_PHOTO_MAX_SIZE,
    ORDER_TITLE_MAX_LENGTH,
    Countdown,
    DutySkip,
    DutyPerson,
    DutySchedule,
    DutyScheduleRule,
    DutySlot,
    Material,
    Order,
    OrderMessage,
    OrderPrefixRule,
    OrderStatus,
    PICKUP_CELL_CHOICES,
    PinnedAnnouncement,
    ProductionSettings,
    ProductionType,
    RejectionReason,
    VALID_EXTENSIONS,
    Weekday,
    admin_duty_people_queryset,
)


def format_size(size):
    return f"{size // (1024 * 1024)} МБ"


def validate_file_size(upload):
    if upload.size > MAX_UPLOAD_SIZE:
        raise ValidationError(
            f"{upload.name}: размер файла не должен превышать {format_size(MAX_UPLOAD_SIZE)}."
        )


def validate_image_size(upload, max_size=ORDER_MESSAGE_PHOTO_MAX_SIZE):
    if upload.size > max_size:
        raise ValidationError(
            f"{upload.name}: размер изображения не должен превышать {format_size(max_size)}."
        )


def production_type_label(production_type):
    return dict(ProductionType.choices).get(production_type, production_type)


def file_format_error(filename, production_type, allowed_extensions):
    allowed_text = ", ".join(sorted(allowed_extensions))
    return (
        f"{filename}: для типа «{production_type_label(production_type)}» допустимы только "
        f"{allowed_text}. Максимальный размер файла - {format_size(MAX_UPLOAD_SIZE)}."
    )


class RegisterForm(UserCreationForm):
    first_name = forms.CharField(label="Имя", max_length=150, required=True)
    last_name = forms.CharField(label="Фамилия", max_length=150, required=True)
    email = forms.EmailField(label="Email", required=False)

    class Meta:
        model = User
        fields = ["username", "last_name", "first_name", "email", "password1", "password2"]


class OrderCreateForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = [
            "title",
            "production_type",
            "material",
            "quantity",
            "photo",
            "production_file",
            "comment",
        ]
        widgets = {
            "comment": forms.Textarea(attrs={"rows": 4}),
            "quantity": forms.NumberInput(attrs={"min": 1}),
            "title": forms.TextInput(attrs={"maxlength": ORDER_TITLE_MAX_LENGTH}),
        }

    def __init__(self, *args, **kwargs):
        fixed_production_type = kwargs.pop("production_type", None)
        super().__init__(*args, **kwargs)
        self.fixed_production_type = fixed_production_type
        material_queryset = Material.objects.filter(is_active=True)
        if fixed_production_type:
            material_queryset = material_queryset.filter(
                production_type__in=[fixed_production_type, "both"]
            )
            self.fields["production_type"].initial = fixed_production_type
            self.fields["production_type"].widget = forms.HiddenInput()
        self.fields["material"].queryset = material_queryset
        self.fields["material"].required = True
        self.fields["material"].error_messages["required"] = "Выберите материал."
        self.fields["photo"].required = True
        self.allowed_extensions = sorted(VALID_EXTENSIONS.get(fixed_production_type, []))

    def clean_material(self):
        material = self.cleaned_data.get("material")
        production_type = self.cleaned_data.get("production_type")
        if not material:
            raise ValidationError("Выберите материал.")
        if production_type and not material.supports(production_type):
            raise ValidationError("Материал не подходит для выбранного типа производства.")
        return material

    def clean_production_file(self):
        production_file = self.cleaned_data["production_file"]
        production_type = self.cleaned_data.get("production_type")
        if production_type and production_file:
            validate_file_size(production_file)
            ext = Path(production_file.name).suffix.lower()
            allowed = VALID_EXTENSIONS.get(production_type, set())
            if ext not in allowed:
                raise ValidationError(file_format_error(production_file.name, production_type, allowed))
        return production_file


class OrderTitleStepForm(forms.Form):
    title = forms.CharField(
        label="Название заказа",
        max_length=ORDER_TITLE_MAX_LENGTH,
        error_messages={
            "max_length": f"Название заказа не должно быть длиннее {ORDER_TITLE_MAX_LENGTH} символов.",
            "required": "Укажите название заказа.",
        },
        widget=forms.TextInput(
            attrs={
                "maxlength": ORDER_TITLE_MAX_LENGTH,
                "placeholder": "Например: корпус датчика",
                "autofocus": True,
            }
        ),
    )

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise ValidationError("Укажите название заказа.")
        return title


class OrderImageStepForm(forms.Form):
    photo = forms.ImageField(
        label="Изображение модели",
        required=False,
        error_messages={"invalid_image": "Загрузите корректное изображение."},
        widget=forms.ClearableFileInput(
            attrs={
                "accept": "image/*",
                "data-photo-input": "true",
            }
        ),
    )


class OrderFileStepForm(forms.Form):
    production_file = forms.FileField(
        label="Производственный файл",
        error_messages={"required": "Загрузите производственный файл."},
    )

    def __init__(self, *args, **kwargs):
        self.production_type = kwargs.pop("production_type")
        super().__init__(*args, **kwargs)
        self.allowed_extensions = sorted(VALID_EXTENSIONS[self.production_type])
        self.fields["production_file"].widget.attrs["accept"] = ",".join(self.allowed_extensions)

    def clean_production_file(self):
        production_file = self.cleaned_data["production_file"]
        validate_file_size(production_file)
        ext = Path(production_file.name).suffix.lower()
        if ext not in VALID_EXTENSIONS[self.production_type]:
            raise ValidationError(
                file_format_error(production_file.name, self.production_type, self.allowed_extensions)
            )
        return production_file


class OrderDetailsStepForm(forms.Form):
    material = forms.ModelChoiceField(
        label="Материал",
        queryset=Material.objects.none(),
        error_messages={"required": "Выберите материал."},
    )
    quantity = forms.IntegerField(
        label="Количество",
        min_value=1,
        initial=1,
        widget=forms.NumberInput(attrs={"min": 1}),
        error_messages={"min_value": "Количество должно быть больше нуля."},
    )
    comment = forms.CharField(
        label="Комментарий",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    is_priority = forms.BooleanField(
        label="Приоритетный заказ",
        required=False,
        help_text="Только для администраторских заказов на 3D-печать.",
    )

    def __init__(self, *args, **kwargs):
        self.production_type = kwargs.pop("production_type")
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Material.objects.filter(
            is_active=True,
            production_type__in=[self.production_type, "both"],
        ).order_by("name")
        self.fields["material"].widget.attrs.update(
            {
                "data-bulk-material-row": self.production_type,
            }
        )
        self.fields["material"].required = True
        if not (getattr(user, "is_staff", False) and self.production_type == ProductionType.PRINT_3D):
            self.fields.pop("is_priority")

    def clean_material(self):
        material = self.cleaned_data.get("material")
        if not material:
            raise ValidationError("Выберите материал.")
        if not material.supports(self.production_type):
            raise ValidationError("Материал не подходит для выбранного типа производства.")
        return material


class OrderMessageForm(forms.ModelForm):
    class Meta:
        model = OrderMessage
        fields = ["text", "photo"]
        widgets = {
            "text": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Напишите вопрос или комментарий по заказу",
                }
            ),
            "photo": forms.ClearableFileInput(attrs={"accept": "image/*"}),
        }

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo:
            validate_image_size(photo)
        return photo

    def clean(self):
        cleaned_data = super().clean()
        text = (cleaned_data.get("text") or "").strip()
        photo = cleaned_data.get("photo")
        if not text and not photo:
            raise ValidationError("Добавьте текст сообщения или фото.")
        cleaned_data["text"] = text
        return cleaned_data


class PriorityPrintBatchForm(forms.Form):
    copies = forms.IntegerField(
        label="Поставлено на печать",
        min_value=1,
        widget=forms.NumberInput(attrs={"min": 1, "placeholder": "Копии"}),
        error_messages={
            "required": "Укажите количество копий.",
            "min_value": "Количество копий должно быть больше нуля.",
        },
    )

    def __init__(self, *args, **kwargs):
        self.order = kwargs.pop("order")
        super().__init__(*args, **kwargs)
        remaining = self.order.priority_remaining_quantity
        if remaining is None:
            remaining = self.order.quantity
        self.fields["copies"].widget.attrs["max"] = remaining

    def clean_copies(self):
        copies = self.cleaned_data["copies"]
        remaining = self.order.priority_remaining_quantity
        if remaining is None:
            remaining = self.order.quantity
        if not self.order.is_priority:
            raise ValidationError("Заказ не является приоритетным.")
        if self.order.status not in {OrderStatus.PENDING, OrderStatus.IN_PROGRESS}:
            raise ValidationError("Запуск копий можно отмечать только для активного заказа.")
        if copies > remaining:
            raise ValidationError("Нельзя поставить на печать больше копий, чем осталось.")
        return copies


class MultiFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiFileField(forms.FileField):
    widget = MultiFileInput

    def clean(self, data, initial=None):
        if not data:
            raise ValidationError("Загрузите хотя бы один файл.")
        files = data if isinstance(data, (list, tuple)) else [data]
        return [super(MultiFileField, self).clean(file, initial) for file in files]


class BulkOrderCreateForm(forms.Form):
    files = MultiFileField(label="Файлы")

    def clean_files(self):
        files = self.cleaned_data["files"]
        errors = []
        for upload in files:
            try:
                validate_file_size(upload)
            except ValidationError as error:
                errors.extend(error.messages)
            ext = Path(upload.name).suffix.lower()
            if ext not in {".stl", ".stp", ".dxf"}:
                errors.append(
                    f"{upload.name}: допустимы только .stl, .stp, .dxf. "
                    f"Максимальный размер файла - {format_size(MAX_UPLOAD_SIZE)}."
                )
        if errors:
            raise ValidationError(errors)
        return files


class BulkOrderItemForm(forms.Form):
    file_token = forms.CharField(widget=forms.HiddenInput())
    photo = forms.ImageField(
        label="Изображение",
        required=False,
        error_messages={"invalid_image": "Загрузите корректное изображение."},
        widget=forms.ClearableFileInput(attrs={"accept": "image/*"}),
    )
    title = forms.CharField(
        label="Название",
        max_length=ORDER_TITLE_MAX_LENGTH,
        error_messages={
            "max_length": f"Название заказа не должно быть длиннее {ORDER_TITLE_MAX_LENGTH} символов.",
            "required": "Укажите название заказа.",
        },
        widget=forms.TextInput(attrs={"maxlength": ORDER_TITLE_MAX_LENGTH}),
    )
    material = forms.ModelChoiceField(
        label="Материал",
        queryset=Material.objects.none(),
        error_messages={"required": "Выберите материал."},
    )
    quantity = forms.IntegerField(
        label="Количество",
        min_value=1,
        initial=1,
        widget=forms.NumberInput(attrs={"min": 1}),
        error_messages={"min_value": "Количество должно быть больше нуля."},
    )

    def __init__(self, *args, **kwargs):
        self.production_type = kwargs.pop("production_type")
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Material.objects.filter(
            is_active=True,
            production_type__in=[self.production_type, "both"],
        ).order_by("name")
        self.fields["material"].widget.attrs["data-bulk-material-row"] = self.production_type

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise ValidationError("Укажите название заказа.")
        return title

    def clean_material(self):
        material = self.cleaned_data.get("material")
        if not material:
            raise ValidationError("Выберите материал.")
        if not material.supports(self.production_type):
            raise ValidationError("Материал не подходит для типа файла.")
        return material


class AdminOrderUpdateForm(forms.ModelForm):
    standard_rejection_reason = forms.ModelChoiceField(
        label="Стандартная причина",
        queryset=RejectionReason.objects.none(),
        required=False,
        empty_label="Выберите причину",
    )

    class Meta:
        model = Order
        fields = ["status", "material", "pickup_cell", "admin_comment", "rejection_reason"]
        widgets = {
            "pickup_cell": forms.Select(choices=[("", "Без ячейки"), *PICKUP_CELL_CHOICES]),
            "admin_comment": forms.Textarea(
                attrs={
                    "rows": 2,
                    "placeholder": "Внутренний комментарий, виден только администраторам.",
                }
            ),
            "rejection_reason": forms.Textarea(
                attrs={
                    "rows": 2,
                    "placeholder": "Заполните, если заказ отклоняется.",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Material.objects.filter(is_active=True)
        self.fields["material"].required = True
        self.fields["standard_rejection_reason"].queryset = RejectionReason.objects.filter(
            is_active=True
        ).order_by("text")
        admin_status_choices = [
            choice for choice in OrderStatus.choices if choice[0] != OrderStatus.DONE
        ]
        if self.instance and self.instance.status == OrderStatus.DONE:
            admin_status_choices.insert(0, (OrderStatus.DONE, OrderStatus.DONE.label))
        self.fields["status"].choices = admin_status_choices

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get("status")
        standard_reason = cleaned_data.get("standard_rejection_reason")
        manual_reason = (cleaned_data.get("rejection_reason") or "").strip()
        reason = manual_reason or (standard_reason.text if standard_reason else "")
        if (
            status == OrderStatus.DONE
            and self.instance
            and self.instance.status != OrderStatus.DONE
        ):
            self.add_error("status", "Заказ становится выполненным только после подтверждения получения заказчиком.")
        if status == OrderStatus.REJECTED and not reason:
            self.add_error("rejection_reason", "Укажите причину отклонения заказа.")
        if status == OrderStatus.REJECTED:
            cleaned_data["rejection_reason"] = reason
        if status != OrderStatus.REJECTED:
            cleaned_data["rejection_reason"] = ""
        pickup_cell = cleaned_data.get("pickup_cell")
        if status != OrderStatus.READY:
            cleaned_data["pickup_cell"] = None
        return cleaned_data


class AdminBulkStatusUpdateForm(forms.Form):
    order_ids = forms.ModelMultipleChoiceField(
        queryset=Order.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "Выберите хотя бы один заказ."},
    )
    status = forms.ChoiceField(label="Новый статус")
    standard_rejection_reason = forms.ModelChoiceField(
        label="Стандартная причина",
        queryset=RejectionReason.objects.none(),
        required=False,
        empty_label="Выберите причину",
    )
    rejection_reason = forms.CharField(
        label="Причина отклонения",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Заполните, если заказы отклоняются."}),
    )
    next = forms.CharField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["order_ids"].queryset = Order.objects.all()
        self.fields["status"].choices = [
            choice for choice in OrderStatus.choices if choice[0] != OrderStatus.DONE
        ]
        self.fields["standard_rejection_reason"].queryset = RejectionReason.objects.filter(
            is_active=True
        ).order_by("text")

    def clean_rejection_reason(self):
        return (self.cleaned_data.get("rejection_reason") or "").strip()

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get("status")
        standard_reason = cleaned_data.get("standard_rejection_reason")
        manual_reason = cleaned_data.get("rejection_reason") or ""
        reason = manual_reason or (standard_reason.text if standard_reason else "")
        if status == OrderStatus.REJECTED and not reason:
            self.add_error("rejection_reason", "Укажите причину отклонения заказов.")
        if status == OrderStatus.REJECTED:
            cleaned_data["rejection_reason"] = reason
        if status != OrderStatus.REJECTED:
            cleaned_data["rejection_reason"] = ""
        return cleaned_data


class SemiPrinterMarkReadyForm(forms.Form):
    pickup_cell = forms.ChoiceField(
        label="Ячейка выдачи",
        choices=[("", "Без ячейки"), *PICKUP_CELL_CHOICES],
        required=False,
    )
    next = forms.CharField(required=False)

    def clean_pickup_cell(self):
        value = self.cleaned_data.get("pickup_cell")
        return int(value) if value else None


class MaterialForm(forms.ModelForm):
    class Meta:
        model = Material
        fields = ["name", "color", "production_type", "is_active"]
        widgets = {
            "color": forms.TextInput(attrs={"placeholder": "Любой цвет"}),
        }

    def clean_color(self):
        return (self.cleaned_data.get("color") or "Любой цвет").strip() or "Любой цвет"


class OrderFilterForm(forms.Form):
    status = forms.ChoiceField(label="Статус", required=False)
    production_type = forms.ChoiceField(label="Тип", required=False)
    material = forms.ModelChoiceField(
        label="Материал",
        queryset=Material.objects.none(),
        required=False,
    )
    sort = forms.ChoiceField(
        label="Сортировка",
        required=False,
        choices=[
            ("-created_at", "Сначала новые"),
            ("created_at", "Сначала старые"),
            ("status", "По статусу"),
            ("production_type", "По типу"),
            ("material", "По материалу"),
        ],
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user")
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [("", "Все"), *OrderStatus.choices]
        self.fields["production_type"].choices = [("", "Все"), *ProductionType.choices]
        self.fields["material"].queryset = Material.objects.filter(
            orders__user=user
        ).distinct().order_by("name")


class RejectionReasonForm(forms.ModelForm):
    class Meta:
        model = RejectionReason
        fields = ["text", "is_active"]
        widgets = {
            "text": forms.TextInput(attrs={"placeholder": "Например: файл поврежден"}),
        }


class PinnedAnnouncementForm(forms.ModelForm):
    class Meta:
        model = PinnedAnnouncement
        fields = ["title", "body", "is_active"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Например: сегодня выдача до 17:00"}),
            "body": forms.Textarea(attrs={"rows": 5, "placeholder": "Короткий текст объявления"}),
        }


class CountdownForm(forms.ModelForm):
    class Meta:
        model = Countdown
        fields = ["title", "target_date", "is_active"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Например: до соревнований"}),
            "target_date": forms.DateInput(attrs={"type": "date"}),
        }


class ProductionSettingsForm(forms.ModelForm):
    class Meta:
        model = ProductionSettings
        fields = ["allow_self_pickup"]


class DutySlotForm(forms.ModelForm):
    class Meta:
        model = DutySlot
        fields = ["schedule", "person", "unload_time", "is_active"]
        widgets = {
            "unload_time": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, **kwargs):
        fixed_schedule = kwargs.pop("schedule", None)
        super().__init__(*args, **kwargs)
        self.fixed_schedule = fixed_schedule
        self.fields["schedule"].queryset = DutySchedule.objects.filter(is_active=True).order_by("name", "id")
        if fixed_schedule:
            self.fields["schedule"].initial = fixed_schedule
            self.fields["schedule"].widget = forms.HiddenInput()
        self.fields["person"].queryset = admin_duty_people_queryset()

    def clean_schedule(self):
        schedule = self.cleaned_data.get("schedule") or self.fixed_schedule
        if not schedule:
            raise ValidationError("Выберите график.")
        return schedule

    def clean_person(self):
        person = self.cleaned_data.get("person")
        if not person:
            raise ValidationError("Выберите дежурного.")
        return person


DutySlotScheduleFormSet = forms.modelformset_factory(
    DutySlot,
    form=DutySlotForm,
    can_delete=True,
    extra=4,
)


class DutyScheduleForm(forms.ModelForm):
    class Meta:
        model = DutySchedule
        fields = ["name", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Например: обычный график"}),
        }

    def clean_name(self):
        value = self.cleaned_data["name"].strip()
        if not value:
            raise ValidationError("Укажите название графика.")
        return value


class DutyScheduleRuleForm(forms.ModelForm):
    class Meta:
        model = DutyScheduleRule
        fields = ["weekday", "schedule", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["schedule"].queryset = DutySchedule.objects.filter(is_active=True).order_by("name", "id")


class DutyPersonForm(forms.ModelForm):
    class Meta:
        model = DutyPerson
        fields = ["user", "last_name", "first_name", "is_active"]
        widgets = {
            "last_name": forms.TextInput(attrs={"placeholder": "Фамилия"}),
            "first_name": forms.TextInput(attrs={"placeholder": "Имя"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].required = False
        self.fields["user"].queryset = User.objects.order_by("last_name", "first_name", "username", "id")

    def clean_first_name(self):
        value = self.cleaned_data["first_name"].strip()
        if not value:
            raise ValidationError("Укажите имя.")
        return value

    def clean_last_name(self):
        value = self.cleaned_data["last_name"].strip()
        if not value:
            raise ValidationError("Укажите фамилию.")
        return value


class DutySkipForm(forms.ModelForm):
    class Meta:
        model = DutySkip
        fields = ["duty_slot", "unavailable_date", "reason", "replacement_person"]
        widgets = {
            "unavailable_date": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(attrs={"rows": 3, "placeholder": "Почему дежурный не может разгрузить очередь"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        slots = DutySlot.objects.select_related("schedule", "person", "person__user").filter(
            is_active=True,
            person__user__is_staff=True,
            person__user__is_active=True,
        ).order_by(
            "schedule__name", "unload_time", "person__last_name", "person__first_name", "last_name", "first_name", "id"
        )
        people = admin_duty_people_queryset()
        self.fields["duty_slot"].queryset = slots
        self.fields["replacement_person"].queryset = people
        self.fields["unavailable_date"].initial = timezone.localdate()

    def clean_reason(self):
        value = self.cleaned_data["reason"].strip()
        if not value:
            raise ValidationError("Укажите причину пропуска.")
        return value

    def clean(self):
        cleaned_data = super().clean()
        duty_slot = cleaned_data.get("duty_slot")
        replacement_person = cleaned_data.get("replacement_person")
        if duty_slot and replacement_person and duty_slot.person_id == replacement_person.pk:
            self.add_error("replacement_person", "Заменяющий должен отличаться от дежурного.")
        if duty_slot:
            cleaned_data["unavailable_time"] = duty_slot.unload_time
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.duty_slot_id:
            instance.unavailable_time = instance.duty_slot.unload_time
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class OrderPrefixRuleForm(forms.ModelForm):
    class Meta:
        model = OrderPrefixRule
        fields = ["prefix", "starts_on", "ends_on", "next_number", "is_active"]
        widgets = {
            "prefix": forms.TextInput(attrs={"placeholder": "Например: ROBO-"}),
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
            "next_number": forms.NumberInput(attrs={"min": 1}),
        }

    def clean_prefix(self):
        prefix = self.cleaned_data["prefix"].strip()
        if not prefix:
            raise ValidationError("Укажите префикс.")
        return prefix

    def clean(self):
        cleaned_data = super().clean()
        starts_on = cleaned_data.get("starts_on")
        ends_on = cleaned_data.get("ends_on")
        if starts_on and ends_on and starts_on > ends_on:
            self.add_error("ends_on", "Дата окончания не может быть раньше даты начала.")
        return cleaned_data


def production_type_for_filename(filename):
    ext = Path(filename).suffix.lower()
    if ext in VALID_EXTENSIONS[ProductionType.PRINT_3D]:
        return ProductionType.PRINT_3D
    if ext in VALID_EXTENSIONS[ProductionType.LASER_CUT]:
        return ProductionType.LASER_CUT
    return None
