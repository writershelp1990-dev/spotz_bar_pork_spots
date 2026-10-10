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

    def test_size_display_strips_trailing_zeros(self):
        from decimal import Decimal
        p = Product(
            name="X", size_value=Decimal("300.000"), size_unit=self.unit_ml,
        )
        self.assertEqual(p.size_display, "300tml")

    def test_size_display_empty_when_no_size(self):
        p = Product(name="Y")
        self.assertEqual(p.size_display, "")

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


class CategoryHierarchyTests(TestCase):
    """Tests for hierarchical Category (parent/child) behavior."""

    def setUp(self):
        self.spirits = Category.objects.create(name="Spirits H", is_active=True)
        self.vodka = Category.objects.create(
            name="Vodka H", parent=self.spirits, is_active=True
        )
        self.whiskey = Category.objects.create(
            name="Whiskey H", parent=self.spirits, is_active=True
        )

    def test_full_path_contains_parent(self):
        self.assertEqual(self.vodka.full_path, "Spirits H > Vodka H")

    def test_top_level_full_path_is_name(self):
        self.assertEqual(self.spirits.full_path, "Spirits H")

    def test_str_shows_full_path_for_child(self):
        self.assertEqual(str(self.vodka), "Spirits H > Vodka H")

    def test_str_is_name_for_root(self):
        self.assertEqual(str(self.spirits), "Spirits H")

    def test_is_root_property(self):
        self.assertTrue(self.spirits.is_root)
        self.assertFalse(self.vodka.is_root)

    def test_cannot_create_two_siblings_with_same_name(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Category.objects.create(
                    name="Vodka H", parent=self.spirits, is_active=True
                )

    def test_can_create_same_name_under_different_parents(self):
        rum = Category.objects.create(name="Rum H", parent=self.spirits, is_active=True)
        cocktail = Category.objects.create(name="Cocktails H", is_active=True)
        rum2 = Category.objects.create(name="Rum H", parent=cocktail, is_active=True)
        self.assertEqual(rum.name, rum2.name)
        self.assertNotEqual(rum.parent_id, rum2.parent_id)

    def test_can_create_top_level_with_same_name_as_child(self):
        top = Category.objects.create(name="Vodka H", parent=None, is_active=True)
        self.assertIsNone(top.parent_id)
