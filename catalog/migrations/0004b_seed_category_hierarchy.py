from django.db import migrations


CATEGORY_TREE = {
    "Spirits": [
        "Vodka", "Whiskey", "Rum", "Tequila", "Gin",
        "Brandy & Cognac", "Liqueurs", "Ready-to-Drink (RTDs)",
    ],
    "Beers": ["Lagers", "Ales", "Stouts", "Ciders"],
    "Wines": ["Red Wine", "White Wine", "Rosé", "Sparkling"],
    "Soft Drinks": ["Sodas", "Waters", "Juices", "Energy Drinks"],
    "Food": ["Snacks", "Meals", "Desserts"],
}


def delete_test_categories(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Category.objects.filter(name__in=["Bar", "tusker"]).delete()


def seed_categories(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    for root_name, children in CATEGORY_TREE.items():
        root, _ = Category.objects.get_or_create(
            name=root_name,
            parent=None,
            defaults={"is_active": True},
        )
        for child_name in children:
            Category.objects.get_or_create(
                name=child_name,
                parent=root,
                defaults={"is_active": True},
            )


def unseed_categories(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    for root_name, children in CATEGORY_TREE.items():
        for child_name in children:
            Category.objects.filter(name=child_name).delete()
        Category.objects.filter(name=root_name, parent=None).delete()


class Migration(migrations.Migration):
    """
    Data-only migration. atomic = False so PostgreSQL allows the sequence
    of deletes and inserts without "pending trigger events".
    """

    atomic = False

    dependencies = [
        ("catalog", "0004_category_hierarchy"),
    ]

    operations = [
        migrations.RunPython(delete_test_categories, migrations.RunPython.noop),
        migrations.RunPython(seed_categories, unseed_categories),
    ]