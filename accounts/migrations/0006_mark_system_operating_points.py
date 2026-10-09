from django.db import migrations


def mark_system_operating_points(apps, schema_editor):
    OperatingPoint = apps.get_model("accounts", "OperatingPoint")

    system_codes = [
        "BAR",
        "BUTCHERY",
        "CHOMA",
        "KITCHEN",
    ]

    OperatingPoint.objects.filter(code__in=system_codes).update(
        is_system_point=True
    )


def unmark_system_operating_points(apps, schema_editor):
    OperatingPoint = apps.get_model("accounts", "OperatingPoint")

    system_codes = [
        "BAR",
        "BUTCHERY",
        "CHOMA",
        "KITCHEN",
    ]

    OperatingPoint.objects.filter(code__in=system_codes).update(
        is_system_point=False
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_operatingpoint_is_system_point_and_more"),
    ]

    operations = [
        migrations.RunPython(
            mark_system_operating_points,
            unmark_system_operating_points,
        ),
    ]
