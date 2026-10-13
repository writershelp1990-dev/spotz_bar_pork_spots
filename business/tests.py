from django.test import TestCase

# Create your tests here.


from datetime import time as _ad_time
from django.contrib.auth import get_user_model as _ad_get_user_model
from django.test import TestCase as _AdTestCase
from django.utils import timezone as _ad_tz
from accounts.models import Employee as _AdEmployee, OperatingPoint as _AdPoint, RoleDefinition as _AdRole
from business.business_day_models import BusinessDay as _AdDay, StaffDeployment as _AdDeploy
from business.services import auto_deploy_all_for_day as _ad_sweep, auto_deploy_employee as _ad_deploy

_AdUser = _ad_get_user_model()


class _AdBase(_AdTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dir_role, _ = _AdRole.objects.get_or_create(
            code="DIRECTOR", defaults={"name": "Director"})
        cls.but_role, _ = _AdRole.objects.get_or_create(
            code="BUTCHERY_ATTENDANT", defaults={"name": "Butchery Attendant"})
        cls.director = _AdUser.objects.create_user(
            username="dir_ad", password="pw", role="DIRECTOR",
            role_definition=cls.dir_role, is_active=True, is_active_staff=True)
        cls.bar, _ = _AdPoint.objects.get_or_create(
            code="BAR", defaults={"name": "Bar", "is_active": True})
        cls.choma, _ = _AdPoint.objects.get_or_create(
            code="CHOMA", defaults={"name": "Choma", "is_active": True})
        cls.kitchen, _ = _AdPoint.objects.get_or_create(
            code="KITCHEN", defaults={"name": "Kitchen", "is_active": True})

    def make_employee(self, username, point, status=None):
        u = _AdUser.objects.create_user(
            username=username, password="pw", role="BUTCHERY_ATTENDANT",
            role_definition=self.but_role, is_active=True, is_active_staff=True)
        return _AdEmployee.objects.create(
            user=u, operating_point=point,
            employment_status=status or _AdEmployee.EmploymentStatus.ACTIVE)

    def open_day(self):
        return _AdDay.objects.create(
            business_date=_ad_tz.localdate(),
            status=_AdDay.Status.OPEN,
            actual_opened_at=_ad_tz.now(),
            opened_by=self.director,
            scheduled_open_time=_ad_time(8, 0),
            scheduled_close_time=_ad_time(23, 0))


class AutoDeployServiceTests(_AdBase):
    def test_assign_employee_with_open_day_creates_deployment(self):
        day = self.open_day()
        emp = self.make_employee("ad_jane", self.bar)
        dep = _ad_deploy(emp)
        self.assertIsNotNone(dep)
        self.assertEqual(dep.employee, emp)
        self.assertEqual(dep.operating_point, self.bar)
        self.assertEqual(dep.business_day, day)
        self.assertEqual(dep.assigned_by, self.director)

    def test_assign_employee_with_no_open_day_does_nothing(self):
        emp = self.make_employee("ad_bob", self.bar)
        dep = _ad_deploy(emp)
        self.assertIsNone(dep)
        self.assertEqual(_AdDeploy.objects.count(), 0)

    def test_sweep_deploys_all_active_assigned_employees(self):
        self.open_day()
        self.make_employee("ad_alice", self.bar)
        self.make_employee("ad_bob", self.choma)
        self.make_employee("ad_carol", self.kitchen)
        _ad_sweep(_AdDay.objects.get(status="OPEN"))
        self.assertEqual(_AdDeploy.objects.count(), 3)

    def test_existing_director_deployment_not_overridden(self):
        day = self.open_day()
        emp = self.make_employee("ad_jane", self.bar)
        _AdDeploy.objects.create(
            employee=emp, business_day=day, operating_point=self.choma,
            start_time=_ad_time(8, 0), end_time=_ad_time(23, 0),
            assigned_by=self.director, reason="Covering CHOMA")
        _ad_deploy(emp)
        self.assertEqual(_AdDeploy.objects.filter(employee=emp).count(), 2)

    def test_suspended_employee_not_deployed(self):
        self.open_day()
        emp = self.make_employee(
            "ad_sus", self.bar,
            status=_AdEmployee.EmploymentStatus.SUSPENDED)
        self.assertIsNone(_ad_deploy(emp))
        self.assertEqual(_AdDeploy.objects.count(), 0)

    def test_idempotent_sweep(self):
        self.open_day()
        self.make_employee("ad_jane", self.bar)
        day = _AdDay.objects.get(status="OPEN")
        _ad_sweep(day); _ad_sweep(day); _ad_sweep(day)
        self.assertEqual(_AdDeploy.objects.count(), 1)

# ---------------------------------------------------------------------------
# Point-scoped open / close
# ---------------------------------------------------------------------------
from django.core.exceptions import PermissionDenied as _pd_PermDeniedT, ValidationError as _pd_ValErrT
from business.services import open_point as _pd_openT, close_point as _pd_closeT
from business.business_day_models import PointDay as _pd_PointDayT


class PointDayTests(_AdBase):
    """Open/close a point independently; auto-close the global day at the end."""

    def test_attendant_can_open_their_point(self):
        self.client.force_login(self.director)  # simplest actor
        pd = _pd_openT(user=self.director, point=self.bar)
        self.assertIsNotNone(pd)
        self.assertTrue(pd.is_open)
        self.assertEqual(pd.operating_point, self.bar)

    def test_open_creates_global_day_if_missing(self):
        # No day exists yet
        self.assertEqual(_AdDay.objects.count(), 0)
        _pd_openT(user=self.director, point=self.bar)
        self.assertEqual(_AdDay.objects.filter(status="OPEN").count(), 1)
        self.assertEqual(_pd_PointDayT.objects.count(), 1)

    def test_open_point_when_day_exists_uses_that_day(self):
        day = self.open_day()
        _pd_openT(user=self.director, point=self.bar)
        pd = _pd_PointDayT.objects.get(operating_point=self.bar)
        self.assertEqual(pd.business_day, day)

    def test_non_director_can_open_own_point(self):
        emp = self.make_employee("ad_pd_own", self.bar)
        self.open_day()
        pd = _pd_openT(user=emp.user, point=self.bar)
        self.assertIsNotNone(pd)
        self.assertTrue(pd.is_open)

    def test_non_director_cannot_open_other_point(self):
        emp = self.make_employee("ad_pd_other", self.bar)
        self.open_day()
        with self.assertRaises(_pd_PermDeniedT):
            _pd_openT(user=emp.user, point=self.kitchen)

    def test_close_point_sets_closed_at(self):
        self.open_day()
        _pd_openT(user=self.director, point=self.bar)
        _pd_closeT(user=self.director, point=self.bar)
        pd = _pd_PointDayT.objects.get(operating_point=self.bar)
        self.assertIsNotNone(pd.closed_at)
        self.assertEqual(pd.closed_by, self.director)

    def test_last_close_auto_closes_business_day(self):
        self.open_day()
        _pd_openT(user=self.director, point=self.bar)
        _pd_openT(user=self.director, point=self.kitchen)
        _pd_closeT(user=self.director, point=self.bar)
        # day still open
        self.assertEqual(_AdDay.objects.filter(status="OPEN").count(), 1)
        _pd_closeT(user=self.director, point=self.kitchen)
        # now auto-closed
        self.assertEqual(_AdDay.objects.filter(status="OPEN").count(), 0)
        self.assertEqual(_AdDay.objects.filter(status="CLOSED").count(), 1)

    def test_close_does_not_auto_close_when_others_still_open(self):
        self.open_day()
        _pd_openT(user=self.director, point=self.bar)
        _pd_openT(user=self.director, point=self.kitchen)
        _pd_closeT(user=self.director, point=self.bar)
        self.assertEqual(_AdDay.objects.filter(status="OPEN").count(), 1)

    def test_reopen_point_after_close(self):
        self.open_day()
        _pd_openT(user=self.director, point=self.bar)
        _pd_closeT(user=self.director, point=self.bar)

        # Confirm closed state straight from the DB
        pd = _pd_PointDayT.objects.get(operating_point=self.bar)
        self.assertFalse(pd.is_open)
        self.assertIsNotNone(pd.closed_at)

        # Reopen
        _pd_openT(user=self.director, point=self.bar)

        # Re-read from the DB — a new query, not refresh_from_db
        pd = _pd_PointDayT.objects.get(operating_point=self.bar)
        self.assertTrue(pd.is_open)
        self.assertIsNone(pd.closed_at)
        self.assertIsNone(pd.closed_by)
        self.assertEqual(pd.reopened_count, 1)

    def test_idempotent_open(self):
        self.open_day()
        _pd_openT(user=self.director, point=self.bar)
        _pd_openT(user=self.director, point=self.bar)
        _pd_openT(user=self.director, point=self.bar)
        self.assertEqual(_pd_PointDayT.objects.count(), 1)