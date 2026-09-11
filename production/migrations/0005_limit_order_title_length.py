from django.db import migrations, models


def truncate_existing_titles(apps, schema_editor):
    Order = apps.get_model("production", "Order")
    OrderStatusLog = apps.get_model("production", "OrderStatusLog")
    for order in Order.objects.exclude(title__isnull=True):
        if len(order.title) > 30:
            order.title = order.title[:30]
            order.save(update_fields=["title"])
    for log in OrderStatusLog.objects.exclude(order_title__isnull=True):
        if len(log.order_title) > 30:
            log.order_title = log.order_title[:30]
            log.save(update_fields=["order_title"])


class Migration(migrations.Migration):

    dependencies = [
        ("production", "0004_alter_order_material"),
    ]

    operations = [
        migrations.RunPython(truncate_existing_titles, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="order",
            name="title",
            field=models.CharField(max_length=30, verbose_name="Название заказа"),
        ),
        migrations.AlterField(
            model_name="orderstatuslog",
            name="order_title",
            field=models.CharField(max_length=30, verbose_name="Название заказа"),
        ),
    ]
