import shutil
import tempfile
from datetime import time, timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import BulkOrderItemForm, OrderCreateForm, OrderDetailsStepForm
from .models import (
    AuditLog,
    Countdown,
    DutySkip,
    DutyPerson,
    DutySchedule,
    DutyScheduleRule,
    DutySlot,
    MAX_UPLOAD_SIZE,
    Material,
    Order,
    OrderDraft,
    OrderMessage,
    OrderPrefixRule,
    OrderStatus,
    OrderStatusLog,
    PinnedAnnouncement,
    ProductionSettings,
    ProductionType,
    RejectionReason,
    SEMI_PRINTER_GROUP,
    Weekday,
    admin_duty_people_queryset,
)

TEST_MEDIA_ROOT = tempfile.mkdtemp()


GIF_BYTES = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00"
    b"\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D"
    b"\x01\x00;"
)


def upload(name, content=b"solid test"):
    return SimpleUploadedFile(name, content, content_type="application/octet-stream")


def upload_with_size(name, size):
    uploaded = upload(name, b"x")
    uploaded.size = size
    return uploaded


def image_upload():
    return SimpleUploadedFile("preview.gif", GIF_BYTES, content_type="image/gif")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ProductionTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.print_material, _ = Material.objects.get_or_create(
            name="PLA",
            defaults={"production_type": ProductionType.PRINT_3D},
        )
        self.laser_material, _ = Material.objects.get_or_create(
            name="Test Acrylic",
            defaults={"production_type": ProductionType.LASER_CUT},
        )

    def test_first_registered_user_becomes_admin(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "admin",
                "first_name": "Admin",
                "last_name": "Owner",
                "email": "admin@example.com",
                "password1": "StrongPass12345!",
                "password2": "StrongPass12345!",
            },
        )
        self.assertRedirects(response, reverse("orders"))
        user = User.objects.get(username="admin")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.first_name, "Admin")
        self.assertEqual(user.last_name, "Owner")

        self.client.post(reverse("logout"))
        self.client.post(
            reverse("register"),
            {
                "username": "user",
                "first_name": "Regular",
                "last_name": "User",
                "email": "user@example.com",
                "password1": "StrongPass12345!",
                "password2": "StrongPass12345!",
            },
        )
        second_user = User.objects.get(username="user")
        self.assertFalse(second_user.is_staff)

    def test_registration_requires_first_and_last_name(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "emptyname",
                "email": "emptyname@example.com",
                "password1": "StrongPass12345!",
                "password2": "StrongPass12345!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="emptyname").exists())

    def test_admin_can_promote_user(self):
        admin = User.objects.create_user(
            "admin",
            password="StrongPass12345!",
            first_name="Admin",
            last_name="Owner",
            is_staff=True,
            is_superuser=True,
        )
        user = User.objects.create_user(
            "operator",
            password="StrongPass12345!",
            first_name="Operator",
            last_name="Lab",
        )
        self.client.force_login(admin)

        list_response = self.client.get(reverse("user_list"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "operator")

        response = self.client.post(
            reverse("user_admin_toggle", args=[user.pk]),
            {"is_admin": "1"},
        )
        self.assertRedirects(response, reverse("user_list"))
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_admin_can_toggle_semi_printer_role(self):
        admin = User.objects.create_user(
            "admin",
            password="StrongPass12345!",
            first_name="Admin",
            last_name="Owner",
            is_staff=True,
            is_superuser=True,
        )
        user = User.objects.create_user(
            "operator",
            password="StrongPass12345!",
            first_name="Operator",
            last_name="Lab",
        )
        staff_user = User.objects.create_user(
            "staff",
            password="StrongPass12345!",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("user_semi_printer_toggle", args=[user.pk]),
            {"is_semi_printer": "1"},
        )

        self.assertRedirects(response, reverse("user_list"))
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertTrue(user.groups.filter(name=SEMI_PRINTER_GROUP).exists())
        self.assertContains(self.client.get(reverse("user_list")), "Полупечатник")

        blocked_response = self.client.post(
            reverse("user_semi_printer_toggle", args=[staff_user.pk]),
            {"is_semi_printer": "1"},
        )
        self.assertRedirects(blocked_response, reverse("user_list"))
        self.assertFalse(staff_user.groups.filter(name=SEMI_PRINTER_GROUP).exists())

        promote_response = self.client.post(
            reverse("user_admin_toggle", args=[user.pk]),
            {"is_admin": "1"},
        )
        self.assertRedirects(promote_response, reverse("user_list"))
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertFalse(user.groups.filter(name=SEMI_PRINTER_GROUP).exists())
        self.assertEqual(AuditLog.objects.filter(action="toggle_semi_printer").count(), 1)

    def test_user_list_orders_newest_accounts_first(self):
        admin = User.objects.create_user(
            "admin",
            password="StrongPass12345!",
            first_name="Admin",
            last_name="Owner",
            is_staff=True,
            is_superuser=True,
        )
        old_user = User.objects.create_user(
            "old",
            password="StrongPass12345!",
            first_name="Old",
            last_name="User",
        )
        new_user = User.objects.create_user(
            "new",
            password="StrongPass12345!",
            first_name="New",
            last_name="User",
        )
        now = timezone.now()
        User.objects.filter(pk=admin.pk).update(date_joined=now - timedelta(days=3))
        User.objects.filter(pk=old_user.pk).update(date_joined=now - timedelta(days=2))
        User.objects.filter(pk=new_user.pk).update(date_joined=now - timedelta(days=1))
        self.client.force_login(admin)

        response = self.client.get(reverse("user_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [account.username for account in response.context["users"]],
            ["new", "old", "admin"],
        )

    def test_admin_can_manage_pinned_announcement(self):
        admin = User.objects.create_user(
            "admin",
            password="StrongPass12345!",
            first_name="Admin",
            last_name="Owner",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("announcement_create"),
            {"title": "Выдача заказов", "body": "Сегодня выдача до 17:00.", "is_active": "on"},
        )
        self.assertRedirects(response, reverse("info_panel"))
        first_announcement = PinnedAnnouncement.objects.get(title="Выдача заказов")
        self.assertTrue(first_announcement.is_active)

        page_response = self.client.get(reverse("orders"))
        self.assertContains(page_response, "Выдача заказов")
        self.assertContains(page_response, "Сегодня выдача до 17:00.")

        self.client.post(
            reverse("announcement_create"),
            {"title": "Новый график", "body": "Окно выдачи перенесено.", "is_active": "on"},
        )
        first_announcement.refresh_from_db()
        self.assertFalse(first_announcement.is_active)
        self.assertEqual(PinnedAnnouncement.objects.filter(is_active=True).count(), 1)

    def test_first_user_admin_rights_cannot_be_removed(self):
        first_admin = User.objects.create_user(
            "first",
            password="StrongPass12345!",
            first_name="First",
            last_name="Admin",
            is_staff=True,
            is_superuser=True,
        )
        second_admin = User.objects.create_user(
            "second",
            password="StrongPass12345!",
            first_name="Second",
            last_name="Admin",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(second_admin)

        list_response = self.client.get(reverse("user_list"))
        self.assertContains(list_response, "Первый администратор")

        response = self.client.post(
            reverse("user_admin_toggle", args=[first_admin.pk]),
            {"is_admin": "0"},
        )
        self.assertRedirects(response, reverse("user_list"))
        first_admin.refresh_from_db()
        self.assertTrue(first_admin.is_staff)
        self.assertTrue(first_admin.is_superuser)

    def test_admin_can_delete_user_but_not_first_or_self(self):
        first_admin = User.objects.create_user(
            "first",
            password="StrongPass12345!",
            first_name="First",
            last_name="Admin",
            is_staff=True,
            is_superuser=True,
        )
        second_admin = User.objects.create_user(
            "second",
            password="StrongPass12345!",
            first_name="Second",
            last_name="Admin",
            is_staff=True,
            is_superuser=True,
        )
        user = User.objects.create_user(
            "operator",
            password="StrongPass12345!",
            first_name="Operator",
            last_name="Lab",
        )
        self.client.force_login(second_admin)

        list_response = self.client.get(reverse("user_list"))
        self.assertContains(list_response, reverse("user_delete", args=[user.pk]))

        self_delete_response = self.client.post(reverse("user_delete", args=[second_admin.pk]))
        self.assertRedirects(self_delete_response, reverse("user_list"))
        self.assertTrue(User.objects.filter(pk=second_admin.pk).exists())

        first_delete_response = self.client.post(reverse("user_delete", args=[first_admin.pk]))
        self.assertRedirects(first_delete_response, reverse("user_list"))
        self.assertTrue(User.objects.filter(pk=first_admin.pk).exists())

        delete_response = self.client.post(reverse("user_delete", args=[user.pk]))
        self.assertRedirects(delete_response, reverse("user_list"))
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_admin_can_bulk_delete_users_but_not_first_or_self(self):
        first_admin = User.objects.create_user(
            "first",
            password="StrongPass12345!",
            is_staff=True,
            is_superuser=True,
        )
        current_admin = User.objects.create_user(
            "current",
            password="StrongPass12345!",
            first_name="Current",
            last_name="Admin",
            is_staff=True,
            is_superuser=True,
        )
        first_user = User.objects.create_user("first_user", password="StrongPass12345!")
        second_user = User.objects.create_user("second_user", password="StrongPass12345!")
        self.client.force_login(current_admin)

        response = self.client.post(
            reverse("user_bulk_delete"),
            {"user_ids": [first_admin.pk, current_admin.pk, first_user.pk, second_user.pk]},
        )

        self.assertRedirects(response, reverse("user_list"))
        self.assertTrue(User.objects.filter(pk=first_admin.pk).exists())
        self.assertTrue(User.objects.filter(pk=current_admin.pk).exists())
        self.assertFalse(User.objects.filter(pk=first_user.pk).exists())
        self.assertFalse(User.objects.filter(pk=second_user.pk).exists())
        self.assertEqual(AuditLog.objects.filter(action="bulk_delete_users").count(), 1)

    def test_order_form_accepts_only_matching_extensions(self):
        valid_form = OrderCreateForm(
            data={
                "title": "Корпус",
                "production_type": ProductionType.PRINT_3D,
                "material": self.print_material.pk,
                "quantity": 1,
                "comment": "",
            },
            files={"photo": image_upload(), "production_file": upload("model.stl")},
        )
        self.assertTrue(valid_form.is_valid(), valid_form.errors)

        invalid_form = OrderCreateForm(
            data={
                "title": "Корпус",
                "production_type": ProductionType.PRINT_3D,
                "material": self.print_material.pk,
                "quantity": 1,
                "comment": "",
            },
            files={"photo": image_upload(), "production_file": upload("model.dxf")},
        )
        self.assertFalse(invalid_form.is_valid())
        self.assertIn("production_file", invalid_form.errors)
        self.assertIn("model.dxf", str(invalid_form.errors["production_file"]))
        self.assertIn("50 МБ", str(invalid_form.errors["production_file"]))

        long_title_form = OrderCreateForm(
            data={
                "title": "x" * 31,
                "production_type": ProductionType.PRINT_3D,
                "material": self.print_material.pk,
                "quantity": 1,
                "comment": "",
            },
            files={"photo": image_upload(), "production_file": upload("model.stl")},
        )
        self.assertFalse(long_title_form.is_valid())
        self.assertIn("title", long_title_form.errors)

    def test_order_create_starts_with_type_choice_and_filters_materials(self):
        user = User.objects.create_user("maker", password="StrongPass12345!")
        self.client.force_login(user)

        type_response = self.client.get(reverse("order_create"))
        self.assertEqual(type_response.status_code, 200)
        self.assertContains(type_response, "3D-печать")
        self.assertContains(type_response, "Лазерная резка")

        form_response = self.client.get(
            reverse("order_create"),
            {"production_type": ProductionType.PRINT_3D},
        )
        self.assertRedirects(form_response, f"{reverse('order_create')}?step=title")

        title_response = self.client.post(
            f"{reverse('order_create')}?step=title",
            {"title": "Корпус"},
        )
        self.assertRedirects(title_response, f"{reverse('order_create')}?step=file")
        draft = OrderDraft.objects.get(user=user)
        self.assertEqual(draft.title, "Корпус")
        self.assertEqual(draft.current_step, "file")

        file_step = self.client.get(f"{reverse('order_create')}?step=file")
        self.assertContains(file_step, ".stl")
        self.assertContains(file_step, ".stp")
        self.assertNotContains(file_step, ".dxf")

        file_response = self.client.post(
            f"{reverse('order_create')}?step=file",
            {"production_file": upload("model.stl")},
        )
        self.assertRedirects(file_response, f"{reverse('order_create')}?step=image")

        source_response = self.client.get(reverse("order_wizard_source_file"))
        self.assertEqual(source_response.status_code, 200)
        self.assertEqual(source_response["Cache-Control"], "no-store")

        image_step = self.client.get(f"{reverse('order_create')}?step=image")
        self.assertContains(image_step, reverse("order_wizard_preview_save"))
        image_missing_response = self.client.post(f"{reverse('order_create')}?step=image", {})
        self.assertContains(image_missing_response, "Дождитесь автоматического изображения")

        stale_preview_response = self.client.post(
            reverse("order_wizard_preview_save"),
            {"source_name": "old-model.stl", "photo": image_upload()},
        )
        self.assertEqual(stale_preview_response.status_code, 409)

        preview_save_response = self.client.post(
            reverse("order_wizard_preview_save"),
            {
                "source_name": "model.stl",
                "photo": image_upload(),
                "model_width": "12.34",
                "model_depth": "56",
                "model_height": "7.89",
            },
        )
        self.assertEqual(preview_save_response.status_code, 200)
        draft.refresh_from_db()
        self.assertTrue(draft.photo)
        self.assertEqual(draft.model_width, 12.3)
        self.assertEqual(draft.model_depth, 56)
        self.assertEqual(draft.model_height, 7.9)

        image_response = self.client.post(
            f"{reverse('order_create')}?step=image",
            {},
        )
        self.assertRedirects(image_response, f"{reverse('order_create')}?step=details")

        back_to_image_response = self.client.get(f"{reverse('order_create')}?step=image")
        self.assertContains(back_to_image_response, reverse("order_wizard_photo_preview"))
        preview_response = self.client.get(reverse("order_wizard_photo_preview"))
        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(preview_response["Cache-Control"], "no-store")

        keep_image_response = self.client.post(f"{reverse('order_create')}?step=image", {})
        self.assertRedirects(keep_image_response, f"{reverse('order_create')}?step=details")

        details_response = self.client.get(f"{reverse('order_create')}?step=details")
        self.assertEqual(details_response.status_code, 200)
        self.assertContains(details_response, "PLA")
        self.assertNotContains(details_response, "Test Acrylic")

        missing_material_response = self.client.post(
            f"{reverse('order_create')}?step=details",
            {"material": "", "quantity": 1, "comment": ""},
        )
        self.assertEqual(missing_material_response.status_code, 200)
        self.assertContains(missing_material_response, "Выберите материал.")

        create_response = self.client.post(
            f"{reverse('order_create')}?step=details",
            {"material": self.print_material.pk, "quantity": 2, "comment": "Срочно"},
        )
        self.assertRedirects(create_response, f"{reverse('order_create')}?step=confirm")
        self.assertEqual(Order.objects.count(), 0)

        confirm_response = self.client.get(f"{reverse('order_create')}?step=confirm")
        self.assertEqual(confirm_response.status_code, 200)
        self.assertContains(confirm_response, "Корпус")
        self.assertContains(confirm_response, "PLA")
        self.assertContains(confirm_response, "model.stl")
        self.assertContains(confirm_response, "12.3 x 56 x 7.9 мм")

        final_response = self.client.post(f"{reverse('order_create')}?step=confirm")
        order = Order.objects.get()
        self.assertRedirects(final_response, reverse("order_detail", args=[order.pk]))
        self.assertEqual(order.title, "Корпус")
        self.assertEqual(order.quantity, 2)
        self.assertEqual(order.material, self.print_material)
        self.assertEqual(order.model_dimensions_display, "12.3 x 56 x 7.9 мм")
        self.assertEqual(OrderDraft.objects.count(), 0)

    def test_only_admin_can_mark_new_3d_order_as_priority(self):
        user = User.objects.create_user("maker", password="StrongPass12345!")
        regular_form = OrderDetailsStepForm(
            data={"material": self.print_material.pk, "quantity": 10, "comment": "", "is_priority": "on"},
            production_type=ProductionType.PRINT_3D,
            user=user,
        )
        self.assertTrue(regular_form.is_valid())
        self.assertNotIn("is_priority", regular_form.fields)
        self.assertFalse(regular_form.cleaned_data.get("is_priority", False))

        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        admin_form = OrderDetailsStepForm(
            data={"material": self.print_material.pk, "quantity": 10, "comment": "", "is_priority": "on"},
            production_type=ProductionType.PRINT_3D,
            user=admin,
        )
        self.assertTrue(admin_form.is_valid())
        self.assertIn("is_priority", admin_form.fields)
        self.assertTrue(admin_form.cleaned_data["is_priority"])

    def test_admin_can_create_priority_order_from_wizard(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        self.client.force_login(admin)

        self.client.get(reverse("order_create"), {"production_type": ProductionType.PRINT_3D})
        self.client.post(f"{reverse('order_create')}?step=title", {"title": "Big batch"})
        self.client.post(f"{reverse('order_create')}?step=file", {"production_file": upload("batch.stl")})
        self.client.post(f"{reverse('order_create')}?step=image", {"photo": image_upload()})
        response = self.client.post(
            f"{reverse('order_create')}?step=details",
            {
                "material": self.print_material.pk,
                "quantity": 12,
                "comment": "Печатать при свободных принтерах",
                "is_priority": "on",
            },
        )

        self.assertRedirects(response, f"{reverse('order_create')}?step=confirm")
        response = self.client.post(f"{reverse('order_create')}?step=confirm")
        order = Order.objects.get(title="Big batch")
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        self.assertTrue(order.is_priority)
        self.assertEqual(order.quantity, 12)
        self.assertEqual(order.priority_remaining_quantity, 12)

    def test_priority_print_batch_reduces_remaining_quantity(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        order = Order.objects.create(
            user=admin,
            title="Priority",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=10,
            photo=image_upload(),
            production_file=upload("priority.stl"),
            is_priority=True,
            status=OrderStatus.PENDING,
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("admin_order_priority_print_batch", args=[order.pk]),
            {"copies": 4, "next": reverse("admin_order_queue")},
        )
        self.assertRedirects(response, reverse("admin_order_queue"))
        order.refresh_from_db()
        self.assertEqual(order.priority_remaining_quantity, 6)
        self.assertEqual(order.priority_started_quantity, 4)
        self.assertEqual(order.status, OrderStatus.IN_PROGRESS)
        self.assertEqual(OrderStatusLog.objects.filter(order=order).count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="priority_print_batch").count(), 1)

        response = self.client.post(
            reverse("admin_order_priority_print_batch", args=[order.pk]),
            {"copies": 7, "next": reverse("admin_order_queue")},
        )
        self.assertRedirects(response, reverse("admin_order_queue"))
        order.refresh_from_db()
        self.assertEqual(order.priority_remaining_quantity, 6)

    def test_priority_order_done_when_remaining_quantity_reaches_zero(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        order = Order.objects.create(
            user=admin,
            title="Batch",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=6,
            photo=image_upload(),
            production_file=upload("batch.stl"),
            is_priority=True,
            priority_remaining_quantity=6,
            status=OrderStatus.IN_PROGRESS,
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("admin_order_priority_print_batch", args=[order.pk]),
            {"copies": 6, "next": reverse("admin_order_queue")},
        )

        self.assertRedirects(response, reverse("admin_order_queue"))
        order.refresh_from_db()
        self.assertEqual(order.priority_remaining_quantity, 0)
        self.assertEqual(order.status, OrderStatus.DONE)
        self.assertEqual(OrderStatusLog.objects.get(order=order).new_status, OrderStatus.DONE)

    def test_active_prefix_rule_assigns_separate_public_numbers_to_new_orders(self):
        today = timezone.localdate()
        rule = OrderPrefixRule.objects.create(
            prefix="R-",
            starts_on=today - timedelta(days=1),
            ends_on=today + timedelta(days=1),
            next_number=7,
        )
        inactive_rule = OrderPrefixRule.objects.create(
            prefix="OLD-",
            starts_on=today - timedelta(days=3),
            ends_on=today - timedelta(days=2),
            next_number=1,
        )
        user = User.objects.create_user("maker", password="StrongPass12345!")

        first_order = Order.objects.create(
            user=user,
            title="Prefixed 1",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("prefixed1.stl"),
        )
        second_order = Order.objects.create(
            user=user,
            title="Prefixed 2",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("prefixed2.stl"),
        )

        rule.refresh_from_db()
        inactive_rule.refresh_from_db()
        self.assertEqual(first_order.display_number, "R-7")
        self.assertEqual(second_order.display_number, "R-8")
        self.assertEqual(rule.next_number, 9)
        self.assertEqual(inactive_rule.next_number, 1)

    def test_admin_can_delete_all_orders_with_prefix(self):
        today = timezone.localdate()
        rule = OrderPrefixRule.objects.create(
            prefix="DEL-",
            starts_on=today - timedelta(days=1),
            ends_on=today + timedelta(days=1),
        )
        user = User.objects.create_user("maker", password="StrongPass12345!")
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        prefixed_order = Order.objects.create(
            user=user,
            title="Delete me",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("delete-me.stl"),
        )
        other_order = Order.objects.create(
            user=user,
            title="Keep me",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("keep-me.stl"),
            prefix_text="KEEP-",
            prefix_number=1,
        )
        self.client.force_login(admin)

        response = self.client.post(reverse("prefix_rule_delete_orders", args=[rule.pk]))

        self.assertRedirects(response, reverse("prefix_rule_list"))
        self.assertFalse(Order.objects.filter(pk=prefixed_order.pk).exists())
        self.assertTrue(Order.objects.filter(pk=other_order.pk).exists())
        self.assertEqual(AuditLog.objects.filter(action="delete_orders_by_prefix").count(), 1)

    def test_admin_can_manage_countdown_and_layout_shows_it(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        self.client.force_login(admin)
        target_date = timezone.localdate() + timedelta(days=9)

        response = self.client.post(
            reverse("countdown_create"),
            {"title": "До старта", "target_date": target_date.isoformat(), "is_active": "on"},
        )

        self.assertRedirects(response, reverse("info_panel"))
        countdown = Countdown.objects.get(title="До старта")
        self.assertEqual(countdown.days_left, 9)
        self.assertEqual(countdown.days_left_word, "дней")
        page_response = self.client.get(reverse("orders"))
        self.assertContains(page_response, "До старта")
        self.assertContains(page_response, "countdown-panel")

    def test_countdown_days_left_word_declension(self):
        today = timezone.localdate()
        cases = [
            (1, "день"),
            (2, "дня"),
            (4, "дня"),
            (5, "дней"),
            (11, "дней"),
            (14, "дней"),
            (21, "день"),
            (23, "дня"),
        ]

        for days, expected_word in cases:
            with self.subTest(days=days):
                countdown = Countdown(title=f"До {days}", target_date=today + timedelta(days=days))
                self.assertEqual(countdown.days_left_word, expected_word)

    def test_empty_sidebar_blocks_are_hidden_when_not_configured(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        self.client.force_login(admin)

        response = self.client.get(reverse("admin_order_queue"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "left-sidebar")
        self.assertNotContains(response, "duty-panel")
        self.assertNotContains(response, "countdown-panel")
        self.assertNotContains(response, "pinned-announcement")

    def test_admin_can_manage_duty_schedule_and_admin_layout_shows_it(self):
        admin = User.objects.create_user(
            "admin",
            password="StrongPass12345!",
            is_staff=True,
            last_name="Иванов",
            first_name="Петр",
        )
        user = User.objects.create_user("user", password="StrongPass12345!")
        self.client.force_login(admin)
        person = admin_duty_people_queryset().get(user=admin)
        schedule = DutySchedule.objects.create(name="Обычный")
        DutyScheduleRule.objects.create(weekday=timezone.localdate().weekday(), schedule=schedule)

        response = self.client.post(
            reverse("duty_slot_create"),
            {
                "schedule": schedule.pk,
                "person": person.pk,
                "unload_time": "16:30",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("duty_slot_list"))
        slot = DutySlot.objects.get(person=person)
        self.assertEqual(slot.full_name, "Иванов Петр")
        self.assertEqual(slot.schedule, schedule)
        admin_page = self.client.get(reverse("admin_order_queue"))
        self.assertContains(admin_page, "График дежурства")
        self.assertContains(admin_page, "Иванов Петр")
        self.assertContains(admin_page, "16:30")
        self.assertContains(admin_page, "Пропуск")

        self.client.force_login(user)
        user_page = self.client.get(reverse("orders"))
        self.assertNotContains(user_page, "Иванов Петр")

    def test_bulk_select_controls_are_rendered_for_orders_and_users(self):
        admin = User.objects.create_user(
            "admin",
            password="StrongPass12345!",
            is_staff=True,
            is_superuser=True,
        )
        user = User.objects.create_user("user", password="StrongPass12345!")
        Order.objects.create(
            user=user,
            title="Select test",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("select-test.stl"),
        )
        self.client.force_login(admin)

        queue_response = self.client.get(reverse("admin_order_queue"))
        user_response = self.client.get(reverse("user_list"))

        self.assertContains(queue_response, 'data-select-all="orders"')
        self.assertContains(queue_response, 'data-select-item="orders"')
        self.assertContains(user_response, 'data-select-all="users"')
        self.assertContains(user_response, 'data-select-item="users"')

    def test_page_transition_assets_are_disabled_by_default(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        self.client.force_login(admin)

        response = self.client.get(reverse("orders"))

        self.assertNotContains(response, "static/js/page-transitions.js")
        self.assertContains(response, "static/css/app.css")
        self.assertContains(response, "20260908-7")
        self.assertTrue((Path(__file__).resolve().parent.parent / "static/js/page-transitions.js").exists())

    @override_settings(ENABLE_PAGE_TRANSITIONS=True)
    def test_page_transition_assets_can_be_enabled(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        self.client.force_login(admin)

        response = self.client.get(reverse("orders"))

        self.assertContains(response, "static/js/page-transitions.js")

    def test_admin_can_edit_named_duty_schedule_and_weekday_rule(self):
        admin = User.objects.create_user(
            "admin",
            password="StrongPass12345!",
            is_staff=True,
            last_name="Сидоров",
            first_name="Иван",
        )
        second_admin = User.objects.create_user(
            "second-admin",
            password="StrongPass12345!",
            is_staff=True,
            last_name="Петрова",
            first_name="Анна",
        )
        self.client.force_login(admin)
        today_weekday = timezone.localdate().weekday()
        people = admin_duty_people_queryset()
        sidoro = people.get(user=admin)
        petrova = people.get(user=second_admin)
        schedule = DutySchedule.objects.create(name="Вечерний")

        response = self.client.post(
            reverse("duty_schedule_edit", args=[schedule.pk]),
            {
                "slots-TOTAL_FORMS": "2",
                "slots-INITIAL_FORMS": "0",
                "slots-MIN_NUM_FORMS": "0",
                "slots-MAX_NUM_FORMS": "1000",
                "slots-0-schedule": schedule.pk,
                "slots-0-person": sidoro.pk,
                "slots-0-unload_time": "12:00",
                "slots-0-is_active": "on",
                "slots-1-schedule": schedule.pk,
                "slots-1-person": petrova.pk,
                "slots-1-unload_time": "18:15",
                "slots-1-is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("duty_slot_list"))
        self.assertEqual(DutySlot.objects.count(), 2)
        self.assertTrue(DutySlot.objects.filter(schedule=schedule, person=sidoro).exists())
        self.assertEqual(AuditLog.objects.filter(action="create_duty_slot").count(), 2)
        rule_response = self.client.post(
            reverse("duty_schedule_rule_create"),
            {"weekday": today_weekday, "schedule": schedule.pk, "is_active": "on"},
        )
        self.assertRedirects(rule_response, reverse("duty_slot_list"))
        overview = self.client.get(reverse("duty_slot_list"))
        self.assertContains(overview, "Вечерний")
        self.assertContains(overview, "Сидоров Иван")

    def test_admin_can_create_duty_skip_with_replacement(self):
        admin = User.objects.create_user(
            "admin",
            password="StrongPass12345!",
            is_staff=True,
            last_name="Иванов",
            first_name="Петр",
        )
        replacement_admin = User.objects.create_user(
            "replacement-admin",
            password="StrongPass12345!",
            is_staff=True,
            last_name="Смирнова",
            first_name="Ольга",
        )
        self.client.force_login(admin)
        today = timezone.localdate()
        people = admin_duty_people_queryset()
        primary_person = people.get(user=admin)
        replacement_person = people.get(user=replacement_admin)
        schedule = DutySchedule.objects.create(name="Обычный")
        DutyScheduleRule.objects.create(weekday=today.weekday(), schedule=schedule)
        primary = DutySlot.objects.create(
            schedule=schedule,
            person=primary_person,
            unload_time="16:30",
        )
        DutySlot.objects.create(
            schedule=schedule,
            person=replacement_person,
            unload_time="16:30",
        )

        response = self.client.post(
            reverse("duty_skip_create"),
            {
                "duty_slot": primary.pk,
                "unavailable_date": today.isoformat(),
                "reason": "Олимпиада",
                "replacement_person": replacement_person.pk,
            },
        )

        self.assertRedirects(response, reverse("duty_slot_list"))
        skip = DutySkip.objects.get(duty_slot=primary)
        self.assertEqual(skip.replacement_person, replacement_person)
        self.assertEqual(skip.unavailable_time, primary.unload_time)
        self.assertEqual(skip.reason, "Олимпиада")
        admin_page = self.client.get(reverse("admin_order_queue"))
        self.assertContains(admin_page, "Смирнова Ольга")
        self.assertContains(admin_page, "замена за Иванов Петр")
        self.assertEqual(AuditLog.objects.filter(action="create_duty_skip").count(), 1)

    def test_duty_people_are_taken_from_admin_accounts(self):
        admin = User.objects.create_user(
            "admin",
            password="StrongPass12345!",
            is_staff=True,
            last_name="Иванов",
            first_name="Петр",
        )
        User.objects.create_user(
            "regular",
            password="StrongPass12345!",
            last_name="Обычный",
            first_name="Пользователь",
        )
        manual_person = DutyPerson.objects.create(last_name="Ручной", first_name="Дежурный")
        self.client.force_login(admin)

        response = self.client.get(reverse("duty_slot_list"))

        self.assertContains(response, "Иванов")
        self.assertContains(response, "admin")
        self.assertNotContains(response, "Обычный")
        self.assertNotContains(response, "Ручной")
        self.assertNotContains(response, "Добавить дежурного")

        legacy_response = self.client.get(reverse("duty_person_update", args=[manual_person.pk]))
        self.assertRedirects(legacy_response, reverse("duty_slot_list"))

    def test_order_file_size_limit_is_50_mb(self):
        form = OrderCreateForm(
            data={
                "title": "Large",
                "production_type": ProductionType.PRINT_3D,
                "material": self.print_material.pk,
                "quantity": 1,
                "comment": "",
            },
            files={
                "photo": image_upload(),
                "production_file": upload_with_size("large.stl", MAX_UPLOAD_SIZE + 1),
            },
            production_type=ProductionType.PRINT_3D,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("production_file", form.errors)

    def test_customer_can_self_pickup_3d_order_when_enabled(self):
        user = User.objects.create_user("maker", password="StrongPass12345!")
        order = Order.objects.create(
            user=user,
            title="Деталь",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("part.stl"),
            status=OrderStatus.IN_PROGRESS,
        )
        self.client.force_login(user)

        page = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertContains(page, "Подтвердить самосъём")

        response = self.client.post(reverse("order_self_pickup", args=[order.pk]))

        self.assertRedirects(response, reverse("orders"))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DONE)
        self.assertTrue(
            OrderStatusLog.objects.filter(
                order=order,
                old_status=OrderStatus.IN_PROGRESS,
                new_status=OrderStatus.DONE,
                reason__contains="Самосъём",
            ).exists()
        )
        self.assertEqual(AuditLog.objects.filter(action="self_pickup_order").count(), 1)

    def test_customer_cannot_self_pickup_when_disabled(self):
        ProductionSettings.get_solo().save()
        settings_obj = ProductionSettings.get_solo()
        settings_obj.allow_self_pickup = False
        settings_obj.save()
        user = User.objects.create_user("maker", password="StrongPass12345!")
        order = Order.objects.create(
            user=user,
            title="Деталь",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("part.stl"),
            status=OrderStatus.IN_PROGRESS,
        )
        self.client.force_login(user)

        page = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertNotContains(page, "Подтвердить самосъём")
        response = self.client.post(reverse("order_self_pickup", args=[order.pk]))

        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.IN_PROGRESS)

    def test_admin_can_disable_self_pickup_from_info_panel(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        self.client.force_login(admin)

        response = self.client.post(reverse("info_panel"), {"save_production_settings": "1"})

        self.assertRedirects(response, reverse("info_panel"))
        self.assertFalse(ProductionSettings.get_solo().allow_self_pickup)
        self.assertEqual(AuditLog.objects.filter(action="update_production_settings").count(), 1)

    def test_customer_can_continue_and_delete_order_draft(self):
        user = User.objects.create_user("maker", password="StrongPass12345!")
        draft = OrderDraft.objects.create(
            user=user,
            production_type=ProductionType.PRINT_3D,
            title="Draft part",
            current_step="file",
        )
        self.client.force_login(user)

        list_response = self.client.get(reverse("orders"))
        self.assertContains(list_response, "Черновики")
        self.assertContains(list_response, "Draft part")

        continue_response = self.client.get(reverse("order_draft_continue", args=[draft.pk]))
        self.assertRedirects(continue_response, f"{reverse('order_create')}?step=file")

        delete_response = self.client.post(reverse("order_draft_delete", args=[draft.pk]))
        self.assertRedirects(delete_response, reverse("orders"))
        self.assertFalse(OrderDraft.objects.filter(pk=draft.pk).exists())

    def test_order_file_is_renamed_after_save(self):
        user = User.objects.create_user("maker", password="StrongPass12345!")
        order = Order.objects.create(
            user=user,
            title="Кронштейн",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=2,
            photo=image_upload(),
            production_file=upload("original.STP"),
        )
        self.assertEqual(order.production_file.name, f"orders/files/{order.pk}_3d.stp")

    def test_admin_order_detail_shows_customer_full_name(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user(
            "maker",
            password="StrongPass12345!",
            first_name="Иван",
            last_name="Петров",
        )
        order = Order.objects.create(
            user=user,
            title="Деталь",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=2,
            photo=image_upload(),
            production_file=upload("part.stl"),
            model_width=10,
            model_depth=20.5,
            model_height=30,
            admin_comment="Внутренняя заметка",
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertContains(response, "Заказчик")
        self.assertContains(response, "Петров Иван")
        self.assertContains(response, "(maker)")
        self.assertContains(response, "Ориентировочные размеры")
        self.assertContains(response, "10 x 20.5 x 30 мм")
        self.assertContains(response, "Внутренняя заметка")
        queue_response = self.client.get(reverse("admin_order_queue"))
        self.assertContains(queue_response, "10 x 20.5 x 30 мм")

        self.client.force_login(user)
        user_response = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertNotContains(user_response, "Ориентировочные размеры")
        self.assertNotContains(user_response, "Внутренняя заметка")

    def test_order_file_download_name_includes_customer_full_name(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user(
            "maker",
            password="StrongPass12345!",
            first_name="Ivan",
            last_name="Petrov",
        )
        other = User.objects.create_user("other", password="StrongPass12345!")
        order = Order.objects.create(
            user=user,
            title="Деталь",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=2,
            photo=image_upload(),
            production_file=upload("part.stl"),
        )

        self.client.force_login(admin)
        response = self.client.get(reverse("order_file_download", args=[order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f'{order.pk}_3d_Petrov_Ivan_x2.stl',
            response["Content-Disposition"],
        )
        response.close()

        self.client.force_login(other)
        self.assertEqual(
            self.client.get(reverse("order_file_download", args=[order.pk])).status_code,
            403,
        )

    def test_customer_filters_orders_and_repeats_order_with_files(self):
        user = User.objects.create_user("maker", password="StrongPass12345!")
        done_order = Order.objects.create(
            user=user,
            title="Done part",
            production_type=ProductionType.LASER_CUT,
            material=self.laser_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("done.dxf"),
            status=OrderStatus.DONE,
        )
        source_order = Order.objects.create(
            user=user,
            title="Repeat me",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=4,
            photo=image_upload(),
            production_file=upload("repeat.stl"),
            comment="Same again",
        )
        self.client.force_login(user)

        filtered_response = self.client.get(
            reverse("orders"),
            {"status": OrderStatus.PENDING, "production_type": ProductionType.PRINT_3D},
        )
        self.assertContains(filtered_response, "Repeat me")
        self.assertNotContains(filtered_response, "Done part")

        repeat_response = self.client.post(reverse("order_repeat", args=[source_order.pk]))
        repeated_order = Order.objects.exclude(pk__in=[done_order.pk, source_order.pk]).get()
        self.assertRedirects(repeat_response, reverse("order_detail", args=[repeated_order.pk]))
        self.assertEqual(repeated_order.status, OrderStatus.PENDING)
        self.assertEqual(repeated_order.title, source_order.title)
        self.assertEqual(repeated_order.quantity, 4)
        self.assertEqual(repeated_order.comment, "Same again")
        self.assertTrue(repeated_order.photo)
        self.assertTrue(repeated_order.production_file)
        self.assertEqual(AuditLog.objects.filter(action="repeat_order").count(), 1)

    def test_customer_can_cancel_own_pending_order(self):
        user = User.objects.create_user("maker", password="StrongPass12345!")
        order = Order.objects.create(
            user=user,
            title="Cancel me",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("cancel.stl"),
        )
        self.client.force_login(user)

        detail_response = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertContains(detail_response, "Отменить заказ")

        confirm_response = self.client.get(reverse("order_cancel_confirm", args=[order.pk]))
        self.assertContains(confirm_response, "Отменить заказ")
        self.assertContains(confirm_response, "Cancel me")

        response = self.client.post(reverse("order_cancel", args=[order.pk]))
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CANCELLED)

        log = OrderStatusLog.objects.get(order=order)
        self.assertEqual(log.changed_by, user)
        self.assertEqual(log.old_status, OrderStatus.PENDING)
        self.assertEqual(log.new_status, OrderStatus.CANCELLED)
        self.assertEqual(log.reason, "Отменено заказчиком.")

    def test_customer_cannot_cancel_other_in_progress_or_finished_order(self):
        owner = User.objects.create_user("owner", password="StrongPass12345!")
        other = User.objects.create_user("other", password="StrongPass12345!")
        order = Order.objects.create(
            user=owner,
            title="Other order",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("other.stl"),
        )
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse("order_cancel", args=[order.pk])).status_code, 403)

        order.status = OrderStatus.IN_PROGRESS
        order.save(update_fields=["status", "updated_at"])
        self.client.force_login(owner)
        response = self.client.post(reverse("order_cancel", args=[order.pk]))
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.IN_PROGRESS)

        detail_response = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertNotContains(detail_response, "Отменить заказ")

        order.status = OrderStatus.DONE
        order.save(update_fields=["status", "updated_at"])
        response = self.client.post(reverse("order_cancel", args=[order.pk]))
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DONE)

    def test_admin_queue_defaults_to_oldest_active_orders_and_archive_is_separate(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        first_user = User.objects.create_user(
            "first_customer",
            password="StrongPass12345!",
            first_name="First",
            last_name="Customer",
        )
        second_user = User.objects.create_user(
            "second_customer",
            password="StrongPass12345!",
            first_name="Second",
            last_name="Customer",
        )
        old_order = Order.objects.create(
            user=first_user,
            title="Old active",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("old.stl"),
        )
        new_order = Order.objects.create(
            user=second_user,
            title="New active",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("new.stl"),
        )
        ready_order = Order.objects.create(
            user=first_user,
            title="Ready active",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("ready.stl"),
            status=OrderStatus.READY,
        )
        archived_order = Order.objects.create(
            user=first_user,
            title="Archived",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("archived.stl"),
            status=OrderStatus.DONE,
        )
        self.client.force_login(admin)

        queue_response = self.client.get(reverse("admin_order_queue"))
        self.assertContains(queue_response, "New active")
        self.assertContains(queue_response, "Old active")
        self.assertContains(queue_response, "Second Customer")
        self.assertNotContains(queue_response, "second_customer")
        self.assertNotContains(queue_response, "Ready active")
        self.assertNotContains(queue_response, "Archived")
        content = queue_response.content.decode()
        self.assertLess(content.index("Old active"), content.index("New active"))

        customer_response = self.client.get(
            reverse("admin_order_queue"),
            {"customer": first_user.pk},
        )
        self.assertContains(customer_response, "Old active")
        self.assertNotContains(customer_response, "New active")
        self.assertNotContains(customer_response, "Ready active")

        ready_response = self.client.get(reverse("admin_order_ready"))
        self.assertContains(ready_response, "Ready active")
        self.assertNotContains(ready_response, "Old active")
        self.assertNotContains(ready_response, "Archived")

        ready_customer_response = self.client.get(
            reverse("admin_order_ready"),
            {"customer": first_user.pk},
        )
        self.assertContains(ready_customer_response, "Ready active")
        self.assertNotContains(ready_customer_response, "New active")

        archive_response = self.client.get(reverse("admin_order_archive"))
        self.assertContains(archive_response, "Archived")
        self.assertNotContains(archive_response, "Old active")
        self.assertNotContains(archive_response, "New active")
        self.assertNotContains(archive_response, "Ready active")

        archive_customer_response = self.client.get(
            reverse("admin_order_archive"),
            {"customer": first_user.pk},
        )
        self.assertContains(archive_customer_response, "Archived")
        self.assertNotContains(archive_customer_response, "New active")

    def test_semi_printer_sees_only_in_progress_queue(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        semi_printer = User.objects.create_user(
            "semi",
            password="StrongPass12345!",
            first_name="Semi",
            last_name="Printer",
        )
        group, _ = Group.objects.get_or_create(name=SEMI_PRINTER_GROUP)
        semi_printer.groups.add(group)
        pending_order = Order.objects.create(
            user=admin,
            title="Pending part",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("pending-part.stl"),
        )
        work_order = Order.objects.create(
            user=admin,
            title="Work part",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("work-part.stl"),
            status=OrderStatus.IN_PROGRESS,
        )
        ready_order = Order.objects.create(
            user=admin,
            title="Ready part",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("ready-part.stl"),
            status=OrderStatus.READY,
        )
        self.client.force_login(semi_printer)

        home_response = self.client.get(reverse("home"))
        self.assertRedirects(home_response, reverse("admin_order_queue"))
        queue_response = self.client.get(reverse("admin_order_queue"))

        self.assertEqual(queue_response.status_code, 200)
        self.assertFalse(semi_printer.is_staff)
        self.assertContains(queue_response, "Work part")
        self.assertNotContains(queue_response, "Pending part")
        self.assertNotContains(queue_response, "Ready part")
        self.assertContains(queue_response, reverse("production_order_mark_ready", args=[work_order.pk]))
        self.assertNotContains(queue_response, reverse("admin_order_update", args=[work_order.pk]))
        self.assertNotContains(queue_response, "Экспорт CSV")
        self.assertNotContains(queue_response, "Массовая загрузка")
        self.assertNotContains(queue_response, "Комментарий администратора")
        self.assertEqual(self.client.get(reverse("user_list")).status_code, 302)
        self.assertEqual(self.client.get(reverse("admin_order_ready")).status_code, 302)
        self.assertEqual(self.client.get(reverse("order_file_download", args=[work_order.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("order_file_download", args=[pending_order.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("order_file_download", args=[ready_order.pk])).status_code, 403)

    def test_semi_printer_can_only_mark_in_progress_order_ready(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        semi_printer = User.objects.create_user("semi", password="StrongPass12345!")
        group, _ = Group.objects.get_or_create(name=SEMI_PRINTER_GROUP)
        semi_printer.groups.add(group)
        pending_order = Order.objects.create(
            user=admin,
            title="Pending part",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("semi-pending.stl"),
        )
        work_order = Order.objects.create(
            user=admin,
            title="Work part",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("semi-work.stl"),
            status=OrderStatus.IN_PROGRESS,
        )
        self.client.force_login(semi_printer)

        response = self.client.post(
            reverse("production_order_mark_ready", args=[work_order.pk]),
            {"pickup_cell": "6", "next": reverse("admin_order_queue")},
        )

        self.assertRedirects(response, reverse("admin_order_queue"))
        work_order.refresh_from_db()
        self.assertEqual(work_order.status, OrderStatus.READY)
        self.assertEqual(work_order.pickup_cell, 6)
        status_log = OrderStatusLog.objects.get(order=work_order)
        self.assertEqual(status_log.changed_by, semi_printer)
        self.assertEqual(status_log.old_status, OrderStatus.IN_PROGRESS)
        self.assertEqual(status_log.new_status, OrderStatus.READY)
        self.assertEqual(AuditLog.objects.filter(action="semi_printer_mark_ready").count(), 1)

        blocked_response = self.client.post(
            reverse("production_order_mark_ready", args=[pending_order.pk]),
            {"pickup_cell": "7", "next": reverse("admin_order_queue")},
        )
        self.assertRedirects(blocked_response, reverse("admin_order_queue"))
        pending_order.refresh_from_db()
        self.assertEqual(pending_order.status, OrderStatus.PENDING)
        self.assertIsNone(pending_order.pickup_cell)

        admin_update_response = self.client.post(
            reverse("admin_order_update", args=[pending_order.pk]),
            {"status": OrderStatus.READY, "material": self.print_material.pk, "pickup_cell": "7"},
        )
        self.assertEqual(admin_update_response.status_code, 302)
        pending_order.refresh_from_db()
        self.assertEqual(pending_order.status, OrderStatus.PENDING)

    def test_admin_navigation_is_grouped(self):
        admin = User.objects.create_user(
            "admin",
            password="StrongPass12345!",
            first_name="Admin",
            last_name="Owner",
            is_staff=True,
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("admin_order_queue"))

        self.assertContains(response, "Производство")
        self.assertContains(response, "Создание")
        self.assertContains(response, "Управление")
        self.assertContains(response, "Отчетность")
        self.assertContains(response, "Owner Admin")
        self.assertContains(response, "data-theme-toggle")
        self.assertContains(response, reverse("admin_dashboard"))
        self.assertContains(response, reverse("rejection_reason_list"))
        self.assertContains(response, reverse("info_panel"))
        self.assertContains(response, reverse("prefix_rule_list"))
        self.assertContains(response, reverse("audit_log_list"))
        self.assertContains(response, "Причины отклонения")

    def test_admin_dashboard_and_csv_export(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user("maker", password="StrongPass12345!")
        order = Order.objects.create(
            user=user,
            title="Export me",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("export.stl"),
            admin_comment="Only admin",
        )
        self.client.force_login(admin)

        dashboard_response = self.client.get(reverse("admin_dashboard"))
        self.assertContains(dashboard_response, "Dashboard")
        self.assertContains(dashboard_response, "Новые")
        self.assertContains(dashboard_response, "3D-печать")

        export_response = self.client.get(reverse("admin_order_export"))
        self.assertEqual(export_response.status_code, 200)
        self.assertIn("text/csv", export_response["Content-Type"])
        content = export_response.content.decode("utf-8-sig")
        self.assertIn("Export me", content)
        self.assertIn("Only admin", content)
        self.assertEqual(AuditLog.objects.filter(action="export_orders").count(), 1)

        ready_export = self.client.get(reverse("admin_order_ready_export"))
        self.assertEqual(ready_export.status_code, 200)
        archive_export = self.client.get(reverse("admin_order_archive_export"))
        self.assertEqual(archive_export.status_code, 200)

    def test_admin_can_bulk_update_order_statuses(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user("maker", password="StrongPass12345!")
        first_order = Order.objects.create(
            user=user,
            title="First",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("first.stl"),
        )
        second_order = Order.objects.create(
            user=user,
            title="Second",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("second.stl"),
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("admin_orders_bulk_status_update"),
            {
                "order_ids": [first_order.pk, second_order.pk],
                "status": OrderStatus.IN_PROGRESS,
                "rejection_reason": "",
                "next": reverse("admin_order_queue"),
            },
        )
        self.assertRedirects(response, reverse("admin_order_queue"))
        first_order.refresh_from_db()
        second_order.refresh_from_db()
        self.assertEqual(first_order.status, OrderStatus.IN_PROGRESS)
        self.assertEqual(second_order.status, OrderStatus.IN_PROGRESS)
        self.assertEqual(OrderStatusLog.objects.count(), 2)
        self.assertTrue(
            OrderStatusLog.objects.filter(
                changed_by=admin,
                old_status=OrderStatus.PENDING,
                new_status=OrderStatus.IN_PROGRESS,
            ).exists()
        )

        missing_reason_response = self.client.post(
            reverse("admin_orders_bulk_status_update"),
            {
                "order_ids": [first_order.pk],
                "status": OrderStatus.REJECTED,
                "rejection_reason": "",
                "next": reverse("admin_order_queue"),
            },
        )
        self.assertRedirects(missing_reason_response, reverse("admin_order_queue"))
        first_order.refresh_from_db()
        self.assertEqual(first_order.status, OrderStatus.IN_PROGRESS)

        rejected_response = self.client.post(
            reverse("admin_orders_bulk_status_update"),
            {
                "order_ids": [first_order.pk, second_order.pk],
                "status": OrderStatus.REJECTED,
                "rejection_reason": "Файлы не подходят.",
                "next": reverse("admin_order_queue"),
            },
        )
        self.assertRedirects(rejected_response, reverse("admin_order_queue"))
        first_order.refresh_from_db()
        second_order.refresh_from_db()
        self.assertEqual(first_order.status, OrderStatus.REJECTED)
        self.assertEqual(second_order.status, OrderStatus.REJECTED)
        self.assertEqual(first_order.rejection_reason, "Файлы не подходят.")
        self.assertEqual(second_order.rejection_reason, "Файлы не подходят.")

        done_response = self.client.post(
            reverse("admin_orders_bulk_status_update"),
            {
                "order_ids": [first_order.pk],
                "status": OrderStatus.DONE,
                "rejection_reason": "",
                "next": reverse("admin_order_archive"),
            },
        )
        self.assertRedirects(done_response, reverse("admin_order_archive"))
        first_order.refresh_from_db()
        self.assertEqual(first_order.status, OrderStatus.REJECTED)

    def test_admin_can_bulk_reject_with_standard_reason(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user("maker", password="StrongPass12345!")
        reason = RejectionReason.objects.create(text="Неверный формат модели")
        inactive_reason = RejectionReason.objects.create(text="Устаревшая причина", is_active=False)
        order = Order.objects.create(
            user=user,
            title="Bulk standard",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("bulk-standard.stl"),
        )
        self.client.force_login(admin)

        page_response = self.client.get(reverse("admin_order_queue"))
        self.assertContains(page_response, "Неверный формат модели")
        self.assertNotContains(page_response, "Устаревшая причина")

        response = self.client.post(
            reverse("admin_orders_bulk_status_update"),
            {
                "order_ids": [order.pk],
                "status": OrderStatus.REJECTED,
                "standard_rejection_reason": reason.pk,
                "rejection_reason": "",
                "next": reverse("admin_order_queue"),
            },
        )
        self.assertRedirects(response, reverse("admin_order_queue"))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertEqual(order.rejection_reason, "Неверный формат модели")
        log = OrderStatusLog.objects.get(order=order)
        self.assertEqual(log.reason, "Неверный формат модели")

    def test_bulk_upload_creates_orders(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        self.client.force_login(admin)

        response = self.client.post(
            reverse("bulk_order_create"),
            {
                "files": [upload("one.stl"), upload("two.stp")],
            },
        )
        self.assertRedirects(response, f"{reverse('bulk_order_create')}?step=edit")
        items = self.client.session["bulk_order_upload"]["items"]
        edit_response = self.client.get(f"{reverse('bulk_order_create')}?step=edit")
        self.assertContains(edit_response, 'data-bulk-material-source="3d_print"')
        self.assertContains(edit_response, 'data-bulk-material-source="laser_cut"')
        self.assertContains(edit_response, 'data-bulk-material-row="3d_print"', count=2)
        self.assertContains(edit_response, "bulk-order-edit.js")

        source_response = self.client.get(reverse("bulk_order_source_file", args=[items[0]["token"]]))
        self.assertEqual(source_response.status_code, 200)
        self.assertEqual(source_response["Cache-Control"], "no-store")

        missing_preview_response = self.client.post(
            reverse("bulk_order_preview_save", args=["missing-token"]),
            {"photo": image_upload()},
        )
        self.assertEqual(missing_preview_response.status_code, 404)

        stale_preview_response = self.client.post(
            reverse("bulk_order_preview_save", args=[items[0]["token"]]),
            {"source_name": "old-one.stl", "photo": image_upload()},
        )
        self.assertEqual(stale_preview_response.status_code, 409)

        preview_response = self.client.post(
            reverse("bulk_order_preview_save", args=[items[0]["token"]]),
            {
                "source_name": items[0]["original_name"],
                "photo": image_upload(),
                "model_width": "100",
                "model_depth": "40.5",
                "model_height": "12",
            },
        )
        self.assertEqual(preview_response.status_code, 200)
        preview_file_response = self.client.get(
            reverse("bulk_order_preview_file", args=[items[0]["token"]])
        )
        self.assertEqual(preview_file_response.status_code, 200)

        response = self.client.post(
            f"{reverse('bulk_order_create')}?step=edit",
            {
                "action": "create",
                "item-0-file_token": items[0]["token"],
                "item-0-title": "One",
                "item-0-material": self.print_material.pk,
                "item-0-quantity": 2,
                "item-1-file_token": items[1]["token"],
                "item-1-title": "Two",
                "item-1-photo": image_upload(),
                "item-1-material": self.print_material.pk,
                "item-1-quantity": 3,
            },
        )
        self.assertRedirects(response, reverse("admin_order_queue"))
        self.assertEqual(Order.objects.count(), 2)
        self.assertTrue(Order.objects.filter(title="One", quantity=2).exists())
        self.assertTrue(Order.objects.filter(title="Two", quantity=3).exists())
        self.assertEqual(Order.objects.get(title="One").model_dimensions_display, "100 x 40.5 x 12 мм")
        self.assertTrue(Order.objects.filter(production_file__endswith="_3d.stl").exists())
        self.assertTrue(Order.objects.filter(production_file__endswith="_3d.stp").exists())

    def test_bulk_item_form_rejects_wrong_material_and_long_title(self):
        wrong_material_form = BulkOrderItemForm(
            production_type=ProductionType.PRINT_3D,
            data={
                "file_token": "abc",
                "title": "Part",
                "material": self.laser_material.pk,
                "quantity": 1,
            },
        )
        self.assertFalse(wrong_material_form.is_valid())
        self.assertIn("material", wrong_material_form.errors)

        long_title_form = BulkOrderItemForm(
            production_type=ProductionType.PRINT_3D,
            data={
                "file_token": "abc",
                "title": "x" * 31,
                "material": self.print_material.pk,
                "quantity": 1,
            },
        )
        self.assertFalse(long_title_form.is_valid())
        self.assertIn("title", long_title_form.errors)

    def test_admin_can_delete_material_without_deleting_orders(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user("maker", password="StrongPass12345!")
        order = Order.objects.create(
            user=user,
            title="Part",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("part.stl"),
        )
        self.client.force_login(admin)

        response = self.client.post(reverse("material_delete", args=[self.print_material.pk]))
        self.assertRedirects(response, reverse("material_list"))
        self.assertFalse(Material.objects.filter(pk=self.print_material.pk).exists())
        order.refresh_from_db()
        self.assertIsNone(order.material)

    def test_admin_can_set_material_color(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        self.client.force_login(admin)

        response = self.client.post(
            reverse("material_create"),
            {
                "name": "PETG Color Test",
                "color": "Любой цвет",
                "production_type": ProductionType.PRINT_3D,
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("material_list"))
        material = Material.objects.get(name="PETG Color Test")
        self.assertEqual(material.color, "Любой цвет")
        self.assertEqual(AuditLog.objects.filter(action="create_material").count(), 1)

    def test_admin_can_manage_rejection_reasons(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        self.client.force_login(admin)

        create_response = self.client.post(
            reverse("rejection_reason_create"),
            {"text": "Файл не открывается", "is_active": "on"},
        )
        self.assertRedirects(create_response, reverse("rejection_reason_list"))
        reason = RejectionReason.objects.get(text="Файл не открывается")
        self.assertTrue(reason.is_active)

        update_response = self.client.post(
            reverse("rejection_reason_update", args=[reason.pk]),
            {"text": "Файл поврежден", "is_active": ""},
        )
        self.assertRedirects(update_response, reverse("rejection_reason_list"))
        reason.refresh_from_db()
        self.assertEqual(reason.text, "Файл поврежден")
        self.assertFalse(reason.is_active)

        delete_response = self.client.post(reverse("rejection_reason_delete", args=[reason.pk]))
        self.assertRedirects(delete_response, reverse("rejection_reason_list"))
        self.assertFalse(RejectionReason.objects.filter(pk=reason.pk).exists())

    def test_security_headers_are_set(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])
        self.assertIn("img-src 'self' data: blob:", response["Content-Security-Policy"])

    def test_admin_can_reject_order_with_reason_and_log_status_change(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user("maker", password="StrongPass12345!")
        order = Order.objects.create(
            user=user,
            title="Bad model",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("bad.stl"),
        )
        self.client.force_login(admin)

        missing_reason_response = self.client.post(
            reverse("admin_order_update", args=[order.pk]),
            {
                "status": OrderStatus.REJECTED,
                "material": self.print_material.pk,
                "rejection_reason": "",
                "next": reverse("order_detail", args=[order.pk]),
            },
        )
        self.assertRedirects(missing_reason_response, reverse("order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertEqual(OrderStatusLog.objects.count(), 0)

        response = self.client.post(
            reverse("admin_order_update", args=[order.pk]),
            {
                "status": OrderStatus.REJECTED,
                "material": self.print_material.pk,
                "rejection_reason": "Файл поврежден.",
                "next": reverse("order_detail", args=[order.pk]),
            },
        )
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertEqual(order.rejection_reason, "Файл поврежден.")

        log = OrderStatusLog.objects.get()
        self.assertEqual(log.changed_by, admin)
        self.assertEqual(log.old_status, OrderStatus.PENDING)
        self.assertEqual(log.new_status, OrderStatus.REJECTED)
        self.assertEqual(log.reason, "Файл поврежден.")

        self.client.force_login(user)
        detail_response = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertContains(detail_response, "Причина отклонения")
        self.assertContains(detail_response, "Файл поврежден.")
        self.assertContains(detail_response, "История статусов")
        self.assertContains(detail_response, "Отклонено")

        list_response = self.client.get(reverse("orders"))
        self.assertContains(list_response, "Причина: Файл поврежден.")

    def test_admin_can_reject_order_with_standard_reason(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user("maker", password="StrongPass12345!")
        reason = RejectionReason.objects.create(text="Нужно перезагрузить файл")
        order = Order.objects.create(
            user=user,
            title="Standard reject",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("standard-reject.stl"),
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("admin_order_update", args=[order.pk]),
            {
                "status": OrderStatus.REJECTED,
                "material": self.print_material.pk,
                "standard_rejection_reason": reason.pk,
                "rejection_reason": "",
                "next": reverse("admin_order_queue"),
            },
        )
        self.assertRedirects(response, reverse("admin_order_queue"))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertEqual(order.rejection_reason, "Нужно перезагрузить файл")
        log = OrderStatusLog.objects.get(order=order)
        self.assertEqual(log.reason, "Нужно перезагрузить файл")

    def test_order_dialog_access_and_validation(self):
        owner = User.objects.create_user("owner", password="StrongPass12345!")
        stranger = User.objects.create_user("stranger", password="StrongPass12345!")
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        order = Order.objects.create(
            user=owner,
            title="Dialog",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("dialog.stl"),
        )

        self.client.force_login(stranger)
        response = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse("order_message_create", args=[order.pk]), {"text": "Alien"})
        self.assertEqual(response.status_code, 403)

        self.client.force_login(owner)
        empty_response = self.client.post(reverse("order_message_create", args=[order.pk]), {"text": ""})
        self.assertRedirects(empty_response, reverse("order_detail", args=[order.pk]))
        self.assertEqual(OrderMessage.objects.count(), 0)

        response = self.client.post(reverse("order_message_create", args=[order.pk]), {"text": "Question"})
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        self.assertEqual(OrderMessage.objects.count(), 1)
        self.assertEqual(OrderMessage.objects.get().author, owner)

        detail_response = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertContains(detail_response, "Question")

        big_photo = SimpleUploadedFile(
            "big.gif",
            GIF_BYTES + (b"x" * (10 * 1024 * 1024 + 1)),
            content_type="image/gif",
        )
        photo_response = self.client.post(
            reverse("order_message_create", args=[order.pk]),
            {"text": "Photo", "photo": big_photo},
        )
        self.assertRedirects(photo_response, reverse("order_detail", args=[order.pk]))
        self.assertEqual(OrderMessage.objects.count(), 1)

        self.client.force_login(admin)
        response = self.client.post(reverse("order_message_create", args=[order.pk]), {"text": "Admin answer"})
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        self.assertEqual(OrderMessage.objects.count(), 2)

        order.status = OrderStatus.READY
        order.save(update_fields=["status", "updated_at"])
        closed_response = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertNotContains(closed_response, "Отправить сообщение")
        blocked_response = self.client.post(reverse("order_message_create", args=[order.pk]), {"text": "Late"})
        self.assertRedirects(blocked_response, reverse("order_detail", args=[order.pk]))
        self.assertEqual(OrderMessage.objects.count(), 2)

    def test_admin_quick_status_actions_log_status_and_audit(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user("maker", password="StrongPass12345!")
        reason = RejectionReason.objects.create(text="Bad scale")
        order = Order.objects.create(
            user=user,
            title="Quick",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("quick.stl"),
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("admin_order_quick_status", args=[order.pk]),
            {"status": OrderStatus.IN_PROGRESS, "next": reverse("admin_order_queue")},
        )
        self.assertRedirects(response, reverse("admin_order_queue"))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.IN_PROGRESS)

        response = self.client.post(
            reverse("admin_order_quick_status", args=[order.pk]),
            {"status": OrderStatus.READY, "next": reverse("admin_order_queue")},
        )
        self.assertRedirects(response, reverse("admin_order_queue"))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.READY)

        response = self.client.post(
            reverse("admin_order_quick_status", args=[order.pk]),
            {"status": OrderStatus.REJECTED, "next": reverse("admin_order_queue")},
        )
        self.assertRedirects(response, reverse("admin_order_queue"))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.READY)

        response = self.client.post(
            reverse("admin_order_quick_status", args=[order.pk]),
            {
                "status": OrderStatus.REJECTED,
                "standard_rejection_reason": reason.pk,
                "next": reverse("admin_order_queue"),
            },
        )
        self.assertRedirects(response, reverse("admin_order_queue"))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertEqual(order.rejection_reason, "Bad scale")
        self.assertEqual(OrderStatusLog.objects.filter(changed_by=admin).count(), 3)
        self.assertEqual(AuditLog.objects.filter(action="quick_status_update").count(), 3)

    def test_system_health_access_and_backup_states(self):
        user = User.objects.create_user("user", password="StrongPass12345!")
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)

        missing_backup_root = tempfile.mkdtemp()
        shutil.rmtree(missing_backup_root)
        self.client.force_login(user)
        with mock.patch.dict("os.environ", {"BACKUP_ROOT": missing_backup_root}):
            response = self.client.get(reverse("system_health"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(admin)
        with mock.patch.dict("os.environ", {"BACKUP_ROOT": missing_backup_root}):
            response = self.client.get(reverse("system_health"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Директория бэкапов не найдена")

        backup_root = tempfile.mkdtemp()
        try:
            backup_dir = Path(backup_root) / "20260908-120000"
            backup_dir.mkdir()
            with mock.patch.dict("os.environ", {"BACKUP_ROOT": backup_root}):
                response = self.client.get(reverse("system_health"))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "20260908-120000")
        finally:
            shutil.rmtree(backup_root, ignore_errors=True)

    def test_customer_order_list_shows_fresh_queue_ahead_count(self):
        user = User.objects.create_user("maker", password="StrongPass12345!")
        other = User.objects.create_user("other", password="StrongPass12345!")
        now = timezone.now()
        stale_order = Order.objects.create(
            user=other,
            title="Stale",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("stale.stl"),
        )
        first_user_order = Order.objects.create(
            user=user,
            title="First fresh",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("first-fresh.stl"),
        )
        fresh_pending = Order.objects.create(
            user=other,
            title="Fresh pending",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("fresh-pending.stl"),
        )
        fresh_progress = Order.objects.create(
            user=other,
            title="Fresh progress",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("fresh-progress.stl"),
            status=OrderStatus.IN_PROGRESS,
        )
        ready_order = Order.objects.create(
            user=other,
            title="Ready ignored",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("ready-ignored.stl"),
            status=OrderStatus.READY,
        )
        current_order = Order.objects.create(
            user=user,
            title="Current order",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("current-order.stl"),
        )
        Order.objects.filter(pk=stale_order.pk).update(created_at=now - timedelta(hours=25))
        Order.objects.filter(pk=first_user_order.pk).update(created_at=now - timedelta(hours=23))
        Order.objects.filter(pk=fresh_pending.pk).update(created_at=now - timedelta(hours=2))
        Order.objects.filter(pk=fresh_progress.pk).update(created_at=now - timedelta(hours=1))
        Order.objects.filter(pk=ready_order.pk).update(created_at=now - timedelta(minutes=30))
        Order.objects.filter(pk=current_order.pk).update(created_at=now)

        self.client.force_login(user)
        response = self.client.get(reverse("orders"))

        self.assertContains(response, "Свежих заказов впереди нет")
        self.assertContains(response, "Перед вами 3 свежих заказов")
        self.assertNotContains(response, "Перед вами 4 свежих заказов")

    def test_ready_order_reminder_stays_until_customer_confirms_receipt(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user("maker", password="StrongPass12345!")
        order = Order.objects.create(
            user=user,
            title="Ready part",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("ready-part.stl"),
        )

        self.client.force_login(admin)
        ready_response = self.client.post(
            reverse("admin_order_update", args=[order.pk]),
            {
                "status": OrderStatus.READY,
                "material": self.print_material.pk,
                "rejection_reason": "",
                "next": reverse("admin_order_queue"),
            },
        )
        self.assertRedirects(ready_response, reverse("admin_order_queue"))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.READY)
        self.assertNotContains(self.client.get(reverse("admin_order_queue")), "Ready part")
        self.assertContains(self.client.get(reverse("admin_order_ready")), "Ready part")

        self.client.force_login(user)
        list_response = self.client.get(reverse("orders"))
        self.assertContains(list_response, "Готовые заказы")
        self.assertContains(list_response, "Подтвердить получение")
        self.assertContains(list_response, "Ready part")

        archive_response = self.client.get(reverse("admin_order_archive"))
        self.assertEqual(archive_response.status_code, 302)

        confirm_response = self.client.post(reverse("order_confirm_receipt", args=[order.pk]))
        self.assertRedirects(confirm_response, reverse("orders"))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DONE)

        log = OrderStatusLog.objects.filter(order=order).latest("pk")
        self.assertEqual(log.changed_by, user)
        self.assertEqual(log.old_status, OrderStatus.READY)
        self.assertEqual(log.new_status, OrderStatus.DONE)
        self.assertEqual(log.reason, "Получение подтверждено заказчиком.")

        list_response = self.client.get(reverse("orders"))
        self.assertNotContains(list_response, "Готовые заказы")

        self.client.force_login(admin)
        archive_response = self.client.get(reverse("admin_order_archive"))
        self.assertContains(archive_response, "Ready part")
        queue_response = self.client.get(reverse("admin_order_queue"))
        self.assertNotContains(queue_response, "Ready part")

    def test_admin_can_assign_pickup_cell_to_ready_order(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user("maker", password="StrongPass12345!")
        order = Order.objects.create(
            user=user,
            title="Cell part",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("cell-part.stl"),
            status=OrderStatus.READY,
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("admin_order_update", args=[order.pk]),
            {
                "status": OrderStatus.READY,
                "material": self.print_material.pk,
                "pickup_cell": "7",
                "rejection_reason": "",
                "next": reverse("admin_order_ready"),
            },
        )
        self.assertRedirects(response, reverse("admin_order_ready"))
        order.refresh_from_db()
        self.assertEqual(order.pickup_cell, 7)
        self.assertContains(self.client.get(reverse("admin_order_ready")), "7")

        self.client.force_login(user)
        self.assertContains(self.client.get(reverse("orders")), "ячейка 7")
        self.assertContains(self.client.get(reverse("order_detail", args=[order.pk])), "Ячейка выдачи")

    def test_ready_orders_can_share_pickup_cell(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user("maker", password="StrongPass12345!")
        first_order = Order.objects.create(
            user=user,
            title="First cell",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("first-cell.stl"),
            status=OrderStatus.READY,
            pickup_cell=3,
        )
        second_order = Order.objects.create(
            user=user,
            title="Second cell",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("second-cell.stl"),
            status=OrderStatus.READY,
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("admin_order_update", args=[second_order.pk]),
            {
                "status": OrderStatus.READY,
                "material": self.print_material.pk,
                "pickup_cell": "3",
                "rejection_reason": "",
                "next": reverse("admin_order_ready"),
            },
        )
        self.assertRedirects(response, reverse("admin_order_ready"))
        first_order.refresh_from_db()
        second_order.refresh_from_db()
        self.assertEqual(first_order.pickup_cell, 3)
        self.assertEqual(second_order.pickup_cell, 3)

    def test_pickup_cell_clears_after_receipt_confirmation(self):
        user = User.objects.create_user("maker", password="StrongPass12345!")
        order = Order.objects.create(
            user=user,
            title="Cell done",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("cell-done.stl"),
            status=OrderStatus.READY,
            pickup_cell=11,
        )
        self.client.force_login(user)

        response = self.client.post(reverse("order_confirm_receipt", args=[order.pk]))
        self.assertRedirects(response, reverse("orders"))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DONE)
        self.assertIsNone(order.pickup_cell)

    def test_admin_cannot_mark_order_done_without_customer_confirmation(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user("maker", password="StrongPass12345!")
        order = Order.objects.create(
            user=user,
            title="No direct done",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("no-direct.stl"),
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("admin_order_update", args=[order.pk]),
            {
                "status": OrderStatus.DONE,
                "material": self.print_material.pk,
                "rejection_reason": "",
                "next": reverse("admin_order_queue"),
            },
        )
        self.assertRedirects(response, reverse("admin_order_queue"))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertEqual(OrderStatusLog.objects.count(), 0)

    def test_only_last_30_done_orders_are_kept_after_customer_confirms_receipt(self):
        user = User.objects.create_user("maker", password="StrongPass12345!")
        done_ids = []
        for index in range(30):
            order = Order.objects.create(
                user=user,
                title=f"Done {index}",
                production_type=ProductionType.PRINT_3D,
                material=self.print_material,
                quantity=1,
                photo=image_upload(),
                production_file=upload(f"done-{index}.stl"),
                status=OrderStatus.DONE,
            )
            done_ids.append(order.pk)

        pending_order = Order.objects.create(
            user=user,
            title="Newest done",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("newest.stl"),
            status=OrderStatus.READY,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("order_confirm_receipt", args=[pending_order.pk]),
        )
        self.assertRedirects(response, reverse("orders"))
        self.assertEqual(Order.objects.filter(status=OrderStatus.DONE).count(), 30)
        self.assertTrue(Order.objects.filter(pk=pending_order.pk).exists())
        self.assertFalse(Order.objects.filter(pk=done_ids[0]).exists())

    def test_user_stats_accessible_only_to_admins(self):
        user = User.objects.create_user("maker", password="StrongPass12345!")
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)

        self.client.force_login(user)
        response = self.client.get(reverse("user_stats", args=[user.pk]))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(admin)
        response = self.client.get(reverse("user_stats", args=[user.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "production/user_stats.html")

    def test_user_stats_counts_user_metrics_and_period(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        user = User.objects.create_user("maker", password="StrongPass12345!")
        pending_order = Order.objects.create(
            user=user,
            title="Fresh pending",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("fresh-pending.stl"),
            status=OrderStatus.PENDING,
        )
        Order.objects.create(
            user=user,
            title="Fresh done",
            production_type=ProductionType.LASER_CUT,
            material=self.laser_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("fresh-done.dxf"),
            status=OrderStatus.DONE,
        )
        old_order = Order.objects.create(
            user=user,
            title="Old ready",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("old-ready.stl"),
            status=OrderStatus.READY,
        )
        Order.objects.filter(pk=old_order.pk).update(created_at=timezone.now() - timedelta(days=45))
        AuditLog.objects.create(actor=user, action="self_pickup_order", target_type="Order", target_label="Fresh pending")
        AuditLog.objects.create(actor=user, action="repeat_order", target_type="Order", target_label="Fresh pending")
        OrderStatusLog.objects.create(
            order=pending_order,
            order_number=pending_order.pk,
            order_title=pending_order.title,
            changed_by=user,
            old_status=OrderStatus.READY,
            new_status=OrderStatus.DONE,
            reason="Получение подтверждено заказчиком.",
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("user_stats", args=[user.pk]))
        self.assertEqual(response.context["customer_metrics"][0]["value"], 2)
        self.assertEqual(response.context["customer_metrics"][1]["value"], 1)
        self.assertEqual(response.context["pickup_metrics"][0]["value"], 1)
        self.assertEqual(response.context["pickup_metrics"][1]["value"], 1)
        self.assertEqual(response.context["pickup_metrics"][2]["value"], 1)

        response = self.client.get(reverse("user_stats", args=[user.pk]) + "?period=all")
        self.assertEqual(response.context["customer_metrics"][0]["value"], 3)

    def test_admin_stats_counts_status_audit_and_duty_metrics(self):
        admin = User.objects.create_user("admin", password="StrongPass12345!", is_staff=True)
        replacement_user = User.objects.create_user("replacement", password="StrongPass12345!")
        customer = User.objects.create_user("maker", password="StrongPass12345!")
        order = Order.objects.create(
            user=customer,
            title="Admin work",
            production_type=ProductionType.PRINT_3D,
            material=self.print_material,
            quantity=1,
            photo=image_upload(),
            production_file=upload("admin-work.stl"),
            status=OrderStatus.PENDING,
        )
        for new_status in (OrderStatus.IN_PROGRESS, OrderStatus.READY, OrderStatus.REJECTED):
            OrderStatusLog.objects.create(
                order=order,
                order_number=order.pk,
                order_title=order.title,
                changed_by=admin,
                old_status=OrderStatus.PENDING,
                new_status=new_status,
            )
        AuditLog.objects.create(actor=admin, action="update_order", target_type="Order", target_label="Admin work")
        AuditLog.objects.create(actor=admin, action="bulk_status_update", target_type="Order", target_label="Admin work")
        AuditLog.objects.create(actor=admin, action="export_orders", target_type="Order", target_label="queue.csv")
        AuditLog.objects.create(actor=admin, action="create_material", target_type="Material", target_label="PLA")
        schedule = DutySchedule.objects.create(name="Default")
        duty_person = DutyPerson.objects.create(user=admin, last_name="Admin", first_name="One")
        replacement_person = DutyPerson.objects.create(user=replacement_user, last_name="User", first_name="Two")
        slot = DutySlot.objects.create(
            schedule=schedule,
            weekday=Weekday.MONDAY,
            person=duty_person,
            unload_time=time(10, 0),
        )
        DutySkip.objects.create(
            duty_slot=slot,
            unavailable_date=timezone.localdate(),
            unavailable_time=slot.unload_time,
            reason="Не могу",
            replacement_person=replacement_person,
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("user_stats", args=[admin.pk]) + "?period=all")

        admin_metrics = {row["label"]: row["value"] for row in response.context["admin_metrics"]}
        duty_metrics = {row["label"]: row["value"] for row in response.context["duty_metrics"]}
        self.assertEqual(admin_metrics["Взял в работу"], 1)
        self.assertEqual(admin_metrics["Подготовил к выдаче"], 1)
        self.assertEqual(admin_metrics["Отклонил"], 1)
        self.assertEqual(admin_metrics["Уникальных заказов обработал"], 1)
        self.assertEqual(admin_metrics["Одиночные правки заказов"], 1)
        self.assertEqual(admin_metrics["Массовые смены статусов"], 1)
        self.assertEqual(admin_metrics["CSV-экспорты"], 1)
        self.assertEqual(admin_metrics["Управленческие действия"], 1)
        self.assertEqual(duty_metrics["Пропустил дежурств"], 1)

        response = self.client.get(reverse("user_stats", args=[replacement_user.pk]) + "?period=all")
        duty_metrics = {row["label"]: row["value"] for row in response.context["duty_metrics"]}
        self.assertEqual(duty_metrics["Заменял других"], 1)

# Create your tests here.
