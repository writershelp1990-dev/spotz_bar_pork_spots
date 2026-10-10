from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Schema-only migration for Category hierarchy.

    Data changes (deleting test categories, seeding the tree) live in
    0005_seed_category_hierarchy.py which runs with atomic = False.
    """

    dependencies = [
        ("catalog", "0003_packaging_and_variant_uniqueness"),
    ]

    operations = [
        # Remove unique=True from name
        migrations.AlterField(
            model_name="category",
            name="name",
            field=models.CharField(max_length=100),
        ),
        # Add parent FK
        migrations.AddField(
            model_name="category",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional. Leave blank for a top-level category.",
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="children",
                to="catalog.category",
            ),
        ),
        # Add per-parent uniqueness
        migrations.AddConstraint(
            model_name="category",
            constraint=models.UniqueConstraint(
                fields=["name", "parent"],
                name="unique_category_name_per_parent",
            ),
        ),
    ]