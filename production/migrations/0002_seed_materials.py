from django.db import migrations


def seed_materials(apps, schema_editor):
    Material = apps.get_model("production", "Material")
    defaults = [
        ("PLA", "3d_print"),
        ("PETG", "3d_print"),
        ("ABS", "3d_print"),
        ("Фанера", "laser_cut"),
        ("Акрил", "laser_cut"),
        ("Картон", "laser_cut"),
    ]
    for name, production_type in defaults:
        Material.objects.get_or_create(
            name=name,
            defaults={"production_type": production_type, "is_active": True},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("production", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_materials, migrations.RunPython.noop),
    ]
