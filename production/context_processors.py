from django.conf import settings
from django.utils import timezone

from .models import (
    Countdown,
    DutyScheduleRule,
    DutySkip,
    DutySlot,
    PinnedAnnouncement,
    admin_duty_people_queryset,
    user_is_semi_printer,
)


def pinned_announcement(request):
    pinned = (
        PinnedAnnouncement.objects.filter(is_active=True)
        .order_by("-updated_at", "-id")
        .first()
    )
    active_countdowns = list(Countdown.objects.filter(is_active=True).order_by("target_date", "title", "id"))
    context = {
        "project_version": settings.PROJECT_VERSION,
        "page_transitions_enabled": settings.ENABLE_PAGE_TRANSITIONS,
        "is_semi_printer_user": user_is_semi_printer(request.user) if getattr(request, "user", None) else False,
        "can_access_production_queue_user": (
            bool(getattr(request, "user", None))
            and request.user.is_authenticated
            and (request.user.is_staff or user_is_semi_printer(request.user))
        ),
        "pinned_announcement": pinned,
        "active_countdowns": active_countdowns,
        "has_active_countdowns": bool(active_countdowns),
        "has_left_sidebar": bool(pinned),
    }
    if getattr(request, "user", None) and request.user.is_authenticated and request.user.is_staff:
        admin_duty_people_queryset()
        today = timezone.localdate()
        rule = (
            DutyScheduleRule.objects.select_related("schedule")
            .filter(is_active=True, weekday=today.weekday(), schedule__is_active=True)
            .first()
        )
        today_slots = DutySlot.objects.select_related("schedule", "person", "person__user").filter(
            is_active=True,
            person__user__is_staff=True,
            person__user__is_active=True,
            schedule__is_active=True,
        )
        if rule:
            today_slots = today_slots.filter(schedule=rule.schedule)
        else:
            today_slots = today_slots.filter(weekday=today.weekday())
        today_slots = today_slots.order_by(
            "unload_time", "person__last_name", "person__first_name", "last_name", "first_name", "id"
        )
        skips = {
            skip.duty_slot_id: skip
            for skip in DutySkip.objects.select_related(
                "duty_slot",
                "duty_slot__person",
                "replacement_slot",
                "replacement_slot__person",
                "replacement_person",
            ).filter(
                unavailable_date=today,
                duty_slot__is_active=True,
            )
        }
        active_duty_slots = []
        for slot in today_slots:
            skip = skips.get(slot.pk)
            if skip:
                replacement_name = skip.replacement_full_name
                replacement_parts = replacement_name.split(maxsplit=1)
                if replacement_name:
                    active_duty_slots.append(
                        {
                            "last_name": replacement_parts[0] if replacement_parts else "",
                            "first_name": replacement_parts[1] if len(replacement_parts) > 1 else "",
                            "unload_time": skip.unavailable_time,
                            "replacement_for": slot.full_name,
                            "skip_reason": skip.reason,
                        }
                    )
            else:
                display_name = f"{slot.display_last_name} {slot.display_first_name}".strip()
                if display_name:
                    active_duty_slots.append(
                        {
                            "last_name": slot.display_last_name,
                            "first_name": slot.display_first_name,
                            "unload_time": slot.unload_time,
                        }
                    )
        context["active_duty_slots"] = active_duty_slots
        context["has_left_sidebar"] = bool(pinned or active_duty_slots)
    else:
        context["active_duty_slots"] = []
    return context
