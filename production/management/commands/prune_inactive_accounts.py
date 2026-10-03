from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from production.models import AccountActivity


class Command(BaseCommand):
    help = "Удаляет обычные аккаунты без активности дольше заданного срока."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=settings.ACCOUNT_INACTIVITY_DAYS,
            help="Срок неактивности в днях. По умолчанию ACCOUNT_INACTIVITY_DAYS.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать, сколько аккаунтов будет удалено, без удаления.",
        )
        parser.add_argument(
            "--include-staff",
            action="store_true",
            help="Также удалять staff/superuser аккаунты. Первый пользователь все равно защищен.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days <= 0:
            raise CommandError("--days должен быть положительным числом.")

        User = get_user_model()
        cutoff = timezone.now() - timedelta(days=days)
        first_user_id = User.objects.order_by("date_joined", "id").values_list("id", flat=True).first()
        stale_user_ids = AccountActivity.objects.filter(last_seen_at__lt=cutoff).values("user_id")
        users = User.objects.filter(id__in=stale_user_ids)
        if first_user_id:
            users = users.exclude(pk=first_user_id)
        if not options["include_staff"]:
            users = users.exclude(is_staff=True).exclude(is_superuser=True)

        user_count = users.count()
        if options["dry_run"]:
            if options["verbosity"] > 0:
                self.stdout.write(
                    self.style.WARNING(
                        f"Будет удалено аккаунтов: {user_count}; cutoff: {cutoff:%Y-%m-%d %H:%M:%S %Z}"
                    )
                )
            return

        usernames = list(users.values_list("username", flat=True))
        with transaction.atomic():
            deleted_count, _ = users.delete()

        if options["verbosity"] > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Удалено аккаунтов: {user_count}; объектов всего: {deleted_count}; "
                    f"cutoff: {cutoff:%Y-%m-%d %H:%M:%S %Z}"
                )
            )
        if usernames and options["verbosity"] > 1:
            self.stdout.write(", ".join(usernames))
