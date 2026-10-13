"""Integration tests: recipe ingredients are consumed during checkout.

These tests exercise the real sales.services.checkout() path so that any
regression in the interaction between checkout and recipes is caught.
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import Employee, OperatingPoint, RoleDefinition
from business.models import BusinessDay
from catalog.models import Category, Product, ProductPriceHistory, UnitOfMeasure
from sales.models import Sale, SaleItem, StockBalance, StockLedger
from sales.services import checkout
from stock import recipes as recipe_svc

User = get_user_model()


class SalesIntegrationBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.dir_role, _ = RoleDefinition.objects.get_or_create(
            code="DIRECTOR", defaults={"name": "Director"})
        cls.choma_role, _ = RoleDefinition.objects.get_or_create(
            code="CHOMA_ATTENDANT", defaults={"name": "Choma Attendant"})

        cls.director = User.objects.create_user(
            username="dir_si", password="pw", role="DIRECTOR",
            role_definition=cls.dir_role, is_active=True, is_active_staff=True)

        cls.choma, _ = OperatingPoint.objects.get_or_create(
            code="CHOMA", defaults={"name": "Choma", "is_active": True})
        cls.kitchen, _ = OperatingPoint.objects.get_or_create(
            code="KITCHEN", defaults={"name": "Kitchen", "is_active": True})

        # Choma attendant — cashier for the sale
        cls.choma_user = User.objects.create_user(
            username="cho_si", password="pw", role="CHOMA_ATTENDANT",
            role_definition=cls.choma_role, is_active=True, is_active_staff=True)
        Employee.objects.create(
            user=cls.choma_user, operating_point=cls.choma,
            employment_status=Employee.EmploymentStatus.ACTIVE)

        # Units
        cls.kg, _ = UnitOfMeasure.objects.get_or_create(
            abbreviation="kg",
            defaults={"name": "Kilogram", "unit_type": "WEIGHT", "is_active": True})
        cls.pc, _ = UnitOfMeasure.objects.get_or_create(
            abbreviation="pc",
            defaults={"name": "Piece", "unit_type": "COUNT", "is_active": True})

        cls.cat, _ = Category.objects.get_or_create(
            name="Food", defaults={"is_active": True})

        def mk(name, code, unit, tracked=True, price="100.00"):
            p = Product.objects.create(
                name=name, code=code, category=cls.cat,
                selling_unit=unit, purchase_unit=unit,
                purchase_to_sale_ratio=Decimal("1"),
                product_type="NON_ALCOHOLIC",
                is_active=True, track_inventory=tracked,
            )
            ProductPriceHistory.objects.create(
                product=p, price_type="SELLING", unit=unit,
                price=Decimal(price),
                effective_from=__import__("django.utils.timezone",
                                          fromlist=["now"]).now()
                              - __import__("datetime").timedelta(minutes=1),
            )
            return p

        cls.pork = mk("Raw Pork", "PORK-RAW-SI", cls.kg, price="500.00")
        cls.ugali = mk("Ugali Portion", "UGALI-PC-SI", cls.pc, price="50.00")
        # Finished products — made to order
        cls.choma_half = mk("Choma ½kg Plain", "CHOMA-HP-SI", cls.pc,
                            tracked=False, price="400.00")
        cls.choma_half_ug = mk("Choma ½kg + Ugali", "CHOMA-HP-UG-SI", cls.pc,
                               tracked=False, price="450.00")
        # A tracked product to prove no regression
        cls.soda = mk("Soda", "SODA-SI", cls.pc, tracked=True, price="80.00")

    def setUp(self):
        # Ensure an open business day for today
        from django.utils import timezone as _tz
        today = _tz.localdate()
        BusinessDay.objects.filter(status="OPEN").delete()
        BusinessDay.objects.create(
            business_date=today,
            status="OPEN",
            opened_by=self.director,
            actual_opened_at=_tz.now(),
        )

    def set_stock(self, product, point, qty):
        b, _ = StockBalance.objects.get_or_create(
            product=product, operating_point=point,
            defaults={"quantity": Decimal("0")})
        b.quantity = Decimal(str(qty))
        b.save(update_fields=["quantity", "updated_at"])


class CheckoutWithRecipeTests(SalesIntegrationBase):

    def setUp(self):
        super().setUp()
        # Recipe: 1 plate of choma ½kg plain = 0.500 kg raw pork
        self.recipe = recipe_svc.create_recipe(
            product=self.choma_half, name="Choma ½kg",
            items=[{"ingredient": self.pork, "quantity": "0.500",
                    "unit": self.kg}],
            user=self.director,
        )
        # Recipe: 1 plate of choma ½kg + ugali = 0.500 kg pork + 1 ugali
        self.recipe_ug = recipe_svc.create_recipe(
            product=self.choma_half_ug, name="Choma ½kg + Ugali",
            items=[
                {"ingredient": self.pork, "quantity": "0.500", "unit": self.kg},
                {"ingredient": self.ugali, "quantity": "1.000", "unit": self.pc},
            ],
            user=self.director,
        )

    def _checkout(self, product, qty=1, cash="1000.00"):
        items = json.dumps([{"product_id": product.pk, "quantity": qty}])
        return checkout(
            user=self.choma_user, operating_point=self.choma,
            payment_method="CASH", amount_tendered=cash,
            items_json=items,
        )

    def test_single_recipe_sale_deducts_ingredients(self):
        self.set_stock(self.pork, self.choma, "5.000")
        sale = self._checkout(self.choma_half, qty=1, cash="1000.00")

        pork_bal = StockBalance.objects.get(
            product=self.pork, operating_point=self.choma)
        self.assertEqual(pork_bal.quantity, Decimal("4.500"))

        # No StockBalance for the finished product (not tracked)
        self.assertFalse(
            StockBalance.objects.filter(
                product=self.choma_half, operating_point=self.choma).exists()
        )

        # A PRODUCTION_OUT ledger row exists for the sale
        movs = StockLedger.objects.filter(
            sale=sale,
            movement_type=StockLedger.MovementType.PRODUCTION_OUT)
        self.assertEqual(movs.count(), 1)
        self.assertEqual(movs.first().product, self.pork)
        self.assertEqual(movs.first().quantity_delta, Decimal("-0.500"))

    def test_two_unit_sale_deducts_double(self):
        self.set_stock(self.pork, self.choma, "5.000")
        self._checkout(self.choma_half, qty=2, cash="2000.00")
        pork_bal = StockBalance.objects.get(
            product=self.pork, operating_point=self.choma)
        self.assertEqual(pork_bal.quantity, Decimal("4.000"))

    def test_recipe_with_multiple_ingredients(self):
        self.set_stock(self.pork, self.choma, "5.000")
        self.set_stock(self.ugali, self.choma, "20.000")
        self._checkout(self.choma_half_ug, qty=1, cash="1000.00")

        pork_bal = StockBalance.objects.get(
            product=self.pork, operating_point=self.choma)
        ugali_bal = StockBalance.objects.get(
            product=self.ugali, operating_point=self.choma)
        self.assertEqual(pork_bal.quantity, Decimal("4.500"))
        self.assertEqual(ugali_bal.quantity, Decimal("19.000"))

    def test_insufficient_ingredient_fails_entire_sale(self):
        self.set_stock(self.pork, self.choma, "0.100")   # less than 0.500 needed
        with self.assertRaises(ValidationError):
            self._checkout(self.choma_half, qty=1, cash="1000.00")
        # No sale created, no ledger row, balance unchanged
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(SaleItem.objects.count(), 0)
        self.assertEqual(
            StockLedger.objects.filter(
                movement_type=StockLedger.MovementType.PRODUCTION_OUT).count(),
            0)
        pork_bal = StockBalance.objects.get(
            product=self.pork, operating_point=self.choma)
        self.assertEqual(pork_bal.quantity, Decimal("0.100"))

    def test_tracked_product_still_deducted_normally(self):
        """Regression: a tracked product still goes through the existing path."""
        self.set_stock(self.soda, self.choma, "10.000")
        sale = self._checkout(self.soda, qty=2, cash="1000.00")
        soda_bal = StockBalance.objects.get(
            product=self.soda, operating_point=self.choma)
        self.assertEqual(soda_bal.quantity, Decimal("8.000"))

        movs = StockLedger.objects.filter(
            sale=sale, movement_type=StockLedger.MovementType.SALE)
        self.assertEqual(movs.count(), 1)
        self.assertEqual(movs.first().product, self.soda)
        self.assertEqual(movs.first().quantity_delta, Decimal("-2.000"))

    def test_no_recipe_no_ingredient_deduction(self):
        """A non-recipe, non-tracked product writes no PRODUCTION_OUT rows."""
        # Create an ad-hoc non-tracked product with no recipe
        untracked = Product.objects.create(
            name="Ad-hoc", code="ADHOC-SI", category=self.cat,
            selling_unit=self.pc, purchase_unit=self.pc,
            purchase_to_sale_ratio=Decimal("1"),
            product_type="NON_ALCOHOLIC",
            is_active=True, track_inventory=False,
        )
        ProductPriceHistory.objects.create(
            product=untracked, price_type="SELLING", unit=self.pc,
            price=Decimal("10.00"),
            effective_from=__import__("django.utils.timezone",
                                      fromlist=["now"]).now()
                          - __import__("datetime").timedelta(minutes=1),
        )
        sale = self._checkout(untracked, qty=1, cash="100.00")
        movs = StockLedger.objects.filter(
            sale=sale,
            movement_type=StockLedger.MovementType.PRODUCTION_OUT)
        self.assertEqual(movs.count(), 0)


class DuplicateProtectionTests(SalesIntegrationBase):

    def setUp(self):
        super().setUp()
        self.recipe = recipe_svc.create_recipe(
            product=self.choma_half, name="Choma ½kg",
            items=[{"ingredient": self.pork, "quantity": "0.500",
                    "unit": self.kg}],
            user=self.director,
        )

    def test_second_consume_call_is_noop(self):
        self.set_stock(self.pork, self.choma, "5.000")
        items = json.dumps([{"product_id": self.choma_half.pk, "quantity": 1}])
        sale = checkout(
            user=self.choma_user, operating_point=self.choma,
            payment_method="CASH", amount_tendered="1000.00",
            items_json=items,
        )
        # Balance after checkout: 4.500
        pork_bal = StockBalance.objects.get(
            product=self.pork, operating_point=self.choma)
        self.assertEqual(pork_bal.quantity, Decimal("4.500"))

        # Manually call consume_recipe_ingredients again with the same sale
        from stock.recipes import consume_recipe_ingredients
        movements = consume_recipe_ingredients(
            sale=sale, product=self.choma_half, quantity=1,
            operating_point=self.choma, user=self.choma_user,
        )
        self.assertEqual(movements, [])
        # Balance unchanged
        pork_bal.refresh_from_db()
        self.assertEqual(pork_bal.quantity, Decimal("4.500"))