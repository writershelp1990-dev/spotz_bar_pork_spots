from django.db import migrations, models


STANDARD_UNITS = [
    ("Milliliter", "ml",  "VOLUME"),
    ("Liter",      "L",   "VOLUME"),
    ("Bottle",     "btl", "VOLUME"),
    ("Can",        "can", "VOLUME"),
    ("Glass",      "gls", "VOLUME"),
    ("Shot",       "sht", "VOLUME"),
    ("Piece",      "pc",  "COUNT"),
    ("Crate",      "crt", "PACKAGING"),
    ("Bale",       "bal", "PACKAGING"),
    ("Bundle",     "bdl", "PACKAGING"),
    ("Carton",     "ctn", "PACKAGING"),
    ("Pack",       "pck", "PACKAGING"),
    ("Kilogram",   "kg",  "WEIGHT"),
    ("Gram",       "g",   "WEIGHT"),
]


def seed_units(apps, schema_editor):
    UnitOfMeasure = apps.get_model("catalog", "UnitOfMeasure")
    for name, abbr, unit_type in STANDARD_UNITS:
        UnitOfMeasure.objects.get_or_create(
            name=name,
            abbreviation=abbr,
            defaults={"unit_type": unit_type, "is_active": True},
        )


def unseed_units(apps, schema_editor):
    UnitOfMeasure = apps.get_model("catalog", "UnitOfMeasure")
    for name, abbr, _ in STANDARD_UNITS:
        UnitOfMeasure.objects.filter(name=name, abbreviation=abbr).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_product_brand_product_packaging_product_size_unit_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="unitofmeasure",
            name="unit_type",
            field=models.CharField(
                choices=[
                    ("WEIGHT", "Weight"),
                    ("VOLUME", "Volume"),
                    ("COUNT", "Count"),
                    ("PACKAGING", "Packaging"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(
                fields=["brand", "variety", "packaging", "size_value", "size_unit"],
                name="unique_product_variant",
            ),
        ),
        migrations.RunPython(seed_units, unseed_units),
    ]