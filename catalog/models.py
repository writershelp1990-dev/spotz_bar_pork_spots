from django.core.exceptions import ValidationError
from django.db import models


class Category(models.Model):
    name = models.CharField(
        max_length=100,
    )

    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
        help_text="Optional. Leave blank for a top-level category.",
    )

    description = models.TextField(
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Categories"
        constraints = [
            models.UniqueConstraint(
                fields=["name", "parent"],
                name="unique_category_name_per_parent",
            )
        ]

    def __str__(self):
        if self.parent_id:
            return f"{self.parent.name} > {self.name}"
        return self.name

    @property
    def full_path(self):
        parts = [self.name]
        node = self.parent
        while node is not None:
            parts.append(node.name)
            node = node.parent
        return " > ".join(reversed(parts))

    @property
    def is_root(self):
        return self.parent_id is None


class UnitOfMeasure(models.Model):
    class UnitType(models.TextChoices):
        WEIGHT = "WEIGHT", "Weight"
        VOLUME = "VOLUME", "Volume"
        COUNT = "COUNT", "Count"
        PACKAGING = "PACKAGING", "Packaging"

    name = models.CharField(
        max_length=50,
    )

    abbreviation = models.CharField(
        max_length=20,
    )

    unit_type = models.CharField(
        max_length=20,
        choices=UnitType.choices,
    )

    is_active = models.BooleanField(
        default=True,
    )

    class Meta:
        ordering = ["unit_type", "name"]

        constraints = [
            models.UniqueConstraint(
                fields=["name", "abbreviation"],
                name="unique_uom_name_abbreviation",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.abbreviation})"


class Product(models.Model):
    class ProductType(models.TextChoices):
        LIQUOR = "LIQUOR", "Liquor"
        NON_ALCOHOLIC = "NON_ALCOHOLIC", "Non-Alcoholic"
        FOOD = "FOOD", "Food"

    name = models.CharField(
        max_length=150,
    )

    brand = models.CharField(max_length=100, blank=True)
    variety = models.CharField(max_length=100, blank=True)
    packaging = models.CharField(max_length=60, blank=True)
    size_value = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="For example, 500 for a 500 ml product.",
    )
    size_unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="sized_products",
        null=True,
        blank=True,
    )
    code = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
    )

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
    )

    product_type = models.CharField(
        max_length=30,
        choices=ProductType.choices,
    )

    purchase_unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="purchase_products",
    )

    selling_unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="selling_products",
    )

    purchase_to_sale_ratio = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=1,
        help_text=(
            "Number of selling units contained in one purchase unit. "
            "Example: 1 crate = 24 bottles."
        ),
    )

    description = models.TextField(
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    track_inventory = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["brand", "variety", "packaging", "size_value", "size_unit"],
                name="unique_product_variant",
            )
        ]

    def clean(self):
        errors = {}

        if (
            self.purchase_to_sale_ratio is not None
            and self.purchase_to_sale_ratio <= 0
        ):
            errors["purchase_to_sale_ratio"] = (
                "Conversion ratio must be greater than zero."
            )

        if self.size_value is not None and self.size_value <= 0:
            errors["size_value"] = "Product size must be greater than zero."

        if (self.size_value is None) != (self.size_unit_id is None):
            errors["size_value"] = (
                "Enter both the product size and its unit, or leave both blank."
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Preserve explicit legacy/test codes. Generate only when blank.
        if self.code:
            return super().save(*args, **kwargs)

        if self.pk is None:
            super().save(*args, **kwargs)

        number = self.pk
        while True:
            candidate = f"SPZ-{number:06d}"
            exists = type(self).objects.filter(code=candidate).exclude(
                pk=self.pk
            ).exists()
            if not exists:
                break
            number += 1

        self.code = candidate
        # This instance has already been inserted if it was new.
        super().save(update_fields=["code"])

    @property
    def display_name(self):
        """
        Compose a human-readable name from brand/variety/packaging/size.
        Falls back to `name` when variant fields are empty.
        """
        parts = []
        if self.brand:
            parts.append(self.brand)
        if self.variety:
            parts.append(self.variety)
        if self.packaging:
            parts.append(self.packaging)
        if self.size_value is not None and self.size_unit_id:
            # normalize() strips trailing zeros; 'f' format avoids scientific
            # notation (so Decimal("500") renders as "500", not "5e+2").
            size = format(self.size_value.normalize(), "f")
            parts.append(f"{size}{self.size_unit.abbreviation}")
        if parts:
            return " ".join(parts)
        return self.name or f"Product {self.pk}"

    @property
    def size_display(self):
        """Size formatted without trailing zeros, e.g. '500ml' not '500.000ml'."""
        if self.size_value is None or not self.size_unit_id:
            return ""
        size = format(self.size_value.normalize(), "f")
        return f"{size}{self.size_unit.abbreviation}"

    def __str__(self):
        return self.display_name


class ProductPriceHistory(models.Model):
    class PriceType(models.TextChoices):
        PURCHASE = "PURCHASE", "Purchase Price"
        SELLING = "SELLING", "Selling Price"

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="price_history",
    )

    price_type = models.CharField(
        max_length=20,
        choices=PriceType.choices,
    )

    unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="price_history",
    )

    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    effective_from = models.DateTimeField()

    effective_to = models.DateTimeField(
        null=True,
        blank=True,
    )

    notes = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-effective_from", "-id"]

        indexes = [
            models.Index(
                fields=[
                    "product",
                    "price_type",
                    "-effective_from",
                ],
                name="price_product_type_date_idx",
            ),
            models.Index(
                fields=[
                    "product",
                    "effective_from",
                ],
                name="price_product_date_idx",
            ),
        ]

    def clean(self):
        if self.price is not None and self.price < 0:
            raise ValidationError(
                {"price": "Price cannot be negative."}
            )

        if self.effective_to:
            if self.effective_to <= self.effective_from:
                raise ValidationError(
                    {
                        "effective_to":
                        "End time must be after start time."
                    }
                )

    def __str__(self):
        return (
            f"{self.product.name} - "
            f"{self.get_price_type_display()} - "
            f"{self.price}"
        )
