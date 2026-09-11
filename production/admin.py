from django.contrib import admin

from .models import (
    AuditLog,
    Countdown,
    DutySkip,
    DutyPerson,
    DutySchedule,
    DutyScheduleRule,
    DutySlot,
    Material,
    Order,
    OrderDraft,
    OrderMessage,
    OrderPrefixRule,
    OrderStatusLog,
    PinnedAnnouncement,
    ProductionSettings,
    RejectionReason,
    admin_duty_people_queryset,
)


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("name", "color", "production_type", "is_active", "created_at")
    list_filter = ("production_type", "color", "is_active")
    search_fields = ("name",)


@admin.register(RejectionReason)
class RejectionReasonAdmin(admin.ModelAdmin):
    list_display = ("text", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("text",)


@admin.register(PinnedAnnouncement)
class PinnedAnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "is_active", "created_at", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("title", "body")


@admin.register(Countdown)
class CountdownAdmin(admin.ModelAdmin):
    list_display = ("title", "target_date", "is_active", "updated_at")
    list_filter = ("is_active", "target_date")
    search_fields = ("title",)


@admin.register(ProductionSettings)
class ProductionSettingsAdmin(admin.ModelAdmin):
    list_display = ("allow_self_pickup", "updated_at")


@admin.register(DutyPerson)
class DutyPersonAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "user", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("last_name", "first_name", "user__username", "user__last_name", "user__first_name")
    readonly_fields = ("user", "last_name", "first_name", "is_active", "created_at", "updated_at")

    def changelist_view(self, request, extra_context=None):
        admin_duty_people_queryset()
        return super().changelist_view(request, extra_context)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DutySlot)
class DutySlotAdmin(admin.ModelAdmin):
    list_display = ("schedule", "weekday", "person", "last_name", "first_name", "unload_time", "is_active")
    list_filter = ("schedule", "weekday", "person", "is_active", "unload_time")
    search_fields = ("person__last_name", "person__first_name", "last_name", "first_name")


@admin.register(DutySchedule)
class DutyScheduleAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(DutyScheduleRule)
class DutyScheduleRuleAdmin(admin.ModelAdmin):
    list_display = ("weekday", "schedule", "is_active", "updated_at")
    list_filter = ("weekday", "schedule", "is_active")
    search_fields = ("schedule__name",)


@admin.register(DutySkip)
class DutySkipAdmin(admin.ModelAdmin):
    list_display = ("unavailable_date", "unavailable_time", "duty_slot", "replacement_person", "replacement_slot", "created_at")
    list_filter = ("unavailable_date", "duty_slot", "replacement_person", "replacement_slot")
    search_fields = (
        "duty_slot__person__last_name",
        "duty_slot__last_name",
        "replacement_person__last_name",
        "replacement_slot__last_name",
        "reason",
    )


@admin.register(OrderPrefixRule)
class OrderPrefixRuleAdmin(admin.ModelAdmin):
    list_display = ("prefix", "starts_on", "ends_on", "next_number", "is_active")
    list_filter = ("is_active", "starts_on", "ends_on")
    search_fields = ("prefix",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "user",
        "production_type",
        "material",
        "status",
        "prefix_text",
        "prefix_number",
        "is_priority",
        "priority_remaining_quantity",
        "pickup_cell",
        "model_width",
        "model_depth",
        "model_height",
        "quantity",
        "created_at",
    )
    list_filter = ("production_type", "status", "material", "prefix_rule", "is_priority", "pickup_cell")
    search_fields = ("title", "user__username")
    readonly_fields = ("created_at", "updated_at")


@admin.register(OrderStatusLog)
class OrderStatusLogAdmin(admin.ModelAdmin):
    list_display = ("order_number", "order_title", "changed_by", "old_status", "new_status", "created_at")
    list_filter = ("old_status", "new_status", "changed_by")
    search_fields = ("order_title", "changed_by__username")
    readonly_fields = ("order", "order_number", "order_title", "changed_by", "old_status", "new_status", "reason", "created_at")


@admin.register(OrderMessage)
class OrderMessageAdmin(admin.ModelAdmin):
    list_display = ("order", "author", "created_at")
    list_filter = ("created_at", "author")
    search_fields = ("order__title", "author__username", "text")
    readonly_fields = ("order", "author", "text", "photo", "created_at")


@admin.register(OrderDraft)
class OrderDraftAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "title", "production_type", "current_step", "updated_at")
    list_filter = ("production_type", "current_step")
    search_fields = ("title", "user__username")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "target_type", "target_label")
    list_filter = ("action", "target_type", "actor")
    search_fields = ("target_label", "changes", "actor__username")
    readonly_fields = ("actor", "action", "target_type", "target_id", "target_label", "changes", "created_at")

# Register your models here.
