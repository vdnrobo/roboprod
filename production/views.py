import csv
import math
import os
import shutil
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group, User
from django.core.files import File
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import urlencode

from .forms import (
    AdminOrderUpdateForm,
    AdminBulkStatusUpdateForm,
    BulkOrderItemForm,
    BulkOrderCreateForm,
    CountdownForm,
    DutySkipForm,
    DutyScheduleForm,
    DutyScheduleRuleForm,
    DutySlotForm,
    DutySlotScheduleFormSet,
    MaterialForm,
    OrderCreateForm,
    OrderDetailsStepForm,
    OrderFilterForm,
    OrderFileStepForm,
    OrderImageStepForm,
    OrderMessageForm,
    OrderPrefixRuleForm,
    OrderTitleStepForm,
    PinnedAnnouncementForm,
    PriorityPrintBatchForm,
    ProductionSettingsForm,
    RejectionReasonForm,
    RegisterForm,
    SemiPrinterMarkReadyForm,
    production_type_for_filename,
)
from .models import (
    AuditLog,
    Countdown,
    DutySkip,
    DutyPerson,
    DutySchedule,
    DutyScheduleRule,
    DutySlot,
    Material,
    MaterialProductionType,
    Order,
    OrderDraft,
    OrderPrefixRule,
    OrderStatus,
    OrderStatusLog,
    PICKUP_CELL_CHOICES,
    PinnedAnnouncement,
    ProductionSettings,
    ProductionType,
    RejectionReason,
    SEMI_PRINTER_GROUP,
    VALID_EXTENSIONS,
    Weekday,
    admin_duty_people_queryset,
    prune_completed_orders,
    user_is_semi_printer,
)

ORDER_WIZARD_SESSION_KEY = "order_wizard"
BULK_ORDER_SESSION_KEY = "bulk_order_upload"
ORDER_WIZARD_STEPS = ("title", "file", "image", "details", "confirm")
ACTIVE_ORDER_STATUSES = (OrderStatus.PENDING, OrderStatus.IN_PROGRESS)
READY_ORDER_STATUSES = (OrderStatus.READY,)
ARCHIVE_ORDER_STATUSES = (OrderStatus.DONE, OrderStatus.CANCELLED, OrderStatus.REJECTED)
MODEL_DIMENSION_FIELDS = ("model_width", "model_depth", "model_height")
MAX_MODEL_DIMENSION = 1_000_000


def parse_model_dimensions(post_data):
    dimensions = {}
    for field in MODEL_DIMENSION_FIELDS:
        raw_value = (post_data.get(field) or "").strip()
        if raw_value == "":
            dimensions[field] = None
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Некорректные размеры модели.") from exc
        if not math.isfinite(value) or value < 0 or value > MAX_MODEL_DIMENSION:
            raise ValidationError("Некорректные размеры модели.")
        dimensions[field] = round(value, 1)
    if dimensions["model_width"] is None or dimensions["model_depth"] is None:
        return {}
    return dimensions


def apply_model_dimensions(target, dimensions):
    for field in MODEL_DIMENSION_FIELDS:
        setattr(target, field, dimensions.get(field))


def clear_model_dimensions(target):
    for field in MODEL_DIMENSION_FIELDS:
        setattr(target, field, None)
USER_STATS_PERIODS = (
    ("7d", "7 дней"),
    ("30d", "30 дней"),
    ("90d", "90 дней"),
    ("all", "Все время"),
    ("custom", "Произвольный период"),
)
ADMIN_MANAGEMENT_ACTIONS = (
    "create_material",
    "update_material",
    "delete_material",
    "create_rejection_reason",
    "update_rejection_reason",
    "delete_rejection_reason",
    "create_announcement",
    "update_announcement",
    "delete_announcement",
    "create_countdown",
    "update_countdown",
    "delete_countdown",
    "update_production_settings",
    "create_duty_slot",
    "update_duty_slot",
    "delete_duty_slot",
    "create_duty_schedule",
    "update_duty_schedule",
    "delete_duty_schedule",
    "create_duty_schedule_rule",
    "update_duty_schedule_rule",
    "delete_duty_schedule_rule",
    "create_duty_person",
    "update_duty_person",
    "delete_duty_person",
    "create_duty_skip",
    "update_duty_skip",
    "delete_duty_skip",
    "create_prefix_rule",
    "update_prefix_rule",
    "delete_prefix_rule",
    "delete_orders_by_prefix",
    "toggle_admin",
    "toggle_semi_printer",
    "delete_user",
    "bulk_delete_users",
)


def is_admin(user):
    return user.is_authenticated and user.is_staff


def is_semi_printer(user):
    return user_is_semi_printer(user)


def can_access_production_queue(user):
    return is_admin(user) or is_semi_printer(user)


def can_mark_ready(order, user):
    return can_access_production_queue(user) and order.status == OrderStatus.IN_PROGRESS


def material_groups():
    materials = Material.objects.order_by("production_type", "name")
    return {
        "print_3d": materials.filter(production_type=MaterialProductionType.PRINT_3D),
        "laser_cut": materials.filter(production_type=MaterialProductionType.LASER_CUT),
        "both": materials.filter(production_type=MaterialProductionType.BOTH),
    }


def first_user_id():
    return User.objects.order_by("id").values_list("id", flat=True).first()


def can_customer_cancel(order, user):
    return order.user_id == user.id and order.status == OrderStatus.PENDING


def can_customer_confirm_receipt(order, user):
    return order.user_id == user.id and order.status == OrderStatus.READY


def can_customer_self_pickup(order, user, settings_obj=None):
    if order.user_id != user.id:
        return False
    if order.production_type != ProductionType.PRINT_3D:
        return False
    if order.status != OrderStatus.IN_PROGRESS:
        return False
    settings_obj = settings_obj or ProductionSettings.get_solo()
    return settings_obj.allow_self_pickup


def can_add_order_message(order):
    return order.status in ACTIVE_ORDER_STATUSES


def log_status_change(order, changed_by, old_status, new_status, reason=""):
    OrderStatusLog.objects.create(
        order=order,
        order_number=order.pk,
        order_title=order.title,
        changed_by=changed_by,
        old_status=old_status,
        new_status=new_status,
        reason=reason,
    )


def log_audit(actor, action, target, changes=""):
    AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target_type=type(target).__name__,
        target_id=getattr(target, "pk", None),
        target_label=str(target),
        changes=changes,
    )


def human_size(size):
    size = float(size or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            if unit == "Б":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024


def directory_stats(path):
    path = Path(path)
    stats = {
        "path": str(path),
        "exists": path.exists(),
        "file_count": 0,
        "size": 0,
        "size_display": human_size(0),
        "error": "",
    }
    if not stats["exists"]:
        return stats
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                stats["file_count"] += 1
                stats["size"] += entry.stat().st_size
    except OSError as exc:
        stats["error"] = str(exc)
    stats["size_display"] = human_size(stats["size"])
    return stats


def latest_backup_info():
    backup_root = Path(os.environ.get("BACKUP_ROOT", "/opt/lab-production-backups"))
    info = {
        "path": str(backup_root),
        "exists": backup_root.exists(),
        "latest": None,
        "latest_date": None,
        "warning": "",
    }
    if not backup_root.exists():
        info["warning"] = "Директория бэкапов не найдена."
        return info
    try:
        candidates = [entry for entry in backup_root.iterdir() if entry.is_dir()]
        if not candidates:
            info["warning"] = "В директории бэкапов пока нет резервных копий."
            return info
        latest = max(candidates, key=lambda entry: entry.stat().st_mtime)
        info["latest"] = latest.name
        info["latest_date"] = datetime.fromtimestamp(
            latest.stat().st_mtime,
            tz=timezone.get_current_timezone(),
        )
    except OSError as exc:
        info["warning"] = str(exc)
    return info


def local_datetime_start(day):
    return timezone.make_aware(datetime.combine(day, time.min), timezone.get_current_timezone())


def parse_user_stats_period(params):
    period = params.get("period") or "30d"
    valid_periods = {value for value, _ in USER_STATS_PERIODS}
    if period not in valid_periods:
        period = "30d"

    today = timezone.localdate()
    until = local_datetime_start(today + timedelta(days=1))
    since = None
    errors = []

    if period == "7d":
        since = timezone.now() - timedelta(days=7)
    elif period == "30d":
        since = timezone.now() - timedelta(days=30)
    elif period == "90d":
        since = timezone.now() - timedelta(days=90)
    elif period == "custom":
        start_date = parse_date(params.get("start_date") or "")
        end_date = parse_date(params.get("end_date") or "")
        if not start_date:
            errors.append("Укажите дату начала периода.")
        if not end_date:
            errors.append("Укажите дату окончания периода.")
        if start_date and end_date and start_date > end_date:
            errors.append("Дата начала не может быть позже даты окончания.")
        if not errors:
            since = local_datetime_start(start_date)
            until = local_datetime_start(end_date + timedelta(days=1))
    elif period == "all":
        until = None

    label = dict(USER_STATS_PERIODS)[period]
    return {
        "period": period,
        "period_options": USER_STATS_PERIODS,
        "start_date": params.get("start_date") or "",
        "end_date": params.get("end_date") or "",
        "since": since,
        "until": until,
        "label": label,
        "errors": errors,
    }


def apply_period(qs, field_name, period):
    if period["since"]:
        qs = qs.filter(**{f"{field_name}__gte": period["since"]})
    if period["until"]:
        qs = qs.filter(**{f"{field_name}__lt": period["until"]})
    return qs


def apply_date_period(qs, field_name, period):
    if period["since"]:
        qs = qs.filter(**{f"{field_name}__gte": timezone.localtime(period["since"]).date()})
    if period["until"]:
        qs = qs.filter(**{f"{field_name}__lt": timezone.localtime(period["until"]).date()})
    return qs


def format_duration(seconds):
    if not seconds:
        return "нет данных"
    total_minutes = int(seconds // 60)
    days, minutes = divmod(total_minutes, 60 * 24)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} дн.")
    if hours:
        parts.append(f"{hours} ч.")
    if minutes and not days:
        parts.append(f"{minutes} мин.")
    return " ".join(parts) or "меньше минуты"


def average_order_completion_seconds(orders):
    durations = []
    logs = (
        OrderStatusLog.objects.select_related("order")
        .filter(order__in=orders, new_status__in=[OrderStatus.READY, OrderStatus.DONE])
        .order_by("order_id", "created_at", "id")
    )
    seen_order_ids = set()
    for log in logs:
        if log.order_id in seen_order_ids or not log.order:
            continue
        seen_order_ids.add(log.order_id)
        durations.append((log.created_at - log.order.created_at).total_seconds())
    if not durations:
        return None
    return sum(durations) / len(durations)


def current_draft(request):
    draft_id = (request.session.get(ORDER_WIZARD_SESSION_KEY) or {}).get("draft_id")
    if not draft_id:
        return None
    return OrderDraft.objects.filter(pk=draft_id, user=request.user).first()


def ensure_session_key(request):
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def temp_dir(request, kind):
    session_key = ensure_session_key(request)
    return Path(settings.MEDIA_ROOT) / "tmp" / kind / session_key


def temp_file_path(request, kind, stored_name):
    return temp_dir(request, kind) / stored_name


def delete_temp_file(request, kind, file_info):
    if not file_info:
        return
    path = temp_file_path(request, kind, file_info.get("stored_name", ""))
    if path.exists() and path.is_file():
        path.unlink()


def clear_temp_area(request, kind):
    shutil.rmtree(temp_dir(request, kind), ignore_errors=True)


def clear_order_wizard(request):
    clear_temp_area(request, "order_wizard")
    request.session.pop(ORDER_WIZARD_SESSION_KEY, None)
    request.session.modified = True


def clear_bulk_order(request):
    clear_temp_area(request, "bulk_order")
    request.session.pop(BULK_ORDER_SESSION_KEY, None)
    request.session.modified = True


def save_temp_upload(request, kind, upload):
    upload_dir = temp_dir(request, kind)
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(upload.name).suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{ext}"
    path = upload_dir / stored_name
    with path.open("wb") as destination:
        for chunk in upload.chunks():
            destination.write(chunk)
    return {
        "stored_name": stored_name,
        "original_name": upload.name,
    }


def order_type_cards():
    return [
        {
            "value": ProductionType.PRINT_3D,
            "label": ProductionType.PRINT_3D.label,
            "formats": ", ".join(sorted(VALID_EXTENSIONS[ProductionType.PRINT_3D])),
        },
        {
            "value": ProductionType.LASER_CUT,
            "label": ProductionType.LASER_CUT.label,
            "formats": ", ".join(sorted(VALID_EXTENSIONS[ProductionType.LASER_CUT])),
        },
    ]


def next_order_wizard_step(wizard):
    if not wizard.get("title"):
        return "title"
    if not wizard.get("file"):
        return "file"
    if not wizard.get("photo"):
        return "image"
    if not wizard.get("details"):
        return "details"
    return "confirm"


def normalize_order_step(step, wizard):
    if step not in ORDER_WIZARD_STEPS:
        step = next_order_wizard_step(wizard)
    first_missing = next_order_wizard_step(wizard)
    if ORDER_WIZARD_STEPS.index(step) > ORDER_WIZARD_STEPS.index(first_missing):
        return first_missing
    return step


def default_bulk_title(filename):
    return Path(filename).stem[:30]


def safe_filename_part(value):
    cleaned = "".join(char if char.isalnum() else "_" for char in value.strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned or "user"


def order_export_filename(order):
    ext = Path(order.production_file.name).suffix.lower()
    customer_parts = [order.user.last_name, order.user.first_name]
    if not any(part.strip() for part in customer_parts):
        customer_parts = [order.user.username]
    parts = [order.display_number, order.file_suffix, *customer_parts, f"x{order.quantity}"]
    return "_".join(safe_filename_part(part) for part in parts if part.strip()) + ext


@login_required
def order_wizard_photo_preview(request):
    wizard = request.session.get(ORDER_WIZARD_SESSION_KEY) or {}
    photo_info = wizard.get("photo")
    draft = current_draft(request)
    if draft and draft.photo:
        response = FileResponse(draft.photo.open("rb"))
        response["Cache-Control"] = "no-store"
        return response
    if not photo_info:
        raise Http404
    photo_path = temp_file_path(request, "order_wizard", photo_info.get("stored_name", ""))
    if not photo_path.exists() or not photo_path.is_file():
        raise Http404
    response = FileResponse(photo_path.open("rb"))
    response["Cache-Control"] = "no-store"
    return response


@login_required
def order_wizard_source_file(request):
    wizard = request.session.get(ORDER_WIZARD_SESSION_KEY) or {}
    file_info = wizard.get("file")
    draft = current_draft(request)
    if draft and draft.production_file:
        response = FileResponse(draft.production_file.open("rb"), as_attachment=False)
        response["Cache-Control"] = "no-store"
        return response
    if not file_info:
        raise Http404
    file_path = temp_file_path(request, "order_wizard", file_info.get("stored_name", ""))
    if not file_path.exists() or not file_path.is_file():
        raise Http404
    response = FileResponse(file_path.open("rb"), as_attachment=False)
    response["Cache-Control"] = "no-store"
    return response


@login_required
def order_wizard_preview_save(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed."}, status=405)

    wizard = request.session.get(ORDER_WIZARD_SESSION_KEY) or {}
    draft = current_draft(request)
    if not draft or not wizard.get("file"):
        return JsonResponse({"ok": False, "error": "Черновик или файл заказа не найден."}, status=404)
    source_name = (request.POST.get("source_name") or "").strip()
    current_source_name = (wizard.get("file") or {}).get("original_name") or Path(
        draft.production_file.name
    ).name
    if source_name and source_name != current_source_name:
        return JsonResponse({"ok": False, "error": "Файл заказа был заменен. Постройте изображение заново."}, status=409)

    form = OrderImageStepForm(request.POST or None, request.FILES or None)
    if not form.is_valid() or not form.cleaned_data.get("photo"):
        return JsonResponse({"ok": False, "error": "Загрузите корректное изображение."}, status=400)
    try:
        dimensions = parse_model_dimensions(request.POST)
    except ValidationError as error:
        return JsonResponse({"ok": False, "error": error.messages[0]}, status=400)

    upload = form.cleaned_data["photo"]
    delete_temp_file(request, "order_wizard", wizard.get("photo"))
    upload.seek(0)
    wizard["photo"] = save_temp_upload(request, "order_wizard", upload)
    wizard["model_dimensions"] = dimensions
    wizard.pop("details", None)

    if draft.photo:
        draft.photo.delete(save=False)
    upload.seek(0)
    draft.photo.save(upload.name, upload, save=False)
    apply_model_dimensions(draft, dimensions)
    draft.current_step = "details"
    draft.save(update_fields=["photo", *MODEL_DIMENSION_FIELDS, "current_step", "updated_at"])

    request.session[ORDER_WIZARD_SESSION_KEY] = wizard
    return JsonResponse(
        {
            "ok": True,
            "preview_url": reverse("order_wizard_photo_preview"),
        }
    )


def bulk_order_item_from_session(request, token):
    bulk_upload = request.session.get(BULK_ORDER_SESSION_KEY) or {}
    items = bulk_upload.get("items") or []
    for item in items:
        if item.get("token") == token:
            return bulk_upload, item
    return bulk_upload, None


@login_required
@user_passes_test(is_admin)
def bulk_order_source_file(request, token):
    _bulk_upload, item = bulk_order_item_from_session(request, token)
    if not item:
        raise Http404
    file_path = temp_file_path(request, "bulk_order", item.get("stored_name", ""))
    if not file_path.exists() or not file_path.is_file():
        raise Http404
    response = FileResponse(file_path.open("rb"), as_attachment=False)
    response["Cache-Control"] = "no-store"
    return response


@login_required
@user_passes_test(is_admin)
def bulk_order_preview_save(request, token):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed."}, status=405)

    bulk_upload, item = bulk_order_item_from_session(request, token)
    if not item:
        return JsonResponse({"ok": False, "error": "Строка массовой загрузки не найдена."}, status=404)
    source_name = (request.POST.get("source_name") or "").strip()
    current_source_name = item.get("original_name") or ""
    if source_name and source_name != current_source_name:
        return JsonResponse({"ok": False, "error": "Файл строки был заменен. Постройте изображение заново."}, status=409)

    form = OrderImageStepForm(request.POST or None, request.FILES or None)
    if not form.is_valid() or not form.cleaned_data.get("photo"):
        return JsonResponse({"ok": False, "error": "Загрузите корректное изображение."}, status=400)
    try:
        dimensions = parse_model_dimensions(request.POST)
    except ValidationError as error:
        return JsonResponse({"ok": False, "error": error.messages[0]}, status=400)

    upload = form.cleaned_data["photo"]
    delete_temp_file(request, "bulk_order", item.get("photo"))
    upload.seek(0)
    item["photo"] = save_temp_upload(request, "bulk_order", upload)
    item["model_dimensions"] = dimensions
    request.session[BULK_ORDER_SESSION_KEY] = bulk_upload
    return JsonResponse({"ok": True})


@login_required
@user_passes_test(is_admin)
def bulk_order_preview_file(request, token):
    _bulk_upload, item = bulk_order_item_from_session(request, token)
    if not item or not item.get("photo"):
        raise Http404
    photo_path = temp_file_path(request, "bulk_order", item["photo"].get("stored_name", ""))
    if not photo_path.exists() or not photo_path.is_file():
        raise Http404
    response = FileResponse(photo_path.open("rb"), as_attachment=False)
    response["Cache-Control"] = "no-store"
    return response


@login_required
def order_file_download(request, pk):
    order = get_object_or_404(Order.objects.select_related("user"), pk=pk)
    if order.user_id != request.user.id and not request.user.is_staff and not can_mark_ready(order, request.user):
        raise PermissionDenied
    if not order.production_file:
        raise Http404
    try:
        return FileResponse(
            order.production_file.open("rb"),
            as_attachment=True,
            filename=order_export_filename(order),
        )
    except (FileNotFoundError, OSError):
        raise Http404


@login_required
def home(request):
    if can_access_production_queue(request.user):
        return redirect("admin_order_queue")
    return redirect("orders")


def register(request):
    if request.user.is_authenticated:
        return redirect("orders")

    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            first_user = not User.objects.exists()
            user = form.save(commit=False)
            if first_user:
                user.is_staff = True
                user.is_superuser = True
            user.save()
        login(request, user)
        if first_user:
            messages.success(request, "Первый пользователь создан с правами администратора.")
        return redirect("orders")

    return render(request, "registration/register.html", {"form": form})


@login_required
def order_list(request):
    filter_form = OrderFilterForm(request.GET or None, user=request.user)
    orders = Order.objects.filter(user=request.user).select_related("material")
    if filter_form.is_valid():
        status = filter_form.cleaned_data.get("status")
        production_type = filter_form.cleaned_data.get("production_type")
        material = filter_form.cleaned_data.get("material")
        sort = filter_form.cleaned_data.get("sort") or "-created_at"
        if status:
            orders = orders.filter(status=status)
        if production_type:
            orders = orders.filter(production_type=production_type)
        if material:
            orders = orders.filter(material=material)
        allowed_sorts = {
            "-created_at": "-created_at",
            "created_at": "created_at",
            "status": "status",
            "production_type": "production_type",
            "material": "material__name",
        }
        orders = orders.order_by(allowed_sorts.get(sort, "-created_at"), "-pk")
    ready_orders = orders.filter(status=OrderStatus.READY)
    drafts = OrderDraft.objects.filter(user=request.user).select_related("material")
    fresh_since = timezone.now() - timedelta(hours=24)
    active_statuses = (OrderStatus.PENDING, OrderStatus.IN_PROGRESS)
    active_orders = [order for order in orders if order.status in active_statuses]
    for order in active_orders:
        order.fresh_queue_ahead_count = Order.objects.filter(
            status__in=active_statuses,
            created_at__gte=fresh_since,
            created_at__lt=order.created_at,
        ).count()
    production_settings = ProductionSettings.get_solo()
    self_pickup_orders = [
        order for order in orders if can_customer_self_pickup(order, request.user, production_settings)
    ]
    return render(
        request,
        "production/order_list.html",
        {
            "orders": orders,
            "ready_orders": ready_orders,
            "self_pickup_orders": self_pickup_orders,
            "drafts": drafts,
            "filter_form": filter_form,
        },
    )


@login_required
def order_detail(request, pk):
    order = get_object_or_404(Order.objects.select_related("material", "user"), pk=pk)
    if order.user != request.user and not request.user.is_staff:
        raise PermissionDenied
    admin_form = AdminOrderUpdateForm(instance=order) if request.user.is_staff else None
    status_logs = order.status_logs.select_related("changed_by")
    order_messages = order.messages.select_related("author")
    can_add_message = can_add_order_message(order)
    production_settings = ProductionSettings.get_solo()
    return render(
        request,
        "production/order_detail.html",
        {
            "order": order,
            "admin_form": admin_form,
            "message_form": OrderMessageForm() if can_add_message else None,
            "order_messages": order_messages,
            "status_logs": status_logs,
            "can_add_message": can_add_message,
            "can_cancel_order": can_customer_cancel(order, request.user),
            "can_confirm_receipt": can_customer_confirm_receipt(order, request.user),
            "can_self_pickup": can_customer_self_pickup(order, request.user, production_settings),
            "can_update_priority_batch": (
                request.user.is_staff
                and order.is_priority
                and bool(order.priority_remaining_quantity)
                and order.status in ACTIVE_ORDER_STATUSES
            ),
        },
    )


@login_required
def order_message_create(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if order.user_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied
    if request.method != "POST":
        return redirect("order_detail", pk=order.pk)
    if not can_add_order_message(order):
        messages.error(request, "Диалог доступен только для заказов в ожидании и в работе.")
        return redirect("order_detail", pk=order.pk)

    form = OrderMessageForm(request.POST, request.FILES)
    if form.is_valid():
        order_message = form.save(commit=False)
        order_message.order = order
        order_message.author = request.user
        order_message.save()
        log_audit(request.user, "create_order_message", order, "Добавлено сообщение в диалог заказа.")
        messages.success(request, "Сообщение добавлено.")
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect("order_detail", pk=order.pk)


@login_required
def order_cancel(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if order.user_id != request.user.id:
        raise PermissionDenied

    if request.method != "POST":
        return redirect("order_detail", pk=order.pk)

    if not can_customer_cancel(order, request.user):
        messages.error(request, "Этот заказ уже нельзя отменить.")
        return redirect("order_detail", pk=order.pk)

    old_status = order.status
    with transaction.atomic():
        order.status = OrderStatus.CANCELLED
        order.rejection_reason = ""
        order.save(update_fields=["status", "rejection_reason", "updated_at"])
        log_status_change(
            order,
            request.user,
            old_status,
            OrderStatus.CANCELLED,
            "Отменено заказчиком.",
        )
    messages.success(request, f"Заказ #{order.display_number} отменен.")
    return redirect("order_detail", pk=order.pk)


@login_required
def order_cancel_confirm(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if order.user_id != request.user.id:
        raise PermissionDenied
    if not can_customer_cancel(order, request.user):
        messages.error(request, "Этот заказ уже нельзя отменить.")
        return redirect("order_detail", pk=order.pk)
    return render(request, "production/order_cancel_confirm.html", {"order": order})


@login_required
def order_repeat(request, pk):
    source = get_object_or_404(Order.objects.select_related("material"), pk=pk)
    if source.user_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied
    if request.method != "POST":
        return redirect("order_detail", pk=source.pk)

    with transaction.atomic():
        order = Order(
            user=request.user,
            title=source.title,
            production_type=source.production_type,
            material=source.material,
            quantity=source.quantity,
            comment=source.comment,
            model_width=source.model_width,
            model_depth=source.model_depth,
            model_height=source.model_height,
            status=OrderStatus.PENDING,
        )
        if source.photo:
            with source.photo.open("rb") as photo:
                order.photo.save(Path(source.photo.name).name, File(photo), save=False)
        if source.production_file:
            with source.production_file.open("rb") as production_file:
                order.production_file.save(
                    Path(source.production_file.name).name,
                    File(production_file),
                    save=False,
                )
        order.save()
        log_audit(request.user, "repeat_order", order, f"Повтор заказа #{source.display_number}")
    messages.success(
        request,
        f"Создан повтор заказа #{source.display_number}: новый заказ #{order.display_number}.",
    )
    return redirect("order_detail", pk=order.pk)


@login_required
def order_confirm_receipt(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if order.user_id != request.user.id:
        raise PermissionDenied

    if request.method != "POST":
        return redirect("order_detail", pk=order.pk)

    if order.status != OrderStatus.READY:
        messages.error(request, "Получение можно подтвердить только для заказа, готового к выдаче.")
        return redirect("orders")

    old_status = order.status
    with transaction.atomic():
        order.status = OrderStatus.DONE
        order.pickup_cell = None
        order.save(update_fields=["status", "pickup_cell", "updated_at"])
        log_status_change(
            order,
            request.user,
            old_status,
            OrderStatus.DONE,
            "Получение подтверждено заказчиком.",
        )
        prune_completed_orders(keep=30)
    messages.success(request, f"Получение заказа #{order.display_number} подтверждено.")
    return redirect("orders")


@login_required
def order_self_pickup(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if order.user_id != request.user.id:
        raise PermissionDenied

    if request.method != "POST":
        return redirect("order_detail", pk=order.pk)

    if not can_customer_self_pickup(order, request.user):
        messages.error(request, "Самосъём для этого заказа сейчас недоступен.")
        return redirect("order_detail", pk=order.pk)

    old_status = order.status
    with transaction.atomic():
        order.status = OrderStatus.DONE
        order.pickup_cell = None
        order.save(update_fields=["status", "pickup_cell", "updated_at"])
        log_status_change(
            order,
            request.user,
            old_status,
            OrderStatus.DONE,
            "Самосъём: заказчик самостоятельно снял деталь с принтера.",
        )
        log_audit(request.user, "self_pickup_order", order)
        prune_completed_orders(keep=30)
    messages.success(request, f"Самосъём заказа #{order.display_number} подтвержден. Заказ перемещен в архив.")
    return redirect("orders")


@login_required
def order_create(request):
    valid_types = {choice[0] for choice in ProductionType.choices}
    selected_type = request.GET.get("production_type")

    if request.GET.get("reset"):
        draft = current_draft(request)
        if draft:
            draft.delete()
        clear_order_wizard(request)
        return redirect("orders")

    if selected_type in valid_types:
        clear_order_wizard(request)
        draft = OrderDraft.objects.create(
            user=request.user,
            production_type=selected_type,
            current_step="title",
        )
        request.session[ORDER_WIZARD_SESSION_KEY] = {
            "production_type": selected_type,
            "draft_id": draft.pk,
        }
        return redirect(f"{reverse('order_create')}?step=title")

    wizard = request.session.get(ORDER_WIZARD_SESSION_KEY) or {}
    production_type = wizard.get("production_type")
    draft = current_draft(request)
    if production_type not in valid_types:
        return render(
            request,
            "production/order_type_select.html",
            {"production_types": order_type_cards()},
        )

    step = normalize_order_step(request.GET.get("step"), wizard)
    requested_step = request.GET.get("step")
    if requested_step and requested_step != step:
        return redirect(f"{reverse('order_create')}?step={step}")

    production_type_label = dict(ProductionType.choices)[production_type]
    allowed_extensions = ", ".join(sorted(VALID_EXTENSIONS[production_type]))
    form = None

    if step == "title":
        form = OrderTitleStepForm(
            request.POST or None,
            initial={"title": wizard.get("title", "")},
        )
        if request.method == "POST" and form.is_valid():
            wizard["title"] = form.cleaned_data["title"]
            if draft:
                draft.title = form.cleaned_data["title"]
                draft.current_step = "file"
                draft.save(update_fields=["title", "current_step", "updated_at"])
            request.session[ORDER_WIZARD_SESSION_KEY] = wizard
            return redirect(f"{reverse('order_create')}?step=file")

    elif step == "file":
        form = OrderFileStepForm(
            request.POST or None,
            request.FILES or None,
            production_type=production_type,
        )
        if request.method == "POST" and form.is_valid():
            delete_temp_file(request, "order_wizard", wizard.get("file"))
            delete_temp_file(request, "order_wizard", wizard.get("photo"))
            upload = form.cleaned_data["production_file"]
            wizard["file"] = save_temp_upload(request, "order_wizard", upload)
            wizard.pop("photo", None)
            wizard.pop("model_dimensions", None)
            wizard.pop("details", None)
            if draft:
                if draft.photo:
                    draft.photo.delete(save=False)
                if draft.production_file:
                    draft.production_file.delete(save=False)
                upload.seek(0)
                draft.production_file.save(upload.name, upload, save=False)
                clear_model_dimensions(draft)
                draft.current_step = "image"
                draft.save(update_fields=["photo", "production_file", *MODEL_DIMENSION_FIELDS, "current_step", "updated_at"])
            request.session[ORDER_WIZARD_SESSION_KEY] = wizard
            return redirect(f"{reverse('order_create')}?step=image")

    elif step == "image":
        form = OrderImageStepForm(
            request.POST if request.method == "POST" else None,
            request.FILES if request.method == "POST" else None,
        )
        has_saved_photo = bool(wizard.get("photo") or (draft and draft.photo))
        if request.method == "POST" and has_saved_photo and "photo" not in request.FILES:
            return redirect(f"{reverse('order_create')}?step=details")
        if request.method == "POST" and "photo" not in request.FILES:
            form.is_valid()
            form.add_error("photo", "Дождитесь автоматического изображения или загрузите фото вручную.")
        elif request.method == "POST" and form.is_valid():
            upload = form.cleaned_data.get("photo")
            if upload:
                delete_temp_file(request, "order_wizard", wizard.get("photo"))
                upload.seek(0)
                wizard["photo"] = save_temp_upload(request, "order_wizard", upload)
                wizard.pop("model_dimensions", None)
                wizard.pop("details", None)
                if draft:
                    if draft.photo:
                        draft.photo.delete(save=False)
                    upload.seek(0)
                    draft.photo.save(upload.name, upload, save=False)
                    clear_model_dimensions(draft)
                    draft.current_step = "details"
                    draft.save(update_fields=["photo", *MODEL_DIMENSION_FIELDS, "current_step", "updated_at"])
                request.session[ORDER_WIZARD_SESSION_KEY] = wizard
                return redirect(f"{reverse('order_create')}?step=details")
            form.add_error("photo", "Дождитесь автоматического изображения или загрузите фото вручную.")

    elif step == "details":
        form = OrderDetailsStepForm(
            request.POST or None,
            production_type=production_type,
            user=request.user,
            initial={
                "material": draft.material_id if draft else None,
                "quantity": draft.quantity if draft else 1,
                "comment": draft.comment if draft else "",
                "is_priority": draft.is_priority if draft else False,
            },
        )
        if request.method == "POST" and form.is_valid():
            if draft:
                draft.material = form.cleaned_data["material"]
                draft.quantity = form.cleaned_data["quantity"]
                draft.comment = form.cleaned_data["comment"]
                draft.is_priority = bool(form.cleaned_data.get("is_priority"))
                draft.current_step = "confirm"
                draft.save(update_fields=["material", "quantity", "comment", "is_priority", "current_step", "updated_at"])
                wizard["details"] = True
                request.session[ORDER_WIZARD_SESSION_KEY] = wizard
                return redirect(f"{reverse('order_create')}?step=confirm")

            messages.error(request, "Черновик заказа не найден. Создайте заказ заново.")
            clear_order_wizard(request)
            return redirect("order_create")

            photo_info = wizard.get("photo") or {}
            file_info = wizard.get("file") or {}
            use_draft_files = bool(draft and draft.photo and draft.production_file)
            photo_path = temp_file_path(request, "order_wizard", photo_info.get("stored_name", ""))
            file_path = temp_file_path(request, "order_wizard", file_info.get("stored_name", ""))
            if not use_draft_files and (not photo_path.exists() or not file_path.exists()):
                messages.error(request, "Временные файлы заказа не найдены. Создайте заказ заново.")
                clear_order_wizard(request)
                return redirect("order_create")

            with transaction.atomic():
                is_priority = bool(form.cleaned_data.get("is_priority"))
                quantity = form.cleaned_data["quantity"]
                order = Order(
                    user=request.user,
                    title=wizard.get("title") or (draft.title if draft else ""),
                    production_type=production_type,
                    material=form.cleaned_data["material"],
                    quantity=quantity,
                    comment=form.cleaned_data["comment"],
                    is_priority=is_priority,
                    priority_remaining_quantity=quantity if is_priority else None,
                    status=OrderStatus.PENDING,
                )
                if draft:
                    apply_model_dimensions(
                        order,
                        {field: getattr(draft, field) for field in MODEL_DIMENSION_FIELDS},
                    )
                else:
                    apply_model_dimensions(order, wizard.get("model_dimensions") or {})
                if use_draft_files:
                    with draft.photo.open("rb") as photo_file:
                        order.photo.save(Path(draft.photo.name).name, File(photo_file), save=False)
                    with draft.production_file.open("rb") as production_file:
                        order.production_file.save(
                            Path(draft.production_file.name).name,
                            File(production_file),
                            save=False,
                        )
                else:
                    with photo_path.open("rb") as photo_file:
                        order.photo.save(photo_info["original_name"], File(photo_file), save=False)
                    with file_path.open("rb") as production_file:
                        order.production_file.save(file_info["original_name"], File(production_file), save=False)
                order.save()
                audit_text = "Создан заказ из черновика."
                if order.is_priority:
                    audit_text += f" Приоритетный заказ, остаток: {order.priority_remaining_quantity}."
                log_audit(request.user, "create_order", order, audit_text)

            if draft:
                draft.delete()
            clear_order_wizard(request)
            if order.is_priority:
                messages.success(request, "Приоритетный заказ создан и добавлен в очередь.")
            else:
                messages.success(request, "Заказ создан и добавлен в очередь.")
            return redirect("order_detail", pk=order.pk)

    elif step == "confirm":
        if not draft:
            messages.error(request, "Черновик заказа не найден. Создайте заказ заново.")
            clear_order_wizard(request)
            return redirect("order_create")
        if not draft.material_id:
            messages.error(request, "Выберите материал перед отправкой заказа.")
            wizard.pop("details", None)
            request.session[ORDER_WIZARD_SESSION_KEY] = wizard
            return redirect(f"{reverse('order_create')}?step=details")
        if not draft.production_file:
            messages.error(request, "Производственный файл не найден. Загрузите файл заново.")
            wizard.pop("file", None)
            wizard.pop("photo", None)
            wizard.pop("details", None)
            request.session[ORDER_WIZARD_SESSION_KEY] = wizard
            return redirect(f"{reverse('order_create')}?step=file")
        if not draft.photo:
            messages.error(request, "Изображение модели не найдено. Проверьте изображение заново.")
            wizard.pop("photo", None)
            wizard.pop("details", None)
            request.session[ORDER_WIZARD_SESSION_KEY] = wizard
            return redirect(f"{reverse('order_create')}?step=image")

        if request.method == "POST":
            with transaction.atomic():
                is_priority = bool(draft.is_priority and request.user.is_staff)
                order = Order(
                    user=request.user,
                    title=wizard.get("title") or draft.title,
                    production_type=production_type,
                    material=draft.material,
                    quantity=draft.quantity,
                    comment=draft.comment,
                    is_priority=is_priority,
                    priority_remaining_quantity=draft.quantity if is_priority else None,
                    status=OrderStatus.PENDING,
                )
                apply_model_dimensions(
                    order,
                    {field: getattr(draft, field) for field in MODEL_DIMENSION_FIELDS},
                )
                with draft.photo.open("rb") as photo_file:
                    order.photo.save(Path(draft.photo.name).name, File(photo_file), save=False)
                with draft.production_file.open("rb") as production_file:
                    order.production_file.save(
                        Path(draft.production_file.name).name,
                        File(production_file),
                        save=False,
                    )
                order.save()
                audit_text = "Создан заказ из черновика после подтверждения."
                if order.is_priority:
                    audit_text += f" Приоритетный заказ, остаток: {order.priority_remaining_quantity}."
                log_audit(request.user, "create_order", order, audit_text)

            draft.delete()
            clear_order_wizard(request)
            if order.is_priority:
                messages.success(request, "Приоритетный заказ создан и добавлен в очередь.")
            else:
                messages.success(request, "Заказ создан и добавлен в очередь.")
            return redirect("order_detail", pk=order.pk)

    return render(
        request,
        "production/order_wizard.html",
        {
            "form": form,
            "step": step,
            "steps": ORDER_WIZARD_STEPS,
            "wizard": wizard,
            "photo_preview_url": reverse("order_wizard_photo_preview")
            if wizard.get("photo") or (draft and draft.photo)
            else "",
            "file_source_url": reverse("order_wizard_source_file")
            if wizard.get("file") or (draft and draft.production_file)
            else "",
            "preview_save_url": reverse("order_wizard_preview_save"),
            "original_file_name": (
                (wizard.get("file") or {}).get("original_name")
                or (Path(draft.production_file.name).name if draft and draft.production_file else "")
            ),
            "draft": draft,
            "selected_type_label": production_type_label,
            "allowed_extensions": allowed_extensions,
        },
    )


@login_required
def order_draft_continue(request, pk):
    draft = get_object_or_404(OrderDraft, pk=pk, user=request.user)
    wizard = {
        "production_type": draft.production_type,
        "draft_id": draft.pk,
    }
    if draft.title:
        wizard["title"] = draft.title
    if draft.photo:
        wizard["photo"] = {"stored_name": "", "original_name": Path(draft.photo.name).name}
    if draft.production_file:
        wizard["file"] = {
            "stored_name": "",
            "original_name": Path(draft.production_file.name).name,
        }
    if draft.material_id:
        wizard["details"] = True
    request.session[ORDER_WIZARD_SESSION_KEY] = wizard
    step = normalize_order_step(draft.current_step, wizard)
    return redirect(f"{reverse('order_create')}?step={step}")


@login_required
def order_draft_delete(request, pk):
    draft = get_object_or_404(OrderDraft, pk=pk, user=request.user)
    if request.method == "POST":
        draft.delete()
        messages.success(request, "Черновик удален.")
    return redirect("orders")


@login_required
@user_passes_test(can_access_production_queue)
def admin_order_queue(request):
    return admin_order_list(request, list_kind="queue")


@login_required
@user_passes_test(is_admin)
def admin_order_ready(request):
    return admin_order_list(request, list_kind="ready")


@login_required
@user_passes_test(is_admin)
def admin_order_archive(request):
    return admin_order_list(request, list_kind="archive")


def admin_order_queryset(request, list_kind="queue"):
    orders = Order.objects.select_related("material", "user")
    status_groups = {
        "queue": ACTIVE_ORDER_STATUSES,
        "ready": READY_ORDER_STATUSES,
        "archive": ARCHIVE_ORDER_STATUSES,
    }
    allowed_statuses = status_groups.get(list_kind, ACTIVE_ORDER_STATUSES)
    if is_semi_printer(request.user):
        allowed_statuses = (OrderStatus.IN_PROGRESS,)
    orders = orders.filter(status__in=allowed_statuses)

    production_type = request.GET.get("production_type", "")
    status = request.GET.get("status", "")
    material = request.GET.get("material", "")
    customer = request.GET.get("customer", "")
    default_sort = "created_at" if list_kind == "queue" else "-created_at"
    sort = request.GET.get("sort", default_sort)

    if production_type:
        orders = orders.filter(production_type=production_type)
    if status in allowed_statuses:
        orders = orders.filter(status=status)
    if material:
        orders = orders.filter(material_id=material)
    if customer:
        orders = orders.filter(user_id=customer)

    allowed_sorts = {
        "created_at": "created_at",
        "-created_at": "-created_at",
        "production_type": "production_type",
        "status": "status",
        "material": "material__name",
        "customer": "user__last_name",
    }
    orders = orders.order_by("-is_priority", allowed_sorts.get(sort, default_sort), "-pk")
    return orders, allowed_statuses, {
        "production_type": production_type,
        "status": status if status in allowed_statuses else "",
        "material": material,
        "customer": customer,
        "sort": sort,
    }


def admin_order_list(request, list_kind="queue"):
    orders, allowed_statuses, filters = admin_order_queryset(request, list_kind)

    query = request.GET.copy()
    semi_printer = is_semi_printer(request.user)
    is_archive = list_kind == "archive"
    is_ready = list_kind == "ready"
    page_titles = {
        "queue": "Очередь производства",
        "ready": "Готовы к выдаче",
        "archive": "Архив заказов",
    }
    page_descriptions = {
        "queue": "Активные заказы: ожидающие и в работе.",
        "ready": "Изготовленные заказы, ожидающие подтверждения получения заказчиком.",
        "archive": "Выполненные, отмененные и отклоненные заказы.",
    }
    context = {
        "orders": orders,
        "materials": Material.objects.filter(is_active=True),
        "rejection_reasons": RejectionReason.objects.filter(is_active=True).order_by("text"),
        "customers": User.objects.filter(orders__isnull=False).distinct().order_by(
            "last_name", "first_name", "username"
        ),
        "production_types": ProductionType.choices,
        "statuses": [choice for choice in OrderStatus.choices if choice[0] in allowed_statuses],
        "update_statuses": OrderStatus.choices,
        "pickup_cell_choices": PICKUP_CELL_CHOICES,
        "is_semi_printer": semi_printer,
        "is_archive": is_archive,
        "is_ready": is_ready,
        "list_kind": list_kind,
        "page_title": page_titles.get(list_kind, page_titles["queue"]),
        "page_description": page_descriptions.get(list_kind, page_descriptions["queue"]),
        "filters": {
            **filters,
        },
        "reset_url": reverse(
            {
                "queue": "admin_order_queue",
                "ready": "admin_order_ready",
                "archive": "admin_order_archive",
            }.get(list_kind, "admin_order_queue")
        ),
        "archive_url": reverse("admin_order_archive"),
        "queue_url": reverse("admin_order_queue"),
        "ready_url": reverse("admin_order_ready"),
        "query_string": urlencode(query),
    }
    return render(request, "production/admin_order_queue.html", context)


@login_required
@user_passes_test(can_access_production_queue)
def production_order_mark_ready(request, pk):
    next_url = request.POST.get("next") or reverse("admin_order_queue")
    if request.method != "POST":
        return redirect(next_url)

    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=pk)
        if not can_mark_ready(order, request.user):
            messages.error(request, "Можно отметить готовым только заказ в работе.")
            return redirect(next_url)

        form = SemiPrinterMarkReadyForm(request.POST)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect(next_url)

        old_status = order.status
        order.status = OrderStatus.READY
        order.pickup_cell = form.cleaned_data["pickup_cell"]
        order.rejection_reason = ""
        order.save(update_fields=["status", "pickup_cell", "rejection_reason", "updated_at"])
        log_status_change(order, request.user, old_status, OrderStatus.READY)
        log_audit(
            request.user,
            "semi_printer_mark_ready",
            order,
            f"Полупечатник отметил заказ готовым; ячейка: {order.pickup_cell or 'без ячейки'}",
        )

    messages.success(request, f"Заказ #{order.display_number} перенесен в готовые к выдаче.")
    return redirect(next_url)


@login_required
@user_passes_test(is_admin)
def admin_order_export(request, list_kind="queue"):
    orders, _, _ = admin_order_queryset(request, list_kind)
    filename_map = {
        "queue": "queue.csv",
        "ready": "ready.csv",
        "archive": "archive.csv",
    }
    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f'attachment; filename="{filename_map.get(list_kind, "orders.csv")}"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(
        [
            "Номер",
            "Название",
            "Заказчик",
            "Тип",
            "Материал",
            "Цвет",
            "Количество",
            "Приоритет",
            "Осталось копий к запуску",
            "Ячейка выдачи",
            "Статус",
            "Создан",
            "Комментарий администратора",
        ]
    )
    for order in orders:
        customer = f"{order.user.last_name} {order.user.first_name}".strip() or order.user.username
        writer.writerow(
            [
                order.display_number,
                order.title,
                customer,
                order.get_production_type_display(),
                order.material.name if order.material else "",
                order.material.color if order.material else "",
                order.quantity,
                "Да" if order.is_priority else "Нет",
                order.priority_remaining_quantity if order.is_priority else "",
                order.pickup_cell or "",
                order.get_status_display(),
                order.created_at.strftime("%d.%m.%Y %H:%M"),
                order.admin_comment,
            ]
        )
    log_audit(request.user, "export_orders", request.user, f"Экспорт {list_kind}: {orders.count()} строк")
    return response


@login_required
@user_passes_test(is_admin)
def admin_dashboard(request):
    now = timezone.now()
    stale_since = now - timedelta(hours=24)
    archive_since = now - timedelta(days=7)
    active_stale = Order.objects.filter(
        status__in=ACTIVE_ORDER_STATUSES,
        created_at__lt=stale_since,
    ).count()
    status_counts = {
        "pending": Order.objects.filter(status=OrderStatus.PENDING).count(),
        "in_progress": Order.objects.filter(status=OrderStatus.IN_PROGRESS).count(),
        "ready": Order.objects.filter(status=OrderStatus.READY).count(),
        "priority": Order.objects.filter(
            is_priority=True,
            status__in=ACTIVE_ORDER_STATUSES,
            priority_remaining_quantity__gt=0,
        ).count(),
        "archive_7d": Order.objects.filter(
            status__in=ARCHIVE_ORDER_STATUSES,
            updated_at__gte=archive_since,
        ).count(),
        "stale": active_stale,
    }
    labels = dict(ProductionType.choices)
    production_stats = [
        {"label": labels.get(row["production_type"], row["production_type"]), "count": row["count"]}
        for row in Order.objects.values("production_type").annotate(count=Count("id")).order_by("-count")
    ]
    material_stats = (
        Order.objects.filter(material__isnull=False)
        .values("material__name")
        .annotate(count=Count("id"))
        .order_by("-count")[:8]
    )
    rejection_stats = (
        Order.objects.filter(status=OrderStatus.REJECTED)
        .exclude(rejection_reason="")
        .values("rejection_reason")
        .annotate(count=Count("id"))
        .order_by("-count")[:8]
    )
    return render(
        request,
        "production/admin_dashboard.html",
        {
            "status_counts": status_counts,
            "production_stats": production_stats,
            "material_stats": material_stats,
            "rejection_stats": rejection_stats,
            "recent_audit": AuditLog.objects.select_related("actor")[:10],
        },
    )


@login_required
@user_passes_test(is_admin)
def system_health(request):
    disk_usage = shutil.disk_usage(settings.BASE_DIR)
    database = settings.DATABASES["default"]
    sqlite_size = None
    db_name = database.get("NAME")
    if "sqlite" in database.get("ENGINE", "") and db_name:
        db_path = Path(db_name)
        if db_path.exists():
            sqlite_size = human_size(db_path.stat().st_size)
    return render(
        request,
        "production/system_health.html",
        {
            "disk": {
                "total": human_size(disk_usage.total),
                "used": human_size(disk_usage.used),
                "free": human_size(disk_usage.free),
            },
            "media_stats": directory_stats(settings.MEDIA_ROOT),
            "static_stats": directory_stats(settings.STATIC_ROOT),
            "database": {
                "engine": database.get("ENGINE", ""),
                "name": db_name,
                "sqlite_size": sqlite_size,
            },
            "backup": latest_backup_info(),
        },
    )


@login_required
@user_passes_test(is_admin)
def audit_log_list(request):
    logs = AuditLog.objects.select_related("actor")[:300]
    return render(request, "production/audit_log_list.html", {"logs": logs})


@login_required
@user_passes_test(is_admin)
def admin_order_update(request, pk):
    order = get_object_or_404(Order, pk=pk)
    old_status = order.status
    old_material_id = order.material_id
    old_pickup_cell = order.pickup_cell
    old_admin_comment = order.admin_comment
    form = AdminOrderUpdateForm(request.POST or None, instance=order)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            updated_order = form.save()
            audit_changes = []
            if old_status != updated_order.status:
                log_status_change(
                    updated_order,
                    request.user,
                    old_status,
                    updated_order.status,
                    updated_order.rejection_reason
                    if updated_order.status == OrderStatus.REJECTED
                    else "",
                )
                audit_changes.append(f"Статус: {old_status} -> {updated_order.status}")
            if old_material_id != updated_order.material_id:
                audit_changes.append("Изменен материал")
            if old_pickup_cell != updated_order.pickup_cell:
                audit_changes.append("Изменена ячейка выдачи")
            if old_admin_comment != updated_order.admin_comment:
                audit_changes.append("Изменен комментарий администратора")
            if audit_changes:
                log_audit(request.user, "update_order", updated_order, "; ".join(audit_changes))
        messages.success(request, f"Заказ #{order.display_number} обновлен.")
    else:
        messages.error(request, "Не удалось обновить заказ.")
    return redirect(request.POST.get("next") or "admin_order_queue")


@login_required
@user_passes_test(is_admin)
def admin_order_quick_status(request, pk):
    next_url = request.POST.get("next") or reverse("admin_order_queue")
    if request.method != "POST":
        return redirect(next_url)

    new_status = request.POST.get("status")
    if new_status not in {OrderStatus.IN_PROGRESS, OrderStatus.READY, OrderStatus.REJECTED}:
        messages.error(request, "Недоступное быстрое действие.")
        return redirect(next_url)

    reason = ""
    if new_status == OrderStatus.REJECTED:
        standard_reason_id = request.POST.get("standard_rejection_reason")
        manual_reason = (request.POST.get("rejection_reason") or "").strip()
        standard_reason = None
        if standard_reason_id:
            standard_reason = RejectionReason.objects.filter(
                pk=standard_reason_id,
                is_active=True,
            ).first()
        reason = manual_reason or (standard_reason.text if standard_reason else "")
        if not reason:
            messages.error(request, "Укажите причину отклонения заказа.")
            return redirect(next_url)

    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=pk)
        old_status = order.status
        if old_status == new_status:
            messages.info(request, f"Заказ #{order.display_number} уже имеет выбранный статус.")
            return redirect(next_url)
        order.status = new_status
        order.rejection_reason = reason if new_status == OrderStatus.REJECTED else ""
        if new_status != OrderStatus.READY:
            order.pickup_cell = None
        order.save(update_fields=["status", "rejection_reason", "pickup_cell", "updated_at"])
        log_status_change(
            order,
            request.user,
            old_status,
            new_status,
            reason if new_status == OrderStatus.REJECTED else "",
        )
        log_audit(
            request.user,
            "quick_status_update",
            order,
            f"Быстрое действие: {old_status} -> {new_status}",
        )

    messages.success(request, f"Заказ #{order.display_number} обновлен.")
    return redirect(next_url)


@login_required
@user_passes_test(is_admin)
def admin_order_priority_print_batch(request, pk):
    if request.method != "POST":
        return redirect("admin_order_queue")

    next_url = request.POST.get("next") or reverse("admin_order_queue")
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=pk)
        old_status = order.status
        old_remaining = order.priority_remaining_quantity
        form = PriorityPrintBatchForm(request.POST, order=order)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect(next_url)

        copies = form.cleaned_data["copies"]
        try:
            order.register_priority_print_batch(copies)
            order.save(update_fields=["status", "priority_remaining_quantity", "pickup_cell", "updated_at"])
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
            return redirect(next_url)

        if old_status != order.status:
            log_status_change(
                order,
                request.user,
                old_status,
                order.status,
                f"Поставлено на печать {copies} коп.",
            )
        log_audit(
            request.user,
            "priority_print_batch",
            order,
            f"Поставлено на печать: {copies}; остаток: {old_remaining} -> {order.priority_remaining_quantity}",
        )

    messages.success(request, f"У заказа #{order.display_number} поставлено на печать копий: {copies}.")
    return redirect(next_url)


@login_required
@user_passes_test(is_admin)
def admin_orders_bulk_status_update(request):
    if request.method != "POST":
        return redirect("admin_order_queue")

    form = AdminBulkStatusUpdateForm(request.POST)
    next_url = request.POST.get("next") or reverse("admin_order_queue")
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect(next_url)

    selected_orders = form.cleaned_data["order_ids"].select_for_update()
    new_status = form.cleaned_data["status"]
    reason = form.cleaned_data["rejection_reason"]
    changed_count = 0

    with transaction.atomic():
        for order in selected_orders:
            old_status = order.status
            if old_status == new_status:
                continue
            order.status = new_status
            order.rejection_reason = reason if new_status == OrderStatus.REJECTED else ""
            if new_status != OrderStatus.READY:
                order.pickup_cell = None
            order.save(update_fields=["status", "rejection_reason", "pickup_cell", "updated_at"])
            log_status_change(
                order,
                request.user,
                old_status,
                new_status,
                reason if new_status == OrderStatus.REJECTED else "",
            )
            changed_count += 1

    if changed_count:
        log_audit(request.user, "bulk_status_update", request.user, f"Обновлено заказов: {changed_count}; статус: {new_status}")
        messages.success(request, f"Обновлено заказов: {changed_count}.")
    else:
        messages.info(request, "Статусы выбранных заказов уже были такими.")
    return redirect(next_url)


@login_required
@user_passes_test(is_admin)
def bulk_order_create(request):
    if request.GET.get("reset"):
        clear_bulk_order(request)
        return redirect("admin_order_queue")

    bulk_upload = request.session.get(BULK_ORDER_SESSION_KEY) or {}
    edit_mode = request.GET.get("step") == "edit" and bulk_upload.get("items")

    if edit_mode:
        items = bulk_upload["items"]
        forms = []

        if request.method == "POST" and request.POST.get("action") == "cancel":
            clear_bulk_order(request)
            return redirect("admin_order_queue")

        for index, item in enumerate(items):
            prefix = f"item-{index}"
            form = BulkOrderItemForm(
                request.POST or None,
                request.FILES or None,
                prefix=prefix,
                production_type=item["production_type"],
                initial={
                    "file_token": item["token"],
                    "title": item["title"],
                    "material": item.get("material"),
                    "quantity": item.get("quantity", 1),
                },
            )
            forms.append({"form": form, "item": item, "prefix": prefix})

        if request.method == "POST" and request.POST.get("action") == "create":
            all_valid = all(row["form"].is_valid() for row in forms)
            if all_valid:
                for row in forms:
                    if not row["item"].get("photo") and not row["form"].cleaned_data.get("photo"):
                        row["form"].add_error("photo", "Дождитесь автоматического изображения или загрузите фото вручную.")
                        all_valid = False
            tokens_match = all(
                row["form"].cleaned_data.get("file_token") == row["item"]["token"]
                for row in forms
                if row["form"].is_valid()
            )
            if all_valid and tokens_match:
                created = 0
                missing_file = next(
                    (
                        row["item"]["original_name"]
                        for row in forms
                        if not temp_file_path(request, "bulk_order", row["item"]["stored_name"]).exists()
                        or (
                            row["item"].get("photo")
                            and not temp_file_path(request, "bulk_order", row["item"]["photo"]["stored_name"]).exists()
                        )
                    ),
                    None,
                )
                if missing_file:
                    messages.error(
                        request,
                        f"Временный файл {missing_file} не найден. Загрузите файлы заново.",
                    )
                    clear_bulk_order(request)
                    return redirect("bulk_order_create")
                with transaction.atomic():
                    for row in forms:
                        item = row["item"]
                        form = row["form"]
                        file_path = temp_file_path(request, "bulk_order", item["stored_name"])
                        with file_path.open("rb") as production_file:
                            manual_photo = form.cleaned_data.get("photo")
                            photo_path = (
                                temp_file_path(request, "bulk_order", item["photo"]["stored_name"])
                                if item.get("photo")
                                else None
                            )
                            order = Order(
                                user=request.user,
                                title=form.cleaned_data["title"],
                                production_type=item["production_type"],
                                material=form.cleaned_data["material"],
                                quantity=form.cleaned_data["quantity"],
                                **(item.get("model_dimensions") or {}),
                                status=OrderStatus.PENDING,
                                comment="Создано через массовую загрузку.",
                            )
                            if manual_photo:
                                clear_model_dimensions(order)
                            order.production_file.save(
                                item["original_name"],
                                File(production_file),
                                save=False,
                            )
                            if manual_photo:
                                manual_photo.seek(0)
                                order.photo.save(manual_photo.name, manual_photo, save=False)
                            elif photo_path:
                                with photo_path.open("rb") as photo_file:
                                    order.photo.save(
                                        item["photo"]["original_name"],
                                        File(photo_file),
                                        save=False,
                                    )
                            order.save()
                            created += 1
                clear_bulk_order(request)
                messages.success(request, f"Создано заказов: {created}.")
                return redirect("admin_order_queue")
            if not tokens_match:
                messages.error(request, "Данные массовой загрузки устарели. Загрузите файлы заново.")

        return render(
            request,
            "production/bulk_order_edit.html",
            {
                "rows": forms,
                "production_types": dict(ProductionType.choices),
                "bulk_material_groups": [
                    {
                        "production_type": ProductionType.PRINT_3D,
                        "label": ProductionType.PRINT_3D.label,
                        "materials": Material.objects.filter(
                            is_active=True,
                            production_type__in=[MaterialProductionType.PRINT_3D, MaterialProductionType.BOTH],
                        ).order_by("name"),
                    },
                    {
                        "production_type": ProductionType.LASER_CUT,
                        "label": ProductionType.LASER_CUT.label,
                        "materials": Material.objects.filter(
                            is_active=True,
                            production_type__in=[MaterialProductionType.LASER_CUT, MaterialProductionType.BOTH],
                        ).order_by("name"),
                    },
                ],
            },
        )

    form = BulkOrderCreateForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        clear_bulk_order(request)
        items = []
        for upload in form.cleaned_data["files"]:
            production_type = production_type_for_filename(upload.name)
            temp_info = save_temp_upload(request, "bulk_order", upload)
            items.append(
                {
                    "token": uuid.uuid4().hex,
                    "stored_name": temp_info["stored_name"],
                    "original_name": temp_info["original_name"],
                    "production_type": production_type,
                    "production_type_label": dict(ProductionType.choices)[production_type],
                    "title": default_bulk_title(upload.name),
                    "quantity": 1,
                }
            )
        request.session[BULK_ORDER_SESSION_KEY] = {"items": items}
        return redirect(f"{reverse('bulk_order_create')}?step=edit")

    return render(request, "production/bulk_order_form.html", {"form": form})


@login_required
@user_passes_test(is_admin)
def material_list(request):
    return render(request, "production/material_list.html", {"groups": material_groups()})


@login_required
@user_passes_test(is_admin)
def material_create(request):
    form = MaterialForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        material = form.save()
        log_audit(request.user, "create_material", material)
        messages.success(request, "Материал создан.")
        return redirect("material_list")
    return render(request, "production/material_form.html", {"form": form, "title": "Новый материал"})


@login_required
@user_passes_test(is_admin)
def material_update(request, pk):
    material = get_object_or_404(Material, pk=pk)
    form = MaterialForm(request.POST or None, instance=material)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_audit(request.user, "update_material", material)
        messages.success(request, "Материал обновлен.")
        return redirect("material_list")
    return render(request, "production/material_form.html", {"form": form, "title": "Редактировать материал"})


@login_required
@user_passes_test(is_admin)
def material_delete(request, pk):
    material = get_object_or_404(Material, pk=pk)
    if request.method == "POST":
        material_name = material.name
        log_audit(request.user, "delete_material", material)
        material.delete()
        messages.success(request, f"Материал «{material_name}» удален.")
        return redirect("material_list")
    return render(request, "production/material_confirm_delete.html", {"material": material})


@login_required
@user_passes_test(is_admin)
def rejection_reason_list(request):
    reasons = RejectionReason.objects.order_by("text")
    return render(request, "production/rejection_reason_list.html", {"reasons": reasons})


@login_required
@user_passes_test(is_admin)
def rejection_reason_create(request):
    form = RejectionReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        reason = form.save()
        log_audit(request.user, "create_rejection_reason", reason)
        messages.success(request, "Причина отклонения создана.")
        return redirect("rejection_reason_list")
    return render(
        request,
        "production/rejection_reason_form.html",
        {"form": form, "title": "Новая причина отклонения"},
    )


@login_required
@user_passes_test(is_admin)
def rejection_reason_update(request, pk):
    reason = get_object_or_404(RejectionReason, pk=pk)
    form = RejectionReasonForm(request.POST or None, instance=reason)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_audit(request.user, "update_rejection_reason", reason)
        messages.success(request, "Причина отклонения обновлена.")
        return redirect("rejection_reason_list")
    return render(
        request,
        "production/rejection_reason_form.html",
        {"form": form, "title": "Редактировать причину отклонения"},
    )


@login_required
@user_passes_test(is_admin)
def rejection_reason_delete(request, pk):
    reason = get_object_or_404(RejectionReason, pk=pk)
    if request.method == "POST":
        reason_text = reason.text
        log_audit(request.user, "delete_rejection_reason", reason)
        reason.delete()
        messages.success(request, f"Причина «{reason_text}» удалена.")
        return redirect("rejection_reason_list")
    return render(
        request,
        "production/rejection_reason_confirm_delete.html",
        {"reason": reason},
    )


@login_required
@user_passes_test(is_admin)
def info_panel(request):
    production_settings = ProductionSettings.get_solo()
    settings_form = ProductionSettingsForm(request.POST or None, instance=production_settings)
    if request.method == "POST" and request.POST.get("save_production_settings") and settings_form.is_valid():
        settings_form.save()
        log_audit(request.user, "update_production_settings", production_settings)
        messages.success(request, "Настройки производства сохранены.")
        return redirect("info_panel")
    return render(
        request,
        "production/info_panel.html",
        {
            "announcements": PinnedAnnouncement.objects.order_by("-is_active", "-updated_at", "-id"),
            "countdowns": Countdown.objects.order_by("target_date", "title", "id"),
            "settings_form": settings_form,
        },
    )


@login_required
@user_passes_test(is_admin)
def announcement_list(request):
    return redirect("info_panel")


@login_required
@user_passes_test(is_admin)
def announcement_create(request):
    form = PinnedAnnouncementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        announcement = form.save()
        if announcement.is_active:
            PinnedAnnouncement.objects.exclude(pk=announcement.pk).update(is_active=False)
        log_audit(request.user, "create_announcement", announcement)
        messages.success(request, "Объявление создано.")
        return redirect("info_panel")
    return render(
        request,
        "production/announcement_form.html",
        {"form": form, "title": "Новое объявление"},
    )


@login_required
@user_passes_test(is_admin)
def announcement_update(request, pk):
    announcement = get_object_or_404(PinnedAnnouncement, pk=pk)
    form = PinnedAnnouncementForm(request.POST or None, instance=announcement)
    if request.method == "POST" and form.is_valid():
        announcement = form.save()
        if announcement.is_active:
            PinnedAnnouncement.objects.exclude(pk=announcement.pk).update(is_active=False)
        log_audit(request.user, "update_announcement", announcement)
        messages.success(request, "Объявление обновлено.")
        return redirect("info_panel")
    return render(
        request,
        "production/announcement_form.html",
        {"form": form, "title": "Редактировать объявление"},
    )


@login_required
@user_passes_test(is_admin)
def announcement_delete(request, pk):
    announcement = get_object_or_404(PinnedAnnouncement, pk=pk)
    if request.method == "POST":
        title = announcement.title
        log_audit(request.user, "delete_announcement", announcement)
        announcement.delete()
        messages.success(request, f"Объявление «{title}» удалено.")
        return redirect("info_panel")
    return render(
        request,
        "production/announcement_confirm_delete.html",
        {"announcement": announcement},
    )


@login_required
@user_passes_test(is_admin)
def countdown_list(request):
    return redirect("info_panel")


@login_required
@user_passes_test(is_admin)
def countdown_create(request):
    form = CountdownForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        countdown = form.save()
        log_audit(request.user, "create_countdown", countdown)
        messages.success(request, "Отсчет создан.")
        return redirect("info_panel")
    return render(
        request,
        "production/countdown_form.html",
        {"form": form, "title": "Новый отсчет"},
    )


@login_required
@user_passes_test(is_admin)
def countdown_update(request, pk):
    countdown = get_object_or_404(Countdown, pk=pk)
    form = CountdownForm(request.POST or None, instance=countdown)
    if request.method == "POST" and form.is_valid():
        countdown = form.save()
        log_audit(request.user, "update_countdown", countdown)
        messages.success(request, "Отсчет обновлен.")
        return redirect("info_panel")
    return render(
        request,
        "production/countdown_form.html",
        {"form": form, "title": "Редактировать отсчет"},
    )


@login_required
@user_passes_test(is_admin)
def countdown_delete(request, pk):
    countdown = get_object_or_404(Countdown, pk=pk)
    if request.method == "POST":
        title = countdown.title
        log_audit(request.user, "delete_countdown", countdown)
        countdown.delete()
        messages.success(request, f"Отсчет «{title}» удален.")
        return redirect("info_panel")
    return render(
        request,
        "production/countdown_confirm_delete.html",
        {"countdown": countdown},
    )


@login_required
@user_passes_test(is_admin)
def duty_slot_list(request):
    admin_duty_people_queryset()
    admin_slots = (
        DutySlot.objects.select_related("person", "person__user")
        .filter(person__user__is_staff=True, person__user__is_active=True)
        .order_by("unload_time", "person__last_name", "person__first_name", "id")
    )
    schedules = DutySchedule.objects.prefetch_related(Prefetch("slots", queryset=admin_slots)).order_by(
        "-is_active", "name", "id"
    )
    rules_by_weekday = {
        rule.weekday: rule
        for rule in DutyScheduleRule.objects.select_related("schedule").order_by("weekday", "id")
    }
    rules = [
        {
            "value": value,
            "label": label,
            "rule": rules_by_weekday.get(value),
        }
        for value, label in Weekday.choices
    ]
    skips = DutySkip.objects.select_related(
        "duty_slot",
        "duty_slot__schedule",
        "duty_slot__person",
        "replacement_slot",
        "replacement_person",
    ).filter(
        duty_slot__person__user__is_staff=True,
        duty_slot__person__user__is_active=True,
    )[:100]
    people = admin_duty_people_queryset()
    return render(
        request,
        "production/duty_slot_list.html",
        {"schedules": schedules, "rules": rules, "skips": skips, "people": people},
    )


@login_required
@user_passes_test(is_admin)
def duty_slot_create(request):
    form = DutySlotForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        slot = form.save()
        log_audit(request.user, "create_duty_slot", slot)
        messages.success(request, "Дежурство добавлено.")
        return redirect("duty_slot_list")
    return render(
        request,
        "production/duty_slot_form.html",
        {"form": form, "title": "Новое дежурство"},
    )


@login_required
@user_passes_test(is_admin)
def duty_slot_update(request, pk):
    slot = get_object_or_404(DutySlot, pk=pk)
    form = DutySlotForm(request.POST or None, instance=slot)
    if request.method == "POST" and form.is_valid():
        slot = form.save()
        log_audit(request.user, "update_duty_slot", slot)
        messages.success(request, "Дежурство обновлено.")
        return redirect("duty_slot_list")
    return render(
        request,
        "production/duty_slot_form.html",
        {"form": form, "title": "Редактировать дежурство"},
    )


@login_required
@user_passes_test(is_admin)
def duty_slot_delete(request, pk):
    slot = get_object_or_404(DutySlot, pk=pk)
    if request.method == "POST":
        label = str(slot)
        log_audit(request.user, "delete_duty_slot", slot)
        slot.delete()
        messages.success(request, f"Дежурство «{label}» удалено.")
        return redirect("duty_slot_list")
    return render(
        request,
        "production/duty_slot_confirm_delete.html",
        {"slot": slot},
    )


@login_required
@user_passes_test(is_admin)
def duty_schedule_create(request):
    form = DutyScheduleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        schedule = form.save()
        log_audit(request.user, "create_duty_schedule", schedule)
        messages.success(request, "График создан.")
        return redirect("duty_slot_list")
    return render(
        request,
        "production/duty_schedule_form.html",
        {"form": form, "title": "Новый график"},
    )


@login_required
@user_passes_test(is_admin)
def duty_schedule_update(request, pk):
    schedule = get_object_or_404(DutySchedule, pk=pk)
    form = DutyScheduleForm(request.POST or None, instance=schedule)
    if request.method == "POST" and form.is_valid():
        schedule = form.save()
        log_audit(request.user, "update_duty_schedule", schedule)
        messages.success(request, "График обновлен.")
        return redirect("duty_slot_list")
    return render(
        request,
        "production/duty_schedule_form.html",
        {"form": form, "title": "Редактировать график"},
    )


@login_required
@user_passes_test(is_admin)
def duty_schedule_delete(request, pk):
    schedule = get_object_or_404(DutySchedule, pk=pk)
    if request.method == "POST":
        label = str(schedule)
        log_audit(request.user, "delete_duty_schedule", schedule)
        try:
            schedule.delete()
        except ProtectedError:
            messages.error(request, "Нельзя удалить график, который используется в правиле дня недели.")
            return redirect("duty_slot_list")
        messages.success(request, f"График «{label}» удален.")
        return redirect("duty_slot_list")
    return render(
        request,
        "production/duty_schedule_confirm_delete.html",
        {"schedule": schedule},
    )


def duty_weekday_or_404(weekday):
    try:
        weekday = int(weekday)
    except (TypeError, ValueError):
        raise Http404
    if weekday not in {value for value, _ in Weekday.choices}:
        raise Http404
    return weekday


@login_required
@user_passes_test(is_admin)
def duty_slot_day_update(request, weekday):
    weekday = duty_weekday_or_404(weekday)
    rule = DutyScheduleRule.objects.filter(weekday=weekday, is_active=True).select_related("schedule").first()
    if rule:
        return redirect("duty_schedule_edit", pk=rule.schedule_id)
    messages.error(request, "Для этого дня недели еще не задано правило графика.")
    return redirect("duty_slot_list")


@login_required
@user_passes_test(is_admin)
def duty_schedule_edit(request, pk):
    schedule = get_object_or_404(DutySchedule, pk=pk)
    queryset = (
        DutySlot.objects.select_related("schedule", "person")
        .filter(schedule=schedule)
        .order_by("unload_time", "person__last_name", "person__first_name", "last_name", "first_name", "id")
    )
    formset = DutySlotScheduleFormSet(
        request.POST or None,
        queryset=queryset,
        prefix="slots",
        form_kwargs={"schedule": schedule},
    )
    if request.method == "POST" and formset.is_valid():
        changed_slots = formset.save(commit=False)
        deleted_count = 0
        for slot in formset.deleted_objects:
            log_audit(request.user, "delete_duty_slot", slot)
            slot.delete()
            deleted_count += 1
        saved_count = 0
        for slot in changed_slots:
            is_new = slot.pk is None
            slot.schedule = schedule
            slot.save()
            log_audit(request.user, "create_duty_slot" if is_new else "update_duty_slot", slot)
            saved_count += 1
        message_parts = []
        if saved_count:
            message_parts.append(f"сохранено записей: {saved_count}")
        if deleted_count:
            message_parts.append(f"удалено записей: {deleted_count}")
        messages.success(request, f"{schedule.name}: " + ", ".join(message_parts or ["изменений нет"]) + ".")
        return redirect("duty_slot_list")
    return render(
        request,
        "production/duty_schedule_edit.html",
        {"formset": formset, "schedule": schedule},
    )


@login_required
@user_passes_test(is_admin)
def duty_schedule_rule_create(request):
    form = DutyScheduleRuleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        rule = form.save()
        log_audit(request.user, "create_duty_schedule_rule", rule)
        messages.success(request, "Правило графика создано.")
        return redirect("duty_slot_list")
    return render(
        request,
        "production/duty_schedule_rule_form.html",
        {"form": form, "title": "Новое правило графика"},
    )


@login_required
@user_passes_test(is_admin)
def duty_schedule_rule_update(request, pk):
    rule = get_object_or_404(DutyScheduleRule, pk=pk)
    form = DutyScheduleRuleForm(request.POST or None, instance=rule)
    if request.method == "POST" and form.is_valid():
        rule = form.save()
        log_audit(request.user, "update_duty_schedule_rule", rule)
        messages.success(request, "Правило графика обновлено.")
        return redirect("duty_slot_list")
    return render(
        request,
        "production/duty_schedule_rule_form.html",
        {"form": form, "title": "Редактировать правило графика"},
    )


@login_required
@user_passes_test(is_admin)
def duty_schedule_rule_delete(request, pk):
    rule = get_object_or_404(DutyScheduleRule, pk=pk)
    if request.method == "POST":
        label = str(rule)
        log_audit(request.user, "delete_duty_schedule_rule", rule)
        rule.delete()
        messages.success(request, f"Правило «{label}» удалено.")
        return redirect("duty_slot_list")
    return render(
        request,
        "production/duty_schedule_rule_confirm_delete.html",
        {"rule": rule},
    )


@login_required
@user_passes_test(is_admin)
def duty_person_create(request):
    admin_duty_people_queryset()
    messages.info(request, "Дежурные формируются автоматически из списка администраторов.")
    return redirect("duty_slot_list")


@login_required
@user_passes_test(is_admin)
def duty_person_update(request, pk):
    get_object_or_404(DutyPerson, pk=pk)
    admin_duty_people_queryset()
    messages.info(request, "Изменяйте ФИ и права администратора в разделе пользователей.")
    return redirect("duty_slot_list")


@login_required
@user_passes_test(is_admin)
def duty_person_delete(request, pk):
    get_object_or_404(DutyPerson, pk=pk)
    admin_duty_people_queryset()
    messages.info(request, "Дежурные больше не удаляются отдельно: снимите права администратора у аккаунта.")
    return redirect("duty_slot_list")


@login_required
@user_passes_test(is_admin)
def duty_skip_create(request):
    form = DutySkipForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        skip = form.save()
        log_audit(request.user, "create_duty_skip", skip)
        messages.success(request, "Пропуск дежурства добавлен.")
        return redirect("duty_slot_list")
    return render(
        request,
        "production/duty_skip_form.html",
        {"form": form, "title": "Пропуск дежурства"},
    )


@login_required
@user_passes_test(is_admin)
def duty_skip_update(request, pk):
    skip = get_object_or_404(DutySkip, pk=pk)
    form = DutySkipForm(request.POST or None, instance=skip)
    if request.method == "POST" and form.is_valid():
        skip = form.save()
        log_audit(request.user, "update_duty_skip", skip)
        messages.success(request, "Пропуск дежурства обновлен.")
        return redirect("duty_slot_list")
    return render(
        request,
        "production/duty_skip_form.html",
        {"form": form, "title": "Редактировать пропуск дежурства"},
    )


@login_required
@user_passes_test(is_admin)
def duty_skip_delete(request, pk):
    skip = get_object_or_404(DutySkip, pk=pk)
    if request.method == "POST":
        label = str(skip)
        log_audit(request.user, "delete_duty_skip", skip)
        skip.delete()
        messages.success(request, f"Пропуск дежурства «{label}» удален.")
        return redirect("duty_slot_list")
    return render(
        request,
        "production/duty_skip_confirm_delete.html",
        {"skip": skip},
    )


@login_required
@user_passes_test(is_admin)
def prefix_rule_list(request):
    rules = OrderPrefixRule.objects.order_by("-starts_on", "-id")
    return render(
        request,
        "production/prefix_rule_list.html",
        {"rules": rules},
    )


@login_required
@user_passes_test(is_admin)
def prefix_rule_create(request):
    form = OrderPrefixRuleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        rule = form.save()
        log_audit(request.user, "create_prefix_rule", rule)
        messages.success(request, "Правило префикса создано.")
        return redirect("prefix_rule_list")
    return render(
        request,
        "production/prefix_rule_form.html",
        {"form": form, "title": "Новое правило префикса"},
    )


@login_required
@user_passes_test(is_admin)
def prefix_rule_update(request, pk):
    rule = get_object_or_404(OrderPrefixRule, pk=pk)
    form = OrderPrefixRuleForm(request.POST or None, instance=rule)
    if request.method == "POST" and form.is_valid():
        rule = form.save()
        log_audit(request.user, "update_prefix_rule", rule)
        messages.success(request, "Правило префикса обновлено.")
        return redirect("prefix_rule_list")
    return render(
        request,
        "production/prefix_rule_form.html",
        {"form": form, "title": "Редактировать правило префикса"},
    )


@login_required
@user_passes_test(is_admin)
def prefix_rule_delete(request, pk):
    rule = get_object_or_404(OrderPrefixRule, pk=pk)
    if request.method == "POST":
        label = str(rule)
        log_audit(request.user, "delete_prefix_rule", rule)
        rule.delete()
        messages.success(request, f"Правило префикса «{label}» удалено.")
        return redirect("prefix_rule_list")
    return render(
        request,
        "production/prefix_rule_confirm_delete.html",
        {"rule": rule},
    )


@login_required
@user_passes_test(is_admin)
def prefix_rule_delete_orders(request, pk):
    rule = get_object_or_404(OrderPrefixRule, pk=pk)
    orders = Order.objects.filter(prefix_text=rule.prefix)
    order_count = orders.count()
    if request.method == "POST":
        deleted_count, _ = orders.delete()
        log_audit(request.user, "delete_orders_by_prefix", rule, f"Префикс: {rule.prefix}; удалено заказов: {deleted_count}")
        messages.success(request, f"Удалено заказов с префиксом «{rule.prefix}»: {deleted_count}.")
        return redirect("prefix_rule_list")
    return render(
        request,
        "production/prefix_rule_delete_orders_confirm.html",
        {"rule": rule, "order_count": order_count},
    )


@login_required
@user_passes_test(is_admin)
def user_stats(request, pk):
    admin_duty_people_queryset()
    account = get_object_or_404(User, pk=pk)
    period = parse_user_stats_period(request.GET)
    for error in period["errors"]:
        messages.error(request, error)

    user_orders = apply_period(
        Order.objects.select_related("material").filter(user=account),
        "created_at",
        period,
    )
    user_order_count = user_orders.count()
    status_counts = {
        status: user_orders.filter(status=status).count()
        for status, _label in OrderStatus.choices
    }
    customer_metrics = [
        {"label": "Создано заказов", "value": user_order_count},
        {
            "label": "Активные",
            "value": status_counts[OrderStatus.PENDING] + status_counts[OrderStatus.IN_PROGRESS],
        },
        {"label": "Готовы к выдаче", "value": status_counts[OrderStatus.READY]},
        {"label": "Выполнены", "value": status_counts[OrderStatus.DONE]},
        {"label": "Отменены", "value": status_counts[OrderStatus.CANCELLED]},
        {"label": "Отклонены", "value": status_counts[OrderStatus.REJECTED]},
    ]

    actor_audit_events = apply_period(AuditLog.objects.filter(actor=account), "created_at", period)
    actor_status_logs = apply_period(OrderStatusLog.objects.filter(changed_by=account), "created_at", period)

    pickup_metrics = [
        {"label": "Самосъемы", "value": actor_audit_events.filter(action="self_pickup_order").count()},
        {
            "label": "Подтверждения получения",
            "value": actor_status_logs.filter(
                new_status=OrderStatus.DONE,
                reason__icontains="Получение подтверждено",
            ).count(),
        },
        {"label": "Повторы заказов", "value": actor_audit_events.filter(action="repeat_order").count()},
    ]

    production_type_labels = dict(ProductionType.choices)
    production_type_stats = [
        {
            "label": production_type_labels.get(row["production_type"], row["production_type"]),
            "count": row["count"],
        }
        for row in user_orders.values("production_type")
        .annotate(count=Count("id"))
        .order_by("-count", "production_type")
    ]
    material_stats = list(
        user_orders.filter(material__isnull=False)
        .values("material__name", "material__color")
        .annotate(count=Count("id"))
        .order_by("-count", "material__name")[:10]
    )

    average_completion = format_duration(average_order_completion_seconds(user_orders))
    recent_status_logs = (
        apply_period(
            OrderStatusLog.objects.filter(order__user=account).select_related("changed_by"),
            "created_at",
            period,
        )
        .order_by("-created_at", "-id")[:10]
    )

    admin_metrics = None
    admin_action_stats = []
    if account.is_staff:
        handled_status_logs = actor_status_logs.filter(
            new_status__in=[
                OrderStatus.IN_PROGRESS,
                OrderStatus.READY,
                OrderStatus.REJECTED,
                OrderStatus.CANCELLED,
                OrderStatus.DONE,
            ]
        )
        admin_metrics = [
            {
                "label": "Взял в работу",
                "value": actor_status_logs.filter(new_status=OrderStatus.IN_PROGRESS).count(),
            },
            {
                "label": "Подготовил к выдаче",
                "value": actor_status_logs.filter(new_status=OrderStatus.READY).count(),
            },
            {
                "label": "Отклонил",
                "value": actor_status_logs.filter(new_status=OrderStatus.REJECTED).count(),
            },
            {
                "label": "Отменил/закрыл",
                "value": actor_status_logs.filter(new_status__in=[OrderStatus.CANCELLED, OrderStatus.DONE]).count(),
            },
            {
                "label": "Уникальных заказов обработал",
                "value": handled_status_logs.values("order_number").distinct().count(),
            },
            {
                "label": "Одиночные правки заказов",
                "value": actor_audit_events.filter(action="update_order").count(),
            },
            {
                "label": "Массовые смены статусов",
                "value": actor_audit_events.filter(action="bulk_status_update").count(),
            },
            {"label": "CSV-экспорты", "value": actor_audit_events.filter(action="export_orders").count()},
            {
                "label": "Управленческие действия",
                "value": actor_audit_events.filter(action__in=ADMIN_MANAGEMENT_ACTIONS).count(),
            },
        ]
        admin_action_stats = list(
            actor_audit_events.values("action")
            .annotate(count=Count("id"))
            .order_by("-count", "action")[:12]
        )

    duty_person = DutyPerson.objects.filter(user=account).first()
    duty_metrics = None
    duty_skip_rows = []
    if duty_person:
        skipped_qs = apply_date_period(
            DutySkip.objects.select_related("duty_slot", "replacement_slot", "replacement_person")
            .filter(duty_slot__person=duty_person),
            "unavailable_date",
            period,
        )
        replacement_qs = apply_date_period(
            DutySkip.objects.select_related("duty_slot", "replacement_slot", "replacement_person")
            .filter(Q(replacement_person=duty_person) | Q(replacement_slot__person=duty_person)),
            "unavailable_date",
            period,
        )
        duty_metrics = [
            {"label": "Пропустил дежурств", "value": skipped_qs.count()},
            {"label": "Заменял других", "value": replacement_qs.count()},
        ]
        duty_skip_rows = list(skipped_qs.order_by("-unavailable_date", "-id")[:8])

    return render(
        request,
        "production/user_stats.html",
        {
            "account": account,
            "period": period,
            "customer_metrics": customer_metrics,
            "pickup_metrics": pickup_metrics,
            "average_completion": average_completion,
            "production_type_stats": production_type_stats,
            "material_stats": material_stats,
            "admin_metrics": admin_metrics,
            "admin_action_stats": admin_action_stats,
            "duty_person": duty_person,
            "duty_metrics": duty_metrics,
            "duty_skip_rows": duty_skip_rows,
            "recent_status_logs": recent_status_logs,
        },
    )


@login_required
@user_passes_test(is_admin)
def user_list(request):
    users = User.objects.order_by("-date_joined", "-id")
    semi_printer_ids = set(
        User.objects.filter(groups__name=SEMI_PRINTER_GROUP).values_list("id", flat=True)
    )
    return render(
        request,
        "production/user_list.html",
        {"users": users, "first_user_id": first_user_id(), "semi_printer_ids": semi_printer_ids},
    )


@login_required
@user_passes_test(is_admin)
def user_admin_toggle(request, pk):
    if request.method != "POST":
        return redirect("user_list")

    target_user = get_object_or_404(User, pk=pk)
    make_admin = request.POST.get("is_admin") == "1"

    if target_user.pk == first_user_id() and not make_admin:
        messages.error(request, "Нельзя снять права администратора у первого пользователя.")
        return redirect("user_list")

    if target_user == request.user and not make_admin:
        messages.error(request, "Нельзя снять права администратора у своей учетной записи.")
        return redirect("user_list")

    target_user.is_staff = make_admin
    target_user.is_superuser = make_admin
    target_user.save(update_fields=["is_staff", "is_superuser"])
    if make_admin:
        semi_printer_group = Group.objects.filter(name=SEMI_PRINTER_GROUP).first()
        if semi_printer_group:
            target_user.groups.remove(semi_printer_group)
    admin_duty_people_queryset()
    log_audit(request.user, "toggle_admin", target_user, f"is_admin={make_admin}")

    if make_admin:
        messages.success(request, f"{target_user.username} назначен администратором.")
    else:
        messages.success(request, f"Права администратора сняты у {target_user.username}.")
    return redirect("user_list")


@login_required
@user_passes_test(is_admin)
def user_semi_printer_toggle(request, pk):
    if request.method != "POST":
        return redirect("user_list")

    target_user = get_object_or_404(User, pk=pk)
    make_semi_printer = request.POST.get("is_semi_printer") == "1"

    if target_user.pk == first_user_id():
        messages.error(request, "Первому пользователю нельзя назначить роль полупечатника.")
        return redirect("user_list")

    if target_user.is_staff:
        messages.error(request, "Администратору нельзя назначить роль полупечатника.")
        return redirect("user_list")

    group, _ = Group.objects.get_or_create(name=SEMI_PRINTER_GROUP)
    if make_semi_printer:
        target_user.groups.add(group)
        messages.success(request, f"{target_user.username} назначен полупечатником.")
    else:
        target_user.groups.remove(group)
        messages.success(request, f"Роль полупечатника снята у {target_user.username}.")
    log_audit(request.user, "toggle_semi_printer", target_user, f"is_semi_printer={make_semi_printer}")
    return redirect("user_list")


@login_required
@user_passes_test(is_admin)
def user_delete(request, pk):
    if request.method != "POST":
        return redirect("user_list")

    target_user = get_object_or_404(User, pk=pk)

    if target_user.pk == first_user_id():
        messages.error(request, "Нельзя удалить первого пользователя.")
        return redirect("user_list")

    if target_user == request.user:
        messages.error(request, "Нельзя удалить свою учетную запись.")
        return redirect("user_list")

    username = target_user.username
    log_audit(request.user, "delete_user", target_user)
    target_user.delete()
    admin_duty_people_queryset()
    messages.success(request, f"Пользователь {username} удален.")
    return redirect("user_list")


@login_required
@user_passes_test(is_admin)
def user_bulk_delete(request):
    if request.method != "POST":
        return redirect("user_list")

    selected_ids = request.POST.getlist("user_ids")
    protected_ids = {first_user_id(), request.user.pk}
    users = User.objects.filter(pk__in=selected_ids).exclude(pk__in=protected_ids)
    usernames = list(users.values_list("username", flat=True))
    user_count = users.count()
    users.delete()
    admin_duty_people_queryset()
    if user_count:
        log_audit(request.user, "bulk_delete_users", request.user, f"Удалены пользователи: {', '.join(usernames)}")
        messages.success(request, f"Удалено пользователей: {user_count}.")
    else:
        messages.error(request, "Не выбраны пользователи, которых можно удалить.")
    return redirect("user_list")


@login_required
@user_passes_test(is_admin)
def status_log_list(request):
    logs = OrderStatusLog.objects.select_related("order", "changed_by")[:200]
    return render(request, "production/status_log_list.html", {"logs": logs})
