"""Model behavior tests for the Property & Assets module."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from property.models import (
    Asset, AssetRelocation, PhysicalArea, PropertyCheck,
    PropertyCheckLine, StaffAreaAccess,
)
from accounts.models import Employee

User = get_user_model()


class ModelTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.dir = User.objects.create_user(
            username="dir_m", password="pw", role="DIRECTOR",
            is_active=True, is_active_staff=True,
        )
        cls.bar_user = User.objects.create_user(
            username="bar_m", password="pw", role="BAR_ATTENDANT",
            is_active=True, is_active_staff=True,
        )
        cls.bar_emp = Employee.objects.create(
            user=cls.bar_user, operating_point=None,
            employment_status=Employee.EmploymentStatus.ACTIVE,
        )
        cls.bar_area = PhysicalArea.objects.get(code="BAR")
        cls.kit_area = PhysicalArea.objects.get(code="KITCHEN")
        cls.dining_area = PhysicalArea.objects.get(code="DINING_AREA")


class PhysicalAreaTests(ModelTestBase):

    def test_code_is_unique(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PhysicalArea.objects.create(code="BAR", name="Duplicate Bar")

    def test_all_twelve_areas_exist(self):
        codes = set(PhysicalArea.objects.values_list("code", flat=True))
        self.assertEqual(codes, {
            "BAR", "BAR_COUNTER", "BUTCHERY", "CHOMA", "KITCHEN",
            "DINING_AREA", "OUTDOOR_AREA", "WASHROOMS", "GARDEN_COMPOUND",
            "OFFICE", "STORE", "OTHER",
        })

    def test_str_returns_name_or_code(self):
        area = PhysicalArea.objects.get(code="BAR")
        self.assertEqual(str(area), "Bar")


class StaffAreaAccessTests(ModelTestBase):

    def test_unique_per_employee_and_area(self):
        StaffAreaAccess.objects.create(
            employee=self.bar_emp, area=self.bar_area, granted_by=self.dir,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StaffAreaAccess.objects.create(
                    employee=self.bar_emp, area=self.bar_area,
                    granted_by=self.dir,
                )


class AssetTests(ModelTestBase):

    def test_asset_number_is_unique(self):
        Asset.objects.create(
            asset_number="AST-1", name="First",
            current_area=self.bar_area, created_by=self.dir,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Asset.objects.create(
                    asset_number="AST-1", name="Second",
                    current_area=self.kit_area, created_by=self.dir,
                )

    def test_is_active_predicate(self):
        a = Asset.objects.create(
            asset_number="AST-2", name="Test",
            current_area=self.bar_area, created_by=self.dir,
            lifecycle_status=Asset.Lifecycle.ACTIVE,
        )
        self.assertTrue(a.is_active)
        a.lifecycle_status = Asset.Lifecycle.DISPOSED
        a.save(update_fields=["lifecycle_status"])
        self.assertFalse(a.is_active)

    def test_default_quantity_is_one(self):
        a = Asset.objects.create(
            asset_number="AST-3", name="Test",
            current_area=self.bar_area, created_by=self.dir,
        )
        self.assertEqual(a.quantity, Decimal("1"))

    def test_str_includes_number_and_name(self):
        a = Asset.objects.create(
            asset_number="AST-4", name="Chair Set",
            current_area=self.bar_area, created_by=self.dir,
        )
        self.assertIn("AST-4", str(a))
        self.assertIn("Chair Set", str(a))


class PropertyCheckTests(ModelTestBase):

    def setUp(self):
        self.asset = Asset.objects.create(
            asset_number="CHK-1", name="Test TV",
            current_area=self.bar_area, created_by=self.dir,
        )
        self.check = PropertyCheck.objects.create(
            area=self.bar_area, checked_by=self.bar_user,
        )

    def test_line_with_no_discrepancy(self):
        line = PropertyCheckLine.objects.create(
            property_check=self.check, asset=self.asset,
            expected_quantity=Decimal("5"), observed_quantity=Decimal("5"),
        )
        self.assertFalse(line.has_discrepancy)

    def test_line_with_discrepancy(self):
        line = PropertyCheckLine.objects.create(
            property_check=self.check, asset=self.asset,
            expected_quantity=Decimal("5"), observed_quantity=Decimal("3"),
        )
        self.assertTrue(line.has_discrepancy)

    def test_multiple_checks_are_kept(self):
        second = PropertyCheck.objects.create(
            area=self.bar_area, checked_by=self.bar_user,
        )
        self.assertEqual(PropertyCheck.objects.filter(area=self.bar_area).count(), 2)
        self.assertNotEqual(self.check.pk, second.pk)


class AssetRelocationStateTests(ModelTestBase):

    def setUp(self):
        self.asset = Asset.objects.create(
            asset_number="REL-1", name="Test Speaker",
            current_area=self.bar_area, created_by=self.dir,
        )
        self.reloc = AssetRelocation.objects.create(
            asset=self.asset, origin_area=self.bar_area,
            destination_area=self.dining_area,
            reason="Customer shade needs a speaker",
            condition_before=Asset.Condition.WORKING,
            receiving_attendant=self.bar_user,
            requested_by=self.bar_user,
        )

    def test_initial_status_is_pending_approval(self):
        self.assertEqual(self.reloc.status, AssetRelocation.Status.PENDING_APPROVAL)
        self.assertTrue(self.reloc.is_pending_director)
        self.assertFalse(self.reloc.is_awaiting_recipient)
        self.assertFalse(self.reloc.is_terminal)

    def test_approved_is_awaiting_recipient(self):
        self.reloc.status = AssetRelocation.Status.APPROVED
        self.reloc.save(update_fields=["status"])
        self.assertFalse(self.reloc.is_pending_director)
        self.assertTrue(self.reloc.is_awaiting_recipient)
        self.assertFalse(self.reloc.is_terminal)

    def test_completed_is_terminal(self):
        self.reloc.status = AssetRelocation.Status.COMPLETED
        self.reloc.save(update_fields=["status"])
        self.assertTrue(self.reloc.is_terminal)

    def test_declined_by_director_is_terminal(self):
        self.reloc.status = AssetRelocation.Status.DECLINED_BY_DIRECTOR
        self.reloc.save(update_fields=["status"])
        self.assertTrue(self.reloc.is_terminal)

    def test_creating_relocation_does_not_change_asset_area(self):
        # The official location must not move until the transfer completes.
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_area, self.bar_area)