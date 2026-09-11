from django.db import migrations


def create_semi_printer_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name="semi_printer")


def remove_semi_printer_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name="semi_printer").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("production", "0022_orderdraft_is_priority_ordermessage"),
    ]

    operations = [
        migrations.RunPython(create_semi_printer_group, remove_semi_printer_group),
    ]
