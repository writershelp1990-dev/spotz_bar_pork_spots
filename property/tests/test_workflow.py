"""Service-layer workflow tests: state machine, transactions, invariants."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import Employee, RoleDefinition
from property import services
from property.models import (
    Asset, AssetNewItemReport, AssetRelocation, ConditionReport,
    PhysicalArea, StaffAreaAccess,
)

User = get_user_model()


class WorkflowBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.dir_role, _ = RoleDefinition.objects.get_or_create(
            code="DIRECTOR", defaults={"name": "Director"})
        cls.bar_role, _ = RoleDefinition.objects.get_or_create(
            code="BAR_ATTENDANT", defaults={"name": "Bar Attendant"})

        cls.bar_area = PhysicalArea.objects.get(code="BAR")
        cls.kit_area = PhysicalArea.objects.get(code="KITCHEN")
        cls.din_area = PhysicalArea.objects.get(code="DINING_AREA")

        cls.director = User.objects.create_user(
            username="wf_dir", password="pw", role="DIRECTOR",
            role_definition=cls.dir_role, is_active=True, is_active_staff=True)
        cls.bar_user = User.objects.create_user(
            username="wf_bar", password="pw", role="BAR_ATTENDANT",
            role_definition=cls.bar_role, is_active=True, is_active_staff=True)
        cls.bar_emp = Employee.objects.create(
            user=cls.bar_user, operating_point=None,
            employment_status=Employee.EmploymentStatus.ACTIVE)

        StaffAreaAccess.objects.create(
            employee=cls.bar_emp, area=cls.bar_area,
            granted_by=cls.director, is_active=True)

        # Kitchen attendant - used as a valid recipient for Kitchen destinations
        # under Rule A. The Bar attendant is NOT authorized for Kitchen, so a
        # Bar -> Kitchen move must designate a Kitchen-authorized recipient.
        cls.kit_role, _ = RoleDefinition.objects.get_or_create(
            code="KITCHEN_ATTENDANT", defaults={"name": "Kitchen Attendant"})
        cls.kit_user = User.objects.create_user(
            username="wf_kit", password="pw",
            role="KITCHEN_ATTENDANT", role_definition=cls.kit_role,
            is_active=True, is_active_staff=True)
        cls.kit_emp = Employee.objects.create(
            user=cls.kit_user, operating_point=None,
            employment_status=Employee.EmploymentStatus.ACTIVE)
        StaffAreaAccess.objects.create(
            employee=cls.kit_emp, area=cls.kit_area,
            granted_by=cls.director, is_active=True)

    def make_asset(self, *, area=None, number=None, user=None):
        return Asset.objects.create(
            asset_number=number or f"WF-{Asset.objects.count()+1:04d}",
            name="Test Asset", current_area=area or self.bar_area,
            quantity=Decimal("1"), created_by=user or self.director)


# ---------------------------------------------------------------------------
# Auto-numbering
# ---------------------------------------------------------------------------
class AutoNumberingTests(WorkflowBase):

    def test_create_asset_auto_generates_number(self):
        a = services.create_asset(user=self.director, data={
            "asset_number": "", "name": "Auto", "category": "",
            "description": "", "quantity": Decimal("1"),
            "current_area": self.bar_area,
            "condition": Asset.Condition.WORKING,
            "lifecycle_status": Asset.Lifecycle.ACTIVE,
            "registration_date": timezone.localdate(),
            "acquired_date": None, "supplier": "",
            "purchase_cost": None, "notes": "",
        })
        self.assertTrue(a.asset_number.startswith("SPZ-BAR-"))
        self.assertEqual(a.asset_number, "SPZ-BAR-0001")

    def test_create_asset_second_number_increments(self):
        for _ in range(2):
            services.create_asset(user=self.director, data={
                "asset_number": "", "name": "Auto", "category": "",
                "description": "", "quantity": Decimal("1"),
                "current_area": self.bar_area,
                "condition": Asset.Condition.WORKING,
                "lifecycle_status": Asset.Lifecycle.ACTIVE,
                "registration_date": timezone.localdate(),
                "acquired_date": None, "supplier": "",
                "purchase_cost": None, "notes": "",
            })
        nums = sorted(Asset.objects.filter(
            asset_number__startswith="SPZ-BAR-").values_list("asset_number", flat=True))
        self.assertEqual(nums, ["SPZ-BAR-0001", "SPZ-BAR-0002"])

    def test_create_asset_override_is_preserved(self):
        a = services.create_asset(user=self.director, data={
            "asset_number": "CUSTOM-99", "name": "Override", "category": "",
            "description": "", "quantity": Decimal("1"),
            "current_area": self.bar_area,
            "condition": Asset.Condition.WORKING,
            "lifecycle_status": Asset.Lifecycle.ACTIVE,
            "registration_date": timezone.localdate(),
            "acquired_date": None, "supplier": "",
            "purchase_cost": None, "notes": "",
        })
        self.assertEqual(a.asset_number, "CUSTOM-99")


# ---------------------------------------------------------------------------
# New-item report approval
# ---------------------------------------------------------------------------
class NewItemReportWorkflowTests(WorkflowBase):

    def setUp(self):
        self.report = AssetNewItemReport.objects.create(
            proposed_name="New TV", proposed_category="Electronics",
            area=self.bar_area, quantity=Decimal("1"),
            condition=Asset.Condition.WORKING,
            reported_by=self.bar_user)

    def test_approve_creates_one_asset_and_links(self):
        asset = services.approve_new_item_report(
            user=self.director, report=self.report, review_notes="ok")
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, AssetNewItemReport.Status.APPROVED)
        self.assertEqual(self.report.resulting_asset_id, asset.pk)
        self.assertTrue(asset.asset_number.startswith("SPZ-BAR-"))

    def test_approve_twice_raises(self):
        services.approve_new_item_report(user=self.director, report=self.report)
        with self.assertRaises(ValidationError):
            services.approve_new_item_report(user=self.director, report=self.report)

    def test_director_cannot_approve_own_report(self):
        self.report.reported_by = self.director
        self.report.save(update_fields=["reported_by"])
        with self.assertRaises(PermissionDenied):
            services.approve_new_item_report(user=self.director, report=self.report)

    def test_non_director_cannot_approve(self):
        with self.assertRaises(PermissionDenied):
            services.approve_new_item_report(user=self.bar_user, report=self.report)

    def test_reject_requires_reason(self):
        with self.assertRaises(ValidationError):
            services.reject_new_item_report(
                user=self.director, report=self.report, review_notes="")

    def test_reject_preserves_report(self):
        services.reject_new_item_report(
            user=self.director, report=self.report, review_notes="not ours")
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, AssetNewItemReport.Status.REJECTED)
        self.assertEqual(self.report.review_notes, "not ours")


# ---------------------------------------------------------------------------
# Condition reports
# ---------------------------------------------------------------------------
class ConditionReportWorkflowTests(WorkflowBase):

    def setUp(self):
        self.asset = self.make_asset()

    def test_staff_submits_report(self):
        r = services.submit_condition_report(
            user=self.bar_user, asset=self.asset,
            data={"condition_reported": ConditionReport.ConditionReported.DAMAGED,
                  "quantity_affected": Decimal("1"), "notes": ""})
        self.assertEqual(r.status, ConditionReport.Status.OPEN)

    def test_out_of_area_staff_cannot_report(self):
        kit_asset = self.make_asset(area=self.kit_area, number="WF-K-1")
        with self.assertRaises(PermissionDenied):
            services.submit_condition_report(
                user=self.bar_user, asset=kit_asset,
                data={"condition_reported": ConditionReport.ConditionReported.DAMAGED,
                      "quantity_affected": Decimal("1"), "notes": ""})

    def test_staff_cannot_resolve(self):
        r = services.submit_condition_report(
            user=self.bar_user, asset=self.asset,
            data={"condition_reported": ConditionReport.ConditionReported.DAMAGED,
                  "quantity_affected": Decimal("1"), "notes": ""})
        with self.assertRaises(PermissionDenied):
            services.resolve_condition_report(
                user=self.bar_user, report=r, resolution_notes="fixed")

    def test_director_resolves(self):
        r = services.submit_condition_report(
            user=self.bar_user, asset=self.asset,
            data={"condition_reported": ConditionReport.ConditionReported.DAMAGED,
                  "quantity_affected": Decimal("1"), "notes": ""})
        services.resolve_condition_report(
            user=self.director, report=r, resolution_notes="fixed")
        r.refresh_from_db()
        self.assertEqual(r.status, ConditionReport.Status.RESOLVED)


# ---------------------------------------------------------------------------
# Relocation — the critical state machine
# ---------------------------------------------------------------------------
class RelocationWorkflowTests(WorkflowBase):

    def setUp(self):
        self.asset = self.make_asset()

    def _request(self, user=None, dest=None, recipient=None):
        """Submit a relocation request.

        Under Rule A the receiving attendant must be authorized for the
        destination area. Default destination is KITCHEN, so the default
        recipient is the Kitchen attendant.
        """
        dest_area = dest or self.kit_area
        if recipient is not None:
            rec = recipient
        elif dest_area.code == "KITCHEN":
            rec = self.kit_user
        elif dest_area.code == "BAR":
            rec = self.bar_user
        else:
            rec = self.kit_user
        return services.create_relocation_request(
            user=user or self.bar_user, asset=self.asset, data={
                "destination_area": dest_area,
                "reason": "test move",
                "condition_before": Asset.Condition.WORKING,
                "receiving_attendant": rec,
                "expected_transfer_date": None, "notes": "",
            })

    def test_request_does_not_move_asset(self):
        self._request()
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_area, self.bar_area)

    def test_request_requires_area_access(self):
        with self.assertRaises(PermissionDenied):
            services.create_relocation_request(
                user=self.bar_user, asset=self.make_asset(area=self.kit_area, number="WF-K-2"),
                data={"destination_area": self.bar_area, "reason": "x",
                      "condition_before": Asset.Condition.WORKING,
                      "receiving_attendant": self.bar_user,
                      "expected_transfer_date": None, "notes": ""})

    def test_director_approval_does_not_move_asset(self):
        r = self._request()
        services.director_decide_relocation(
            user=self.director, relocation=r, action="approve")
        self.asset.refresh_from_db()
        r.refresh_from_db()
        self.assertEqual(r.status, AssetRelocation.Status.APPROVED)
        self.assertEqual(self.asset.current_area, self.bar_area)

    def test_decline_requires_reason(self):
        r = self._request()
        with self.assertRaises(ValidationError):
            services.director_decide_relocation(
                user=self.director, relocation=r, action="decline", notes="")

    def test_non_director_cannot_approve(self):
        r = self._request()
        with self.assertRaises(PermissionDenied):
            services.director_decide_relocation(
                user=self.bar_user, relocation=r, action="approve")

    def test_recipient_accept_does_not_move_asset(self):
        r = self._request()
        services.director_decide_relocation(
            user=self.director, relocation=r, action="approve")
        services.recipient_decide(user=self.kit_user, relocation=r, action="accept")
        self.asset.refresh_from_db()
        r.refresh_from_db()
        self.assertEqual(r.recipient_decision, "ACCEPTED")
        self.assertEqual(r.status, AssetRelocation.Status.APPROVED)
        self.assertEqual(self.asset.current_area, self.bar_area)

    def test_recipient_decline_creates_exception(self):
        r = self._request()
        services.director_decide_relocation(
            user=self.director, relocation=r, action="approve")
        services.recipient_decide(user=self.kit_user, relocation=r, action="decline", decline_reason="not on duty")
        r.refresh_from_db()
        self.assertEqual(r.status, AssetRelocation.Status.TRANSFER_EXCEPTION)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_area, self.bar_area)

    def test_confirm_receipt_moves_asset(self):
        r = self._request()
        services.director_decide_relocation(
            user=self.director, relocation=r, action="approve")
        services.recipient_decide(user=self.kit_user, relocation=r, action="accept")
        services.confirm_receipt(user=self.kit_user, relocation=r, condition_at_receipt=Asset.Condition.WORKING, receipt_notes="ok")
        self.asset.refresh_from_db()
        r.refresh_from_db()
        self.assertEqual(self.asset.current_area, self.kit_area)
        self.assertEqual(r.status, AssetRelocation.Status.COMPLETED)

    def test_second_receipt_raises(self):
        r = self._request()
        services.director_decide_relocation(
            user=self.director, relocation=r, action="approve")
        services.recipient_decide(user=self.kit_user, relocation=r, action="accept")
        services.confirm_receipt(user=self.kit_user, relocation=r, condition_at_receipt=Asset.Condition.WORKING, receipt_notes="")
        with self.assertRaises(ValidationError):
            services.confirm_receipt(user=self.kit_user, relocation=r, condition_at_receipt=Asset.Condition.WORKING, receipt_notes="")

    def test_recipient_cannot_be_someone_else(self):
        r = self._request()
        services.director_decide_relocation(
            user=self.director, relocation=r, action="approve")
        other = User.objects.create_user(
            username="wf_other", password="pw", role="BAR_ATTENDANT",
            is_active=True, is_active_staff=True)
        with self.assertRaises(PermissionDenied):
            services.recipient_decide(
                user=other, relocation=r, action="accept")

    def test_destination_must_differ_from_current(self):
        with self.assertRaises(ValidationError):
            services.create_relocation_request(
                user=self.bar_user, asset=self.asset,
                data={"destination_area": self.bar_area, "reason": "x",
                      "condition_before": Asset.Condition.WORKING,
                      "receiving_attendant": self.bar_user,
                      "expected_transfer_date": None, "notes": ""})

class RuleARelocationTests(WorkflowBase):
    """Rule A: a non-Director can propose any destination for an asset they
    can see, as long as the designated recipient is authorized there."""

    def setUp(self):
        # The Bar user can see this asset (BAR is their granted area)
        self.asset = self.make_asset()
        # Create a Kitchen attendant who is NOT authorized at Bar
        from accounts.models import User as _U
        self.kit_role, _ = RoleDefinition.objects.get_or_create(
            code="KITCHEN_ATTENDANT", defaults={"name": "Kitchen Attendant"})
        self.kit_user = _U.objects.create_user(
            username="wf_kit_rule_a", password="pw",
            role="KITCHEN_ATTENDANT", role_definition=self.kit_role,
            is_active=True, is_active_staff=True)
        self.kit_emp = Employee.objects.create(
            user=self.kit_user, operating_point=None,
            employment_status=Employee.EmploymentStatus.ACTIVE)
        StaffAreaAccess.objects.create(
            employee=self.kit_emp, area=self.kit_area,
            granted_by=self.director, is_active=True)

    def test_bar_attendant_can_request_kitchen_destination_with_kitchen_recipient(self):
        """Bar attendant proposes a Kitchen move; Kitchen attendant receives."""
        relocation = services.create_relocation_request(
            user=self.bar_user, asset=self.asset, data={
                "destination_area": self.kit_area,
                "reason": "Move to kitchen",
                "condition_before": Asset.Condition.WORKING,
                "receiving_attendant": self.kit_user,
                "expected_transfer_date": None, "notes": "",
            })
        self.assertEqual(relocation.status, AssetRelocation.Status.PENDING_APPROVAL)
        self.assertEqual(relocation.destination_area, self.kit_area)
        self.assertEqual(relocation.receiving_attendant, self.kit_user)

    def test_bar_attendant_cannot_designate_unauthorized_recipient(self):
        """Bar attendant cannot designate themselves for a Kitchen move
        (the Bar attendant is not authorized at Kitchen)."""
        with self.assertRaises(ValidationError):
            services.create_relocation_request(
                user=self.bar_user, asset=self.asset, data={
                    "destination_area": self.kit_area,
                    "reason": "Self as recipient — should fail",
                    "condition_before": Asset.Condition.WORKING,
                    "receiving_attendant": self.bar_user,
                    "expected_transfer_date": None, "notes": "",
                })

    def test_director_can_designate_self_for_any_destination(self):
        """Director is authorized everywhere, so they can receive anywhere."""
        asset = self.make_asset(area=self.bar_area, number="WF-DIR-1")
        relocation = services.create_relocation_request(
            user=self.director, asset=asset, data={
                "destination_area": self.kit_area,
                "reason": "Director receives",
                "condition_before": Asset.Condition.WORKING,
                "receiving_attendant": self.director,
                "expected_transfer_date": None, "notes": "",
            })
        self.assertEqual(relocation.receiving_attendant, self.director)
