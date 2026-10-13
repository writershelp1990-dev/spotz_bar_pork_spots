"""Tests for stock.services — transfers and wastage."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import Employee, OperatingPoint, RoleDefinition
from catalog.models import Category, Product, UnitOfMeasure
from sales.models import StockAlertRule, StockBalance, StockLedger
from stock import services
from stock.models import StockTransfer

User = get_user_model()


class ServiceTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.dir_role, _ = RoleDefinition.objects.get_or_create(
            code="DIRECTOR", defaults={"name": "Director"})
        cls.but_role, _ = RoleDefinition.objects.get_or_create(
            code="BUTCHERY_ATTENDANT", defaults={"name": "Butchery Attendant"})
        cls.kit_role, _ = RoleDefinition.objects.get_or_create(
            code="KITCHEN_ATTENDANT", defaults={"name": "Kitchen Attendant"})

        cls.director = User.objects.create_user(
            username="dir_svc", password="pw", role="DIRECTOR",
            role_definition=cls.dir_role, is_active=True, is_active_staff=True)

        cls.butchery, _ = OperatingPoint.objects.get_or_create(
            code="BUTCHERY",
            defaults={"name": "Butchery", "is_active": True},
        )
        cls.kitchen, _ = OperatingPoint.objects.get_or_create(
            code="KITCHEN",
            defaults={"name": "Kitchen", "is_active": True},
        )
        cls.choma, _ = OperatingPoint.objects.get_or_create(
            code="CHOMA",
            defaults={"name": "Choma", "is_active": True},
        )

        # Butchery attendant deployed to BUTCHERY
        cls.but_user = User.objects.create_user(
            username="but_svc", password="pw", role="BUTCHERY_ATTENDANT",
            role_definition=cls.but_role, is_active=True, is_active_staff=True)
        cls.but_emp = Employee.objects.create(
            user=cls.but_user, operating_point=cls.butchery,
            employment_status=Employee.EmploymentStatus.ACTIVE)

        # Kitchen attendant deployed to KITCHEN
        cls.kit_user = User.objects.create_user(
            username="kit_svc", password="pw", role="KITCHEN_ATTENDANT",
            role_definition=cls.kit_role, is_active=True, is_active_staff=True)
        cls.kit_emp = Employee.objects.create(
            user=cls.kit_user, operating_point=cls.kitchen,
            employment_status=Employee.EmploymentStatus.ACTIVE)

        # Unit + product
        cls.kg, _ = UnitOfMeasure.objects.get_or_create(
            abbreviation="kg",
            defaults={"name": "Kilogram", "unit_type": "WEIGHT", "is_active": True})
        cls.cat, _ = Category.objects.get_or_create(
            name="Pork", defaults={"is_active": True})
        cls.pork = Product.objects.create(
            name="Raw Pork", code="PORK-RAW", category=cls.cat,
            selling_unit=cls.kg, purchase_unit=cls.kg,
            purchase_to_sale_ratio=Decimal("1"),
            product_type="NON_ALCOHOLIC",
            is_active=True, track_inventory=True,
        )

    def set_stock(self, product, point, qty):
        b, _ = StockBalance.objects.get_or_create(
            product=product, operating_point=point, defaults={"quantity": 0})
        b.quantity = Decimal(str(qty))
        b.save(update_fields=["quantity", "updated_at"])
        return b


class TransferCreateTests(ServiceTestBase):

    def test_creates_pending_transfer(self):
        t = services.create_transfer(
            user=self.but_user, product=self.pork,
            source_point=self.butchery, destination_point=self.kitchen,
            quantity="5.000", reason="Kitchen needs pork",
        )
        self.assertEqual(t.status, StockTransfer.Status.PENDING)
        self.assertEqual(t.product, self.pork)
        self.assertEqual(t.source_point, self.butchery)
        self.assertEqual(t.destination_point, self.kitchen)
        self.assertEqual(t.quantity, Decimal("5.000"))
        self.assertEqual(t.requested_by, self.but_user)

    def test_rejects_same_point(self):
        with self.assertRaises(ValidationError):
            services.create_transfer(
                user=self.but_user, product=self.pork,
                source_point=self.butchery, destination_point=self.butchery,
                quantity="5.000", reason="same point",
            )

    def test_requires_stock_permission_at_source(self):
        with self.assertRaises(PermissionDenied):
            services.create_transfer(
                user=self.kit_user, product=self.pork,
                source_point=self.butchery, destination_point=self.kitchen,
                quantity="5.000", reason="not my source",
            )

    def test_rejects_non_tracked_product(self):
        p = Product.objects.create(
            name="Untracked", code="UNTRK-1", category=self.cat,
            selling_unit=self.kg, purchase_unit=self.kg,
            purchase_to_sale_ratio=Decimal("1"),
            product_type="NON_ALCOHOLIC",
            is_active=True, track_inventory=False,
        )
        with self.assertRaises(ValidationError):
            services.create_transfer(
                user=self.but_user, product=p,
                source_point=self.butchery, destination_point=self.kitchen,
                quantity="5.000", reason="not tracked",
            )


class TransferApproveCompleteTests(ServiceTestBase):

    def _new_transfer(self, qty="5.000"):
        return services.create_transfer(
            user=self.but_user, product=self.pork,
            source_point=self.butchery, destination_point=self.kitchen,
            quantity=qty, reason="test",
        )

    def test_only_director_approves(self):
        t = self._new_transfer()
        with self.assertRaises(PermissionDenied):
            services.approve_transfer(transfer=t, user=self.but_user)

    def test_director_approves(self):
        t = self._new_transfer()
        t2 = services.approve_transfer(transfer=t, user=self.director)
        self.assertEqual(t2.status, StockTransfer.Status.APPROVED)
        self.assertEqual(t2.approved_by, self.director)
        self.assertIsNotNone(t2.approved_at)

    def test_only_director_completes(self):
        t = self._new_transfer()
        services.approve_transfer(transfer=t, user=self.director)
        with self.assertRaises(PermissionDenied):
            services.complete_transfer(transfer=t, user=self.but_user)

    def test_complete_moves_stock_and_writes_two_ledger_rows(self):
        self.set_stock(self.pork, self.butchery, "10.000")
        self.set_stock(self.pork, self.kitchen, "0.000")

        t = self._new_transfer("4.000")
        services.approve_transfer(transfer=t, user=self.director)
        services.complete_transfer(transfer=t, user=self.director)

        self.set_stock(self.pork, self.butchery, "10.000")
        self.set_stock(self.pork, self.kitchen, "0.000")
        # Re-create to test from a clean base
        t2 = self._new_transfer("4.000")
        services.approve_transfer(transfer=t2, user=self.director)
        services.complete_transfer(transfer=t2, user=self.director)

        src = StockBalance.objects.get(product=self.pork, operating_point=self.butchery)
        dst = StockBalance.objects.get(product=self.pork, operating_point=self.kitchen)
        self.assertEqual(src.quantity, Decimal("6.000"))
        self.assertEqual(dst.quantity, Decimal("4.000"))

        out = StockLedger.objects.filter(
            movement_type=StockLedger.MovementType.TRANSFER_OUT,
            reference=t.transfer_number)
        inb = StockLedger.objects.filter(
            movement_type=StockLedger.MovementType.TRANSFER_IN,
            reference=t.transfer_number)
        self.assertEqual(out.count(), 1)
        self.assertEqual(inb.count(), 1)
        self.assertEqual(out.first().quantity_delta, Decimal("-4.000"))
        self.assertEqual(inb.first().quantity_delta, Decimal("4.000"))

    def test_insufficient_source_rejected_and_nothing_written(self):
        self.set_stock(self.pork, self.butchery, "2.000")
        self.set_stock(self.pork, self.kitchen, "0.000")
        t = self._new_transfer("5.000")
        services.approve_transfer(transfer=t, user=self.director)
        with self.assertRaises(ValidationError):
            services.complete_transfer(transfer=t, user=self.director)
        src = StockBalance.objects.get(product=self.pork, operating_point=self.butchery)
        dst = StockBalance.objects.get(product=self.pork, operating_point=self.kitchen)
        self.assertEqual(src.quantity, Decimal("2.000"))
        self.assertEqual(dst.quantity, Decimal("0.000"))

    def test_cannot_complete_twice(self):
        self.set_stock(self.pork, self.butchery, "10.000")
        t = self._new_transfer("3.000")
        services.approve_transfer(transfer=t, user=self.director)
        services.complete_transfer(transfer=t, user=self.director)
        with self.assertRaises(ValidationError):
            services.complete_transfer(transfer=t, user=self.director)

    def test_cancel_requires_reason(self):
        t = self._new_transfer("1.000")
        with self.assertRaises(ValidationError):
            services.cancel_transfer(transfer=t, user=self.director, reason="")

    def test_cancel_pending_does_not_touch_balances(self):
        self.set_stock(self.pork, self.butchery, "10.000")
        t = self._new_transfer("4.000")
        services.cancel_transfer(transfer=t, user=self.director, reason="not needed")
        # The service re-fetches the row under select_for_update, so refresh
        # the local instance before asserting.
        t.refresh_from_db()
        src = StockBalance.objects.get(product=self.pork, operating_point=self.butchery)
        self.assertEqual(src.quantity, Decimal("10.000"))
        self.assertEqual(t.status, StockTransfer.Status.CANCELLED)


class WastageTests(ServiceTestBase):

    def test_reduces_balance_and_writes_wastage_row(self):
        self.set_stock(self.pork, self.butchery, "10.000")
        m = services.record_wastage(
            user=self.but_user, product=self.pork,
            operating_point=self.butchery, quantity="1.500",
            reason="Spoiled", reference="WS-001",
        )
        self.assertEqual(m.movement_type, StockLedger.MovementType.WASTAGE)
        self.assertEqual(m.quantity_delta, Decimal("-1.500"))
        b = StockBalance.objects.get(product=self.pork, operating_point=self.butchery)
        self.assertEqual(b.quantity, Decimal("8.500"))

    def test_rejects_exceeding_stock(self):
        self.set_stock(self.pork, self.butchery, "1.000")
        with self.assertRaises(ValidationError):
            services.record_wastage(
                user=self.but_user, product=self.pork,
                operating_point=self.butchery, quantity="2.000",
                reason="too much",
            )

    def test_requires_stock_permission(self):
        with self.assertRaises(PermissionDenied):
            services.record_wastage(
                user=self.kit_user, product=self.pork,
                operating_point=self.butchery, quantity="1.000",
                reason="not mine",
            )

    def test_requires_reason(self):
        self.set_stock(self.pork, self.butchery, "10.000")
        with self.assertRaises(ValidationError):
            services.record_wastage(
                user=self.but_user, product=self.pork,
                operating_point=self.butchery, quantity="1.000",
                reason="",
            )

    def test_wastage_fires_low_stock_evaluation(self):
        self.set_stock(self.pork, self.butchery, "5.000")
        StockAlertRule.objects.create(
            product=self.pork, operating_point=self.butchery,
            minimum_quantity=Decimal("3.000"), target_quantity=Decimal("8.000"),
            is_active=True, created_by=self.director,
        )
        services.record_wastage(
            user=self.but_user, product=self.pork,
            operating_point=self.butchery, quantity="3.000",
            reason="Crossed threshold",
        )
        from sales.models import StockAlert
        self.assertTrue(
            StockAlert.objects.filter(
                product=self.pork, operating_point=self.butchery,
                status=StockAlert.Status.ACTIVE,
            ).exists()
        )


class LedgerImmutabilityTests(ServiceTestBase):

    def test_ledger_row_cannot_be_resaved(self):
        self.set_stock(self.pork, self.butchery, "5.000")
        m = services.record_wastage(
            user=self.but_user, product=self.pork,
            operating_point=self.butchery, quantity="1.000",
            reason="test",
        )
        m.reason = "changed"
        with self.assertRaises(ValidationError):
            m.save()