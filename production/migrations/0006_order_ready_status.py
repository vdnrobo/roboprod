from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("production", "0005_limit_order_title_length"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Ожидает"),
                    ("in_progress", "В работе"),
                    ("ready", "Готов к выдаче"),
                    ("done", "Выполнен"),
                    ("cancelled", "Отменено"),
                    ("rejected", "Отклонено"),
                ],
                default="pending",
                max_length=20,
                verbose_name="Статус",
            ),
        ),
        migrations.AlterField(
            model_name="orderstatuslog",
            name="new_status",
            field=models.CharField(
                choices=[
                    ("pending", "Ожидает"),
                    ("in_progress", "В работе"),
                    ("ready", "Готов к выдаче"),
                    ("done", "Выполнен"),
                    ("cancelled", "Отменено"),
                    ("rejected", "Отклонено"),
                ],
                max_length=20,
                verbose_name="Новый статус",
            ),
        ),
        migrations.AlterField(
            model_name="orderstatuslog",
            name="old_status",
            field=models.CharField(
                choices=[
                    ("pending", "Ожидает"),
                    ("in_progress", "В работе"),
                    ("ready", "Готов к выдаче"),
                    ("done", "Выполнен"),
                    ("cancelled", "Отменено"),
                    ("rejected", "Отклонено"),
                ],
                max_length=20,
                verbose_name="Старый статус",
            ),
        ),
    ]
