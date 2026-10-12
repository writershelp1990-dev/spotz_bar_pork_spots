from django.db import migrations


AREA_ROWS = [
    ("BAR", "Bar"),
    ("BAR_COUNTER", "Bar Counter"),
    ("BUTCHERY", "Butchery"),
    ("CHOMA", "Choma"),
    ("KITCHEN", "Kitchen"),
    ("DINING_AREA", "Dining Area"),
    ("OUTDOOR_AREA", "Outdoor Area"),
    ("WASHROOMS", "Washrooms"),
    ("GARDEN_COMPOUND", "Garden / Compound"),
    ("OFFICE", "Office"),
    ("STORE", "Store"),
    ("OTHER", "Other"),
]


def seed_areas(apps, schema_editor):
    PhysicalArea = apps.get_model("property", "PhysicalArea")
    for code, name in AREA_ROWS:
        PhysicalArea.objects.get_or_create(
            code=code, defaults={"name": name, "is_active": True},
        )


def unseed_areas(apps, schema_editor):
    PhysicalArea = apps.get_model("property", "PhysicalArea")
    PhysicalArea.objects.filter(code__in=[c for c, _ in AREA_ROWS]).delete()


class Migration(migrations.Migration):
    dependencies = [("property", "0001_initial")]
    operations = [migrations.RunPython(seed_areas, reverse_code=unseed_areas)]