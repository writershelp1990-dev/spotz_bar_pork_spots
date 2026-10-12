"""Security boundary tests for the Property & Assets module.

These tests are the most important in the module: they prove that
access to physical areas, assets, and Director-only actions is
correctly enforced server-side.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase

from accounts.models import Employee, RoleDefinition
from property.models import Asset, PhysicalArea, StaffAreaAccess
from property.permissions import (
    areas_for,
    is_active_staff,
    is_director,
    require_active_staff,
    require_director,
    user_can_access_area,
    user_can_report_on_asset,
    user_can_view_asset,
)

User = get_user_model()


class PermissionTestBase(TestCase):
    """Shared fixtures: one Director, one Bar Attendant, one Kitchen Attendant."""

    @classmethod
    def setUpTestData(cls):
        # Roles come from the DB in this project.
        cls.dir_role, _ = RoleDefinition.objects.get_or_create(
            code="DIRECTOR", defaults={"name": "Director"},
        )
        cls.bar_role, _ = RoleDefinition.objects.get_or_create(
            code="BAR_ATTENDANT", defaults={"name": "Bar Attendant"},
        )
        cls.kit_role, _ = RoleDefinition.objects.get_or_create(
            code="KITCHEN_ATTENDANT", defaults={"name": "Kitchen Attendant"},
        )

        cls.bar_area = PhysicalArea.objects.get(code="BAR")
        cls.kit_area = PhysicalArea.objects.get(code="KITCHEN")
        cls.dining_area = PhysicalArea.objects.get(code="DINING_AREA")

        # Director user
        cls.director = User.objects.create_user(
            username="dir_t", password="pw", email="dir_t@example.com",
            role="DIRECTOR", role_definition=cls.dir_role,
            is_active=True, is_active_staff=True,
        )

        # Bar attendant user + employee
        cls.bar_user = User.objects.create_user(
            username="bar_t", password="pw", email="bar_t@example.com",
            role="BAR_ATTENDANT", role_definition=cls.bar_role,
            is_active=True, is_active_staff=True,
        )
        cls.bar_emp = Employee.objects.create(
            user=cls.bar_user, operating_point=None,
            employment_status=Employee.EmploymentStatus.ACTIVE,
        )

        # Kitchen attendant user + employee
        cls.kit_user = User.objects.create_user(
            username="kit_t", password="pw", email="kit_t@example.com",
            role="KITCHEN_ATTENDANT", role_definition=cls.kit_role,
            is_active=True, is_active_staff=True,
        )
        cls.kit_emp = Employee.objects.create(
            user=cls.kit_user, operating_point=None,
            employment_status=Employee.EmploymentStatus.ACTIVE,
        )

        # Grant Bar attendant access to BAR only
        StaffAreaAccess.objects.create(
            employee=cls.bar_emp, area=cls.bar_area,
            granted_by=cls.director, is_active=True,
        )
        # Grant Kitchen attendant access to KITCHEN only
        StaffAreaAccess.objects.create(
            employee=cls.kit_emp, area=cls.kit_area,
            granted_by=cls.director, is_active=True,
        )

        # Assets in each area
        cls.bar_asset = Asset.objects.create(
            asset_number="TST-BAR-1", name="Test Bar Stool",
            current_area=cls.bar_area, quantity=Decimal("1"),
            created_by=cls.director,
        )
        cls.kit_asset = Asset.objects.create(
            asset_number="TST-KIT-1", name="Test Kitchen TV",
            current_area=cls.kit_area, quantity=Decimal("1"),
            created_by=cls.director,
        )
        cls.dining_asset = Asset.objects.create(
            asset_number="TST-DIN-1", name="Test Dining Table",
            current_area=cls.dining_area, quantity=Decimal("1"),
            created_by=cls.director,
        )


# ---------------------------------------------------------------------------
# is_director / is_active_staff predicates
# ---------------------------------------------------------------------------
class IsDirectorTests(PermissionTestBase):

    def test_director_returns_true(self):
        self.assertTrue(is_director(self.director))

    def test_bar_attendant_returns_false(self):
        self.assertFalse(is_director(self.bar_user))

    def test_anonymous_returns_false(self):
        self.assertFalse(is_director(None))


class IsActiveStaffTests(PermissionTestBase):

    def test_active_staff_returns_true(self):
        self.assertTrue(is_active_staff(self.bar_user))

    def test_inactive_user_returns_false(self):
        inactive = User.objects.create_user(
            username="inactive_t", password="pw",
            is_active=False, is_active_staff=True,
        )
        self.assertFalse(is_active_staff(inactive))

    def test_non_staff_returns_false(self):
        non_staff = User.objects.create_user(
            username="nonstaff_t", password="pw",
            is_active=True, is_active_staff=False,
        )
        self.assertFalse(is_active_staff(non_staff))


# ---------------------------------------------------------------------------
# areas_for
# ---------------------------------------------------------------------------
class AreasForTests(PermissionTestBase):

    def test_director_gets_all_active_areas(self):
        areas = areas_for(self.director)
        self.assertIn("BAR", areas)
        self.assertIn("KITCHEN", areas)
        self.assertIn("DINING_AREA", areas)
        self.assertEqual(len(areas), PhysicalArea.objects.filter(is_active=True).count())

    def test_bar_attendant_only_gets_granted_areas(self):
        areas = areas_for(self.bar_user)
        self.assertEqual(areas, {"BAR"})

    def test_kitchen_attendant_only_gets_granted_areas(self):
        areas = areas_for(self.kit_user)
        self.assertEqual(areas, {"KITCHEN"})

    def test_staff_with_no_grants_gets_empty_set(self):
        new_user = User.objects.create_user(
            username="nogrant_t", password="pw",
            role="BAR_ATTENDANT", is_active=True, is_active_staff=True,
        )
        Employee.objects.create(
            user=new_user, operating_point=None,
            employment_status=Employee.EmploymentStatus.ACTIVE,
        )
        self.assertEqual(areas_for(new_user), set())

    def test_inactive_staff_gets_empty_set(self):
        self.bar_user.is_active = False
        self.bar_user.save(update_fields=["is_active"])
        self.assertEqual(areas_for(self.bar_user), set())

    def test_revoked_grant_is_ignored(self):
        grant = StaffAreaAccess.objects.get(
            employee=self.bar_emp, area=self.bar_area,
        )
        grant.is_active = False
        grant.save(update_fields=["is_active"])
        self.assertEqual(areas_for(self.bar_user), set())


# ---------------------------------------------------------------------------
# user_can_access_area
# ---------------------------------------------------------------------------
class UserCanAccessAreaTests(PermissionTestBase):

    def test_director_can_access_any_area(self):
        self.assertTrue(user_can_access_area(self.director, "KITCHEN"))
        self.assertTrue(user_can_access_area(self.director, "OTHER"))

    def test_bar_attendant_can_access_bar_only(self):
        self.assertTrue(user_can_access_area(self.bar_user, "BAR"))
        self.assertFalse(user_can_access_area(self.bar_user, "KITCHEN"))
        self.assertFalse(user_can_access_area(self.bar_user, "DINING_AREA"))


# ---------------------------------------------------------------------------
# user_can_view_asset
# ---------------------------------------------------------------------------
class UserCanViewAssetTests(PermissionTestBase):

    def test_director_can_view_every_asset(self):
        self.assertTrue(user_can_view_asset(self.director, self.bar_asset))
        self.assertTrue(user_can_view_asset(self.director, self.kit_asset))
        self.assertTrue(user_can_view_asset(self.director, self.dining_asset))

    def test_bar_attendant_can_view_bar_asset(self):
        self.assertTrue(user_can_view_asset(self.bar_user, self.bar_asset))

    def test_bar_attendant_cannot_view_kitchen_asset(self):
        self.assertFalse(user_can_view_asset(self.bar_user, self.kit_asset))

    def test_bar_attendant_cannot_view_ungranted_area(self):
        self.assertFalse(user_can_view_asset(self.bar_user, self.dining_asset))

    def test_inactive_user_cannot_view_any_asset(self):
        self.bar_user.is_active = False
        self.bar_user.save(update_fields=["is_active"])
        self.assertFalse(user_can_view_asset(self.bar_user, self.bar_asset))


# ---------------------------------------------------------------------------
# user_can_report_on_asset
# ---------------------------------------------------------------------------
class UserCanReportOnAssetTests(PermissionTestBase):

    def test_active_staff_can_report_on_in_area_asset(self):
        self.assertTrue(user_can_report_on_asset(self.bar_user, self.bar_asset))

    def test_active_staff_cannot_report_on_out_of_area_asset(self):
        self.assertFalse(user_can_report_on_asset(self.bar_user, self.kit_asset))

    def test_director_can_always_report(self):
        self.assertTrue(user_can_report_on_asset(self.director, self.kit_asset))

    def test_terminated_employee_cannot_report(self):
        self.bar_emp.employment_status = Employee.EmploymentStatus.TERMINATED
        self.bar_emp.save(update_fields=["employment_status"])
        self.assertFalse(user_can_report_on_asset(self.bar_user, self.bar_asset))


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------
class RequireDirectorDecoratorTests(PermissionTestBase):

    def setUp(self):
        self.rf = RequestFactory()

    def test_director_passes(self):
        @require_director
        def view(request):
            return "ok"
        req = self.rf.get("/")
        req.user = self.director
        self.assertEqual(view(req), "ok")

    def test_bar_attendant_is_denied(self):
        @require_director
        def view(request):
            return "ok"
        req = self.rf.get("/")
        req.user = self.bar_user
        with self.assertRaises(PermissionDenied):
            view(req)


class RequireActiveStaffDecoratorTests(PermissionTestBase):

    def setUp(self):
        self.rf = RequestFactory()

    def test_active_staff_passes(self):
        @require_active_staff
        def view(request):
            return "ok"
        req = self.rf.get("/")
        req.user = self.bar_user
        self.assertEqual(view(req), "ok")

    def test_inactive_user_is_denied(self):
        @require_active_staff
        def view(request):
            return "ok"
        self.bar_user.is_active = False
        self.bar_user.save(update_fields=["is_active"])
        req = self.rf.get("/")
        req.user = self.bar_user
        with self.assertRaises(PermissionDenied):
            view(req)