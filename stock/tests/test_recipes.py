"""Tests for recipe and production services."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts.models import Employee, OperatingPoint, RoleDefinition
from catalog.models import Category, Product, UnitOfMeasure
from sales.models import StockAlertRule, StockBalance, StockLedger
from stock import recipes as recipe_svc
from stock.models import ProductionBatch, Recipe, RecipeItem

User = get_user_model()


class RecipeTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.dir_role, _ = RoleDefinition.objects.get_or_create(
            code="DIRECTOR", defaults={"name": "Director"})
        cls.kit_role, _ = RoleDefinition.objects.get_or_create(
            code="KITCHEN_ATTENDANT", defaults={"name": "Kitchen Attendant"})
        cls.choma_role, _ = RoleDefinition.objects.get_or_create(
            code="CHOMA_ATTENDANT", defaults={"name": "Choma Attendant"})

        cls.director = User.objects.create_user(
            username="dir_rec", password="pw", role="DIRECTOR",
            role_definition=cls.dir_role, is_active=True, is_active_staff=True)

        cls.kitchen, _ = OperatingPoint.objects.get_or_create(
            code="KITCHEN", defaults={"name": "Kitchen", "is_active": True})
        cls.choma, _ = OperatingPoint.objects.get_or_create(
            code="CHOMA", defaults={"name": "Choma", "is_active": True})

        cls.kit_user = User.objects.create_user(
            username="kit_rec", password="pw", role="KITCHEN_ATTENDANT",
            role_definition=cls.kit_role, is_active=True, is_active_staff=True)
        Employee.objects.create(
            user=cls.kit_user, operating_point=cls.kitchen,
            employment_status=Employee.EmploymentStatus.ACTIVE)

        cls.choma_user = User.objects.create_user(
            username="cho_rec", password="pw", role="CHOMA_ATTENDANT",
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

        def mk(name, code, unit, tracked=True):
            return Product.objects.create(
                name=name, code=code, category=cls.cat,
                selling_unit=unit, purchase_unit=unit,
                purchase_to_sale_ratio=Decimal("1"),
                product_type="NON_ALCOHOLIC",
                is_active=True, track_inventory=tracked,
            )

        cls.pork = mk("Raw Pork", "PORK-RAW", cls.kg)
        cls.ugali = mk("Ugali Portion", "UGALI-PC", cls.pc)
        # Finished product — not tracked, uses a recipe
        cls.stew = mk("Pork Stew Plate", "STEW-PLATE", cls.pc, tracked=False)

    def set_stock(self, product, point, qty):
        b, _ = StockBalance.objects.get_or_create(
            product=product, operating_point=point, defaults={"quantity": 0})
        b.quantity = Decimal(str(qty))
        b.save(update_fields=["quantity", "updated_at"])


class CreateRecipeTests(RecipeTestBase):

    def test_creates_recipe_with_items(self):
        recipe = recipe_svc.create_recipe(
            product=self.stew,
            name="Pork Stew",
            items=[
                {"ingredient": self.pork, "quantity": "0.300", "unit": self.kg},
                {"ingredient": self.ugali, "quantity": "1.000", "unit": self.pc},
            ],
            user=self.director,
        )
        self.assertEqual(recipe.product, self.stew)
        self.assertEqual(recipe.items.count(), 2)
        items = {i.ingredient.code: i.quantity for i in recipe.items.all()}
        self.assertEqual(items["PORK-RAW"], Decimal("0.300"))
        self.assertEqual(items["UGALI-PC"], Decimal("1.000"))

    def test_non_director_rejected(self):
        with self.assertRaises(PermissionDenied):
            recipe_svc.create_recipe(
                product=self.stew, name="x",
                items=[{"ingredient": self.pork, "quantity": "0.300",
                        "unit": self.kg}],
                user=self.kit_user,
            )

    def test_duplicate_recipe_rejected(self):
        recipe_svc.create_recipe(
            product=self.stew, name="Pork Stew",
            items=[{"ingredient": self.pork, "quantity": "0.300",
                    "unit": self.kg}],
            user=self.director,
        )
        with self.assertRaises(ValidationError):
            recipe_svc.create_recipe(
                product=self.stew, name="Dup",
                items=[{"ingredient": self.pork, "quantity": "0.300",
                        "unit": self.kg}],
                user=self.director,
            )

    def test_untracked_ingredient_rejected(self):
        with self.assertRaises(ValidationError):
            recipe_svc.create_recipe(
                product=self.stew, name="x",
                items=[{"ingredient": self.stew, "quantity": "1.000",
                        "unit": self.pc}],
                user=self.director,
            )

    def test_empty_ingredients_rejected(self):
        with self.assertRaises(ValidationError):
            recipe_svc.create_recipe(
                product=self.stew, name="x", items=[], user=self.director,
            )

    def test_zero_quantity_rejected(self):
        with self.assertRaises(ValidationError):
            recipe_svc.create_recipe(
                product=self.stew, name="x",
                items=[{"ingredient": self.pork, "quantity": "0",
                        "unit": self.kg}],
                user=self.director,
            )


class ProductionBatchTests(RecipeTestBase):

    def setUp(self):
        self.recipe = recipe_svc.create_recipe(
            product=self.stew, name="Pork Stew",
            items=[
                {"ingredient": self.pork, "quantity": "0.300", "unit": self.kg},
                {"ingredient": self.ugali, "quantity": "1.000", "unit": self.pc},
            ],
            user=self.director,
        )

    def test_batch_consumes_correct_quantities(self):
        self.set_stock(self.pork, self.kitchen, "10.000")
        self.set_stock(self.ugali, self.kitchen, "50.000")

        batch = recipe_svc.create_production_batch(
            user=self.kit_user, recipe=self.recipe,
            source_point=self.kitchen, preparation_point=self.kitchen,
            quantity_produced="10.000",
        )
        self.assertEqual(batch.status, ProductionBatch.Status.COMPLETED)
        self.assertEqual(batch.quantity_produced, Decimal("10.000"))

        # 10 plates = 3.000 kg pork and 10 ugali
        pork_bal = StockBalance.objects.get(
            product=self.pork, operating_point=self.kitchen)
        ugali_bal = StockBalance.objects.get(
            product=self.ugali, operating_point=self.kitchen)
        self.assertEqual(pork_bal.quantity, Decimal("7.000"))
        self.assertEqual(ugali_bal.quantity, Decimal("40.000"))

        # Two PRODUCTION_OUT ledger rows
        movs = StockLedger.objects.filter(
            reference=batch.batch_number,
            movement_type=StockLedger.MovementType.PRODUCTION_OUT)
        self.assertEqual(movs.count(), 2)

    def test_batch_exceeding_stock_rolls_back(self):
        self.set_stock(self.pork, self.kitchen, "1.000")   # only 1 kg
        self.set_stock(self.ugali, self.kitchen, "50.000")
        with self.assertRaises(ValidationError):
            recipe_svc.create_production_batch(
                user=self.kit_user, recipe=self.recipe,
                source_point=self.kitchen, preparation_point=self.kitchen,
                quantity_produced="10.000",  # needs 3.000 kg
            )
        # Balances unchanged
        pork_bal = StockBalance.objects.get(
            product=self.pork, operating_point=self.kitchen)
        ugali_bal = StockBalance.objects.get(
            product=self.ugali, operating_point=self.kitchen)
        self.assertEqual(pork_bal.quantity, Decimal("1.000"))
        self.assertEqual(ugali_bal.quantity, Decimal("50.000"))
        # No batch created
        self.assertEqual(ProductionBatch.objects.filter(
            recipe=self.recipe).count(), 0)

    def test_batch_fires_low_stock_evaluation(self):
        self.set_stock(self.pork, self.kitchen, "5.000")
        self.set_stock(self.ugali, self.kitchen, "50.000")
        StockAlertRule.objects.create(
            product=self.pork, operating_point=self.kitchen,
            minimum_quantity=Decimal("3.000"), target_quantity=Decimal("8.000"),
            is_active=True, created_by=self.director,
        )
        # 10 plates consume 3 kg -> leaves 2 kg, below the 3 kg minimum
        recipe_svc.create_production_batch(
            user=self.kit_user, recipe=self.recipe,
            source_point=self.kitchen, preparation_point=self.kitchen,
            quantity_produced="10.000",
        )
        from sales.models import StockAlert
        self.assertTrue(StockAlert.objects.filter(
            product=self.pork, operating_point=self.kitchen,
            status=StockAlert.Status.ACTIVE).exists())

    def test_non_deployed_user_rejected(self):
        self.set_stock(self.pork, self.kitchen, "10.000")
        self.set_stock(self.ugali, self.kitchen, "50.000")
        with self.assertRaises(PermissionDenied):
            recipe_svc.create_production_batch(
                user=self.choma_user, recipe=self.recipe,
                source_point=self.kitchen, preparation_point=self.kitchen,
                quantity_produced="1.000",
            )