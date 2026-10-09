from catalog.models import Category, Product, UnitOfMeasure
from django.test import TestCase

# Create your tests here.


class PackagingAndVariantTests(TestCase):
    """Tests for PACKAGING unit type, display_name, and variant uniqueness."""

    def setUp(self):
        from decimal import Decimal
        self.cat = Category.objects.create(name="Variant Test Category", is_active=True)
        self.unit_ml = UnitOfMeasure.objects.create(
            name="Test ML", abbreviation="tml", unit_type="VOLUME", is_active=True
        )
        self.unit_btl = UnitOfMeasure.objects.create(
            name="Test Bottle", abbreviation="tbt", unit_type="VOLUME", is_active=True
        )
        self.unit_crt = UnitOfMeasure.objects.create(
            name="Test Crate", abbreviation="tct", unit_type="PACKAGING", is_active=True
        )

    def test_packaging_unit_type_exists(self):
        choices = dict(UnitOfMeasure.UnitType.choices)
        self.assertIn("PACKAGING", choices)

    def test_display_name_composes_all_fields(self):
        from decimal import Decimal
        p = Product(
            name="Coca-Cola", brand="Coca-Cola", variety="Original",
            packaging="Bottle", size_value=Decimal("500"),
            size_unit=self.unit_ml,
        )
        self.assertEqual(p.display_name, "Coca-Cola Original Bottle 500tml")

    def test_display_name_falls_back_to_name_when_empty(self):
        p = Product(name="Plain Product")
        self.assertEqual(p.display_name, "Plain Product")

    def test_display_name_strips_trailing_zeros(self):
        from decimal import Decimal
        p = Product(
            name="X", brand="B", variety="V", packaging="P",
            size_value=Decimal("500.000"), size_unit=self.unit_ml,
        )
        self.assertEqual(p.display_name, "B V P 500tml")

    def test_display_name_preserves_fractional_sizes(self):
        from decimal import Decimal
        p = Product(
            name="X", brand="B", variety="V", packaging="P",
            size_value=Decimal("0.500"), size_unit=self.unit_ml,
        )
        self.assertEqual(p.display_name, "B V P 0.5tml")

    def test_cannot_create_duplicate_product_variant(self):
        from decimal import Decimal
        from django.db import IntegrityError, transaction

        Product.objects.create(
            name="Coke", brand="Coca-Cola", variety="Original",
            packaging="Bottle", size_value=Decimal("500"),
            size_unit=self.unit_ml, category=self.cat, product_type="NON_ALCOHOLIC",
            purchase_unit=self.unit_crt, selling_unit=self.unit_btl,
            purchase_to_sale_ratio=Decimal("24"),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Product.objects.create(
                    name="Coke Duplicate", brand="Coca-Cola", variety="Original",
                    packaging="Bottle", size_value=Decimal("500"),
                    size_unit=self.unit_ml, category=self.cat, product_type="NON_ALCOHOLIC",
                    purchase_unit=self.unit_crt, selling_unit=self.unit_btl,
                    purchase_to_sale_ratio=Decimal("24"),
                )

    def test_can_create_different_sizes_of_same_drink(self):
        from decimal import Decimal
        Product.objects.create(
            name="Coke 500", brand="Coca-Cola", variety="Original",
            packaging="Bottle", size_value=Decimal("500"),
            size_unit=self.unit_ml, category=self.cat, product_type="NON_ALCOHOLIC",
            purchase_unit=self.unit_crt, selling_unit=self.unit_btl,
            purchase_to_sale_ratio=Decimal("24"),
        )
        Product.objects.create(
            name="Coke 1L", brand="Coca-Cola", variety="Original",
            packaging="Bottle", size_value=Decimal("1000"),
            size_unit=self.unit_ml, category=self.cat, product_type="NON_ALCOHOLIC",
            purchase_unit=self.unit_crt, selling_unit=self.unit_btl,
            purchase_to_sale_ratio=Decimal("12"),
        )
        self.assertEqual(Product.objects.count(), 2)

    def test_can_create_different_packaging_of_same_drink(self):
        from decimal import Decimal
        Product.objects.create(
            name="Coke Bottle", brand="Coca-Cola", variety="Original",
            packaging="Bottle", size_value=Decimal("500"),
            size_unit=self.unit_ml, category=self.cat, product_type="NON_ALCOHOLIC",
            purchase_unit=self.unit_crt, selling_unit=self.unit_btl,
            purchase_to_sale_ratio=Decimal("24"),
        )
        Product.objects.create(
            name="Coke Can", brand="Coca-Cola", variety="Original",
            packaging="Can", size_value=Decimal("330"),
            size_unit=self.unit_ml, category=self.cat, product_type="NON_ALCOHOLIC",
            purchase_unit=self.unit_crt, selling_unit=self.unit_btl,
            purchase_to_sale_ratio=Decimal("12"),
        )
        self.assertEqual(Product.objects.count(), 2)
