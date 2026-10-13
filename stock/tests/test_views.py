"""HTTP-level authorization tests for the stock views.

These tests confirm that every stock URL enforces role and operating-point
restrictions server-side, regardless of what the UI shows.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Employee, OperatingPoint, RoleDefinition
from catalog.models import Category, Product, UnitOfMeasure
from stock.models import StockTransfer

User = get_user_model()


class ViewTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.dir_role, _ = RoleDefinition.objects.get_or_create(
            code="DIRECTOR", defaults={"name": "Director"})
        cls.kit_role, _ = RoleDefinition.objects.get_or_create(
            code="KITCHEN_ATTENDANT", defaults={"name": "Kitchen Attendant"})
        cls.but_role, _ = RoleDefinition.objects.get_or_create(
            code="BUTCHERY_ATTENDANT", defaults={"name": "Butchery Attendant"})

        cls.director = User.objects.create_user(
            username="dir_vw", password="pw", role="DIRECTOR",
            role_definition=cls.dir_role, is_active=True, is_active_staff=True)

        cls.butchery, _ = OperatingPoint.objects.get_or_create(
            code="BUTCHERY", defaults={"name": "Butchery", "is_active": True})
        cls.kitchen, _ = OperatingPoint.objects.get_or_create(
            code="KITCHEN", defaults={"name": "Kitchen", "is_active": True})
        cls.choma, _ = OperatingPoint.objects.get_or_create(
            code="CHOMA", defaults={"name": "Choma", "is_active": True})

        cls.kit_user = User.objects.create_user(
            username="kit_vw", password="pw", role="KITCHEN_ATTENDANT",
            role_definition=cls.kit_role, is_active=True, is_active_staff=True)
        Employee.objects.create(
            user=cls.kit_user, operating_point=cls.kitchen,
            employment_status=Employee.EmploymentStatus.ACTIVE)

        cls.but_user = User.objects.create_user(
            username="but_vw", password="pw", role="BUTCHERY_ATTENDANT",
            role_definition=cls.but_role, is_active=True, is_active_staff=True)
        Employee.objects.create(
            user=cls.but_user, operating_point=cls.butchery,
            employment_status=Employee.EmploymentStatus.ACTIVE)

        cls.kg, _ = UnitOfMeasure.objects.get_or_create(
            abbreviation="kg",
            defaults={"name": "Kilogram", "unit_type": "WEIGHT", "is_active": True})
        cls.cat, _ = Category.objects.get_or_create(
            name="Pork", defaults={"is_active": True})
        cls.pork = Product.objects.create(
            name="Raw Pork", code="PORK-VW", category=cls.cat,
            selling_unit=cls.kg, purchase_unit=cls.kg,
            purchase_to_sale_ratio=Decimal("1"),
            product_type="NON_ALCOHOLIC",
            is_active=True, track_inventory=True,
        )

    def make_transfer(self, source=None, destination=None, status=None,
                      requested_by=None):
        src = source or self.butchery
        dst = destination or self.kitchen
        t = StockTransfer.objects.create(
            transfer_number=f"T-{StockTransfer.objects.count()+1:04d}",
            product=self.pork,
            source_point=src, destination_point=dst,
            quantity=Decimal("1.000"), unit=self.kg,
            reason="test",
            requested_by=requested_by or self.but_user,
            status=status or StockTransfer.Status.REQUESTED,
        )
        return t


class AnonymousRedirectTests(ViewTestBase):
    """Every stock URL requires authentication."""

    def test_stock_root_redirects_anonymous(self):
        r = self.client.get("/stock/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])

    def test_transfer_list_redirects_anonymous(self):
        r = self.client.get(reverse("stock:transfer_list"))
        self.assertEqual(r.status_code, 302)

    def test_transfer_request_redirects_anonymous(self):
        r = self.client.get(reverse("stock:transfer_request"))
        self.assertEqual(r.status_code, 302)

    def test_recipe_list_redirects_anonymous(self):
        r = self.client.get(reverse("stock:recipe_list"))
        self.assertEqual(r.status_code, 302)

    def test_batch_list_redirects_anonymous(self):
        r = self.client.get(reverse("stock:batch_list"))
        self.assertEqual(r.status_code, 302)

    def test_wastage_create_redirects_anonymous(self):
        r = self.client.get(reverse("stock:wastage_create"))
        self.assertEqual(r.status_code, 302)

    def test_reconciliation_redirects_anonymous(self):
        r = self.client.get(reverse("stock:reconciliation"))
        self.assertEqual(r.status_code, 302)


class DirectorOnlyViewsTests(ViewTestBase):
    """Non-Directors are rejected on Director-only pages."""

    def setUp(self):
        self.client.force_login(self.kit_user)

    def test_recipe_list_403_for_kitchen(self):
        r = self.client.get(reverse("stock:recipe_list"))
        self.assertEqual(r.status_code, 403)

    def test_recipe_create_get_403_for_kitchen(self):
        r = self.client.get(reverse("stock:recipe_create"))
        self.assertEqual(r.status_code, 403)

    def test_recipe_create_post_403_for_kitchen(self):
        r = self.client.post(reverse("stock:recipe_create"), {
            "product": self.pork.pk,
            "name": "test",
        })
        self.assertEqual(r.status_code, 403)

    def test_reconciliation_403_for_kitchen(self):
        r = self.client.get(reverse("stock:reconciliation"))
        self.assertEqual(r.status_code, 403)

    def test_transfer_cancel_403_for_kitchen(self):
        t = self.make_transfer()
        r = self.client.post(reverse("stock:transfer_cancel", args=[t.pk]),
                             {"reason": "test"})
        self.assertEqual(r.status_code, 403)


class DirectorAccessTests(ViewTestBase):
    """Director sees everything."""

    def setUp(self):
        self.client.force_login(self.director)

    def test_dashboard_200(self):
        r = self.client.get("/stock/")
        self.assertEqual(r.status_code, 200)

    def test_transfer_list_200(self):
        r = self.client.get(reverse("stock:transfer_list"))
        self.assertEqual(r.status_code, 200)

    def test_recipe_list_200(self):
        r = self.client.get(reverse("stock:recipe_list"))
        self.assertEqual(r.status_code, 200)

    def test_recipe_create_get_200(self):
        r = self.client.get(reverse("stock:recipe_create"))
        self.assertEqual(r.status_code, 200)

    def test_reconciliation_200(self):
        r = self.client.get(reverse("stock:reconciliation"))
        self.assertEqual(r.status_code, 200)

    def test_batch_list_200(self):
        r = self.client.get(reverse("stock:batch_list"))
        self.assertEqual(r.status_code, 200)

    def test_wastage_create_200(self):
        r = self.client.get(reverse("stock:wastage_create"))
        self.assertEqual(r.status_code, 200)


class TransferScopingTests(ViewTestBase):
    """Staff see only transfers that touch their point."""

    def test_director_sees_all_transfers(self):
        self.make_transfer(source=self.butchery, destination=self.kitchen)
        self.make_transfer(source=self.choma, destination=self.kitchen)
        self.client.force_login(self.director)
        r = self.client.get(reverse("stock:transfer_list"))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("T-0001", body)
        self.assertIn("T-0002", body)

    def test_kitchen_attendant_sees_only_own_point_transfers(self):
        self.make_transfer(source=self.butchery, destination=self.kitchen)
        self.make_transfer(source=self.butchery, destination=self.choma)
        self.client.force_login(self.kit_user)
        r = self.client.get(reverse("stock:transfer_list"))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("T-0001", body)
        self.assertNotIn("T-0002", body)

    def test_detail_403_for_irrelevant_transfer(self):
        t = self.make_transfer(source=self.butchery, destination=self.choma)
        self.client.force_login(self.kit_user)
        r = self.client.get(reverse("stock:transfer_detail", args=[t.pk]))
        self.assertEqual(r.status_code, 403)

    def test_detail_200_for_own_source_transfer(self):
        t = self.make_transfer(source=self.butchery, destination=self.kitchen)
        self.client.force_login(self.kit_user)
        r = self.client.get(reverse("stock:transfer_detail", args=[t.pk]))
        self.assertEqual(r.status_code, 200)

    def test_detail_200_for_requesting_user(self):
        t = self.make_transfer(
            source=self.butchery, destination=self.choma,
            requested_by=self.kit_user)
        self.client.force_login(self.kit_user)
        r = self.client.get(reverse("stock:transfer_detail", args=[t.pk]))
        self.assertEqual(r.status_code, 200)


class TransferRequestScopingTests(ViewTestBase):
    """Staff can only create a transfer from their own point."""

    def test_kitchen_attendant_cannot_create_from_butchery(self):
        self.client.force_login(self.kit_user)
        r = self.client.post(reverse("stock:transfer_request"), {
            "product": self.pork.pk,
            "source_point": self.butchery.pk,
            "destination_point": self.kitchen.pk,
            "quantity": "1.000",
            "unit": self.kg.pk,
            "transfer_type": StockTransfer.TransferType.ALLOCATION,
            "reason": "should be rejected",
            "reference": "",
        })
        self.assertEqual(StockTransfer.objects.count(), 0)
        self.assertEqual(r.status_code, 200)

    def test_kitchen_attendant_cannot_create_from_choma(self):
        self.client.force_login(self.kit_user)
        r = self.client.post(reverse("stock:transfer_request"), {
            "product": self.pork.pk,
            "source_point": self.choma.pk,
            "destination_point": self.kitchen.pk,
            "quantity": "1.000",
            "unit": self.kg.pk,
            "transfer_type": StockTransfer.TransferType.ALLOCATION,
            "reason": "should be rejected",
            "reference": "",
        })
        self.assertEqual(StockTransfer.objects.count(), 0)

    def test_director_can_create_from_any_point(self):
        self.client.force_login(self.director)
        r = self.client.post(reverse("stock:transfer_request"), {
            "product": self.pork.pk,
            "source_point": self.butchery.pk,
            "destination_point": self.kitchen.pk,
            "quantity": "1.000",
            "unit": self.kg.pk,
            "transfer_type": StockTransfer.TransferType.ALLOCATION,
            "reason": "director-initiated",
            "reference": "",
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(StockTransfer.objects.count(), 1)


class TransferActionEndpointTests(ViewTestBase):
    """The new sender/recipient POST actions and popup API."""

    def test_transfer_accept_endpoint_flips_to_sent(self):
        t = self.make_transfer(
            source=self.butchery, destination=self.kitchen,
            status=StockTransfer.Status.REQUESTED)
        self.client.force_login(self.but_user)
        r = self.client.post(
            reverse("stock:transfer_accept", args=[t.pk]),
            {"quantity_sent": "1.000"})
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.status, StockTransfer.Status.SENT)

    def test_transfer_accept_denied_for_recipient(self):
        t = self.make_transfer(
            source=self.butchery, destination=self.kitchen,
            status=StockTransfer.Status.REQUESTED)
        self.client.force_login(self.kit_user)
        r = self.client.post(
            reverse("stock:transfer_accept", args=[t.pk]),
            {"quantity_sent": "1.000"})
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.status, StockTransfer.Status.REQUESTED)

    def test_transfer_acknowledge_endpoint_completes(self):
        from sales.models import StockBalance
        StockBalance.objects.create(
            product=self.pork, operating_point=self.butchery,
            quantity=Decimal("5.000"))
        t = self.make_transfer(
            source=self.butchery, destination=self.kitchen,
            status=StockTransfer.Status.SENT)
        t.quantity_sent = Decimal("1.000")
        t.save(update_fields=["quantity_sent"])
        self.client.force_login(self.kit_user)
        r = self.client.post(
            reverse("stock:transfer_recipient_acknowledge", args=[t.pk]),
            {"quantity_received": "1.000"})
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.status, StockTransfer.Status.COMPLETED)

    def test_api_pending_transfers_returns_sender_action(self):
        self.make_transfer(
            source=self.butchery, destination=self.kitchen,
            status=StockTransfer.Status.REQUESTED)
        self.client.force_login(self.but_user)
        r = self.client.get(reverse("stock:api_pending_transfers"))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data["actions"]), 1)
        self.assertEqual(data["actions"][0]["kind"], "sender_accept")

    def test_api_pending_transfers_returns_recipient_action(self):
        self.make_transfer(
            source=self.butchery, destination=self.kitchen,
            status=StockTransfer.Status.SENT)
        self.client.force_login(self.kit_user)
        r = self.client.get(reverse("stock:api_pending_transfers"))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data["actions"]), 1)
        self.assertEqual(data["actions"][0]["kind"], "recipient_acknowledge")

class PointDetailViewTests(ViewTestBase):
    """One page per operating point; scoped to staff, open to Director."""

    def test_director_sees_any_point(self):
        self.client.force_login(self.director)
        r = self.client.get(reverse("stock:point_detail", args=["BUTCHERY"]))
        self.assertEqual(r.status_code, 200)

    def test_point_user_sees_own_point(self):
        self.client.force_login(self.but_user)
        r = self.client.get(reverse("stock:point_detail", args=["BUTCHERY"]))
        self.assertEqual(r.status_code, 200)

    def test_point_user_blocked_from_other_point(self):
        self.client.force_login(self.but_user)
        r = self.client.get(reverse("stock:point_detail", args=["KITCHEN"]))
        self.assertEqual(r.status_code, 403)

    def test_anonymous_redirects(self):
        r = self.client.get(reverse("stock:point_detail", args=["BAR"]))
        self.assertEqual(r.status_code, 302)

    def test_invalid_point_returns_404(self):
        self.client.force_login(self.director)
        r = self.client.get(reverse("stock:point_detail", args=["NOPE"]))
        self.assertEqual(r.status_code, 404)

    def test_renders_without_business_day(self):
        self.client.force_login(self.director)
        r = self.client.get(reverse("stock:point_detail", args=["KITCHEN"]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "READY TO START")

    def test_open_business_day_shows_open_pill(self):
        from business.business_day_models import BusinessDay
        from django.utils import timezone
        BusinessDay.objects.create(
            business_date=timezone.now().date(),
            status=BusinessDay.Status.OPEN,
            actual_opened_at=timezone.now(),
        )
        self.client.force_login(self.director)
        r = self.client.get(reverse("stock:point_detail", args=["KITCHEN"]))
        self.assertContains(r, "BUSINESS DAY OPEN")

    def test_low_stock_item_is_listed(self):
        from sales.models import StockAlertRule
        from decimal import Decimal
        StockAlertRule.objects.create(
            product=self.pork, operating_point=self.kitchen,
            minimum_quantity=Decimal("10.000"), target_quantity=Decimal("20.000"),
            is_active=True, created_by=self.director,
        )
        self.client.force_login(self.director)
        r = self.client.get(reverse("stock:point_detail", args=["KITCHEN"]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.pork.name)