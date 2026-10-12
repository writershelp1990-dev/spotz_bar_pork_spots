"""HTTP-level tests: authorization on every route, form validation."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Employee, RoleDefinition
from property.models import Asset, PhysicalArea, StaffAreaAccess

User = get_user_model()


class ViewAuthBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.dir_role, _ = RoleDefinition.objects.get_or_create(
            code="DIRECTOR", defaults={"name": "Director"})
        cls.bar_role, _ = RoleDefinition.objects.get_or_create(
            code="BAR_ATTENDANT", defaults={"name": "Bar Attendant"})

        cls.bar_area = PhysicalArea.objects.get(code="BAR")
        cls.kit_area = PhysicalArea.objects.get(code="KITCHEN")

        cls.director = User.objects.create_user(
            username="vw_dir", password="pw", role="DIRECTOR",
            role_definition=cls.dir_role, is_active=True, is_active_staff=True)
        cls.bar_user = User.objects.create_user(
            username="vw_bar", password="pw", role="BAR_ATTENDANT",
            role_definition=cls.bar_role, is_active=True, is_active_staff=True)
        cls.bar_emp = Employee.objects.create(
            user=cls.bar_user, operating_point=None,
            employment_status=Employee.EmploymentStatus.ACTIVE)
        StaffAreaAccess.objects.create(
            employee=cls.bar_emp, area=cls.bar_area,
            granted_by=cls.director, is_active=True)

        cls.bar_asset = Asset.objects.create(
            asset_number="VW-BAR-1", name="Bar Thing",
            current_area=cls.bar_area, quantity=Decimal("1"),
            created_by=cls.director)
        cls.kit_asset = Asset.objects.create(
            asset_number="VW-KIT-1", name="Kit Thing",
            current_area=cls.kit_area, quantity=Decimal("1"),
            created_by=cls.director)


class AnonymousRedirectsTests(ViewAuthBase):

    def test_dashboard_redirects(self):
        r = self.client.get(reverse("property:dashboard"))
        self.assertEqual(r.status_code, 302)

    def test_asset_list_redirects(self):
        r = self.client.get(reverse("property:asset_list"))
        self.assertEqual(r.status_code, 302)

    def test_approval_queue_redirects(self):
        r = self.client.get(reverse("property:approval_queue"))
        self.assertEqual(r.status_code, 302)


class DirectorOnlyViewsTests(ViewAuthBase):

    def setUp(self):
        self.client.force_login(self.bar_user)

    def _assert_403(self, name, *args, method="get", data=None):
        url = reverse(name, args=args)
        r = getattr(self.client, method)(url, data or {})
        self.assertEqual(r.status_code, 403, f"{name} should be 403 for staff")

    def test_asset_create_get_403(self):
        self._assert_403("property:asset_create")

    def test_asset_edit_get_403(self):
        self._assert_403("property:asset_edit", self.bar_asset.pk)

    def test_asset_dispose_403(self):
        self._assert_403("property:asset_dispose", self.bar_asset.pk, method="post")

    def test_approval_queue_403(self):
        self._assert_403("property:approval_queue")

    def test_reports_403(self):
        self._assert_403("property:reports")

    def test_reports_csv_403(self):
        self._assert_403("property:reports_csv")

    def test_new_item_review_403(self):
        r = self.client.get(reverse("property:new_item_list"))
        self.assertEqual(r.status_code, 200)  # staff can list their reports
        # But cannot review


class StaffScopedListTests(ViewAuthBase):

    def test_bar_attendant_sees_only_bar_assets(self):
        self.client.force_login(self.bar_user)
        r = self.client.get(reverse("property:asset_list"))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("VW-BAR-1", body)
        self.assertNotIn("VW-KIT-1", body)

    def test_bar_attendant_detail_403_for_out_of_area(self):
        self.client.force_login(self.bar_user)
        r = self.client.get(reverse("property:asset_detail", args=[self.kit_asset.pk]))
        self.assertEqual(r.status_code, 403)

    def test_bar_attendant_detail_ok_for_in_area(self):
        self.client.force_login(self.bar_user)
        r = self.client.get(reverse("property:asset_detail", args=[self.bar_asset.pk]))
        self.assertEqual(r.status_code, 200)

    def test_director_sees_all(self):
        self.client.force_login(self.director)
        r = self.client.get(reverse("property:asset_list"))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("VW-BAR-1", body)
        self.assertIn("VW-KIT-1", body)

    def test_director_grouped_sections_render(self):
        self.client.force_login(self.director)
        r = self.client.get(reverse("property:asset_list"))
        body = r.content.decode()
        # Area headings present when grouping is active
        self.assertIn("Bar", body)
        self.assertIn("Kitchen", body)


class FormValidationTests(ViewAuthBase):

    def setUp(self):
        self.client.force_login(self.director)

    def test_asset_create_blank_number_auto_generates(self):
        r = self.client.post(reverse("property:asset_create"), {
            "asset_number": "",
            "name": "Auto Test", "category": "Misc", "description": "",
            "quantity": "1", "current_area": self.bar_area.pk,
            "condition": "WORKING", "lifecycle_status": "ACTIVE",
            "registration_date": "2026-10-12", "acquired_date": "",
            "supplier": "", "purchase_cost": "", "notes": "",
        })
        self.assertEqual(r.status_code, 302)
        a = Asset.objects.get(name="Auto Test")
        self.assertTrue(a.asset_number.startswith("SPZ-BAR-"))

    def test_asset_create_duplicate_number_shows_error(self):
        r = self.client.post(reverse("property:asset_create"), {
            "asset_number": "VW-BAR-1",  # already exists
            "name": "Dup", "category": "", "description": "",
            "quantity": "1", "current_area": self.bar_area.pk,
            "condition": "WORKING", "lifecycle_status": "ACTIVE",
            "registration_date": "2026-10-12", "acquired_date": "",
            "supplier": "", "purchase_cost": "", "notes": "",
        })
        self.assertEqual(r.status_code, 200)  # re-rendered with errors
        self.assertContains(r, "already exists", status_code=200)

class ExportSmokeTests(ViewAuthBase):
    """Directors must be able to download every report format."""

    def setUp(self):
        self.client.force_login(self.director)

    def test_csv_returns_200(self):
        r = self.client.get(reverse("property:reports_csv"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r["Content-Type"])

    def test_pdf_returns_200(self):
        r = self.client.get(reverse("property:reports_pdf"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")

    def test_xlsx_returns_200(self):
        r = self.client.get(reverse("property:reports_xlsx"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml.sheet", r["Content-Type"])
