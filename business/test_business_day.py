from datetime import date, time

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Employee, OperatingPoint, User
from audit.models import AuditLog

from .business_day_models import BusinessDay, BusinessHoliday, StaffDeployment
from .business_day_services import (
    assert_business_day_open,
    create_staff_deployment,
    get_scheduled_business_day_times,
    open_business_day,
    close_business_day,
    reopen_business_day,
)


@override_settings(USE_TZ=True)
class BusinessDayTests(TestCase):
    def setUp(self):
        self.director = User.objects.create_user(
            username="director_business_day",
            password="TestPassword123!",
            role=User.Role.DIRECTOR,
            is_active=True,
            is_active_staff=True,
        )

        self.staff = User.objects.create_user(
            username="staff_business_day",
            password="TestPassword123!",
            role=User.Role.BAR_ATTENDANT,
            is_active=True,
            is_active_staff=True,
        )

        self.point = OperatingPoint.objects.create(
            code="BDTEST",
            name="Business Day Test",
            is_active=True,
        )

        self.employee = Employee.objects.create(
            user=self.staff,
            operating_point=self.point,
            employment_status=Employee.EmploymentStatus.ACTIVE,
        )

    def test_weekday_schedule(self):
        result = get_scheduled_business_day_times(date(2026, 10, 5))
        self.assertEqual(result["business_open"], time(8, 0))
        self.assertEqual(result["bar_open"], time(16, 0))
        self.assertEqual(result["close"], time(23, 0))

    def test_weekend_schedule(self):
        result = get_scheduled_business_day_times(date(2026, 10, 10))
        self.assertEqual(result["bar_open"], time(14, 0))

    def test_holiday_schedule(self):
        BusinessHoliday.objects.create(
            date=date(2026, 10, 5),
            name="Test Holiday",
        )
        result = get_scheduled_business_day_times(date(2026, 10, 5))
        self.assertEqual(result["bar_open"], time(14, 0))

    def test_director_can_open_business_day(self):
        business_day = open_business_day(
            user=self.director,
            business_date=date(2026, 10, 5),
        )

        self.assertEqual(
            business_day.status,
            BusinessDay.Status.OPEN,
        )
        self.assertEqual(
            business_day.opened_by,
            self.director,
        )

    def test_non_director_cannot_open(self):
        with self.assertRaises(PermissionDenied):
            open_business_day(
                user=self.staff,
                business_date=date(2026, 10, 5),
            )

    def test_only_one_open_business_day(self):
        open_business_day(
            user=self.director,
            business_date=date(2026, 10, 5),
        )

        with self.assertRaises(ValidationError):
            open_business_day(
                user=self.director,
                business_date=date(2026, 10, 6),
            )

    def test_close_locks_business_day(self):
        business_day = open_business_day(
            user=self.director,
            business_date=date(2026, 10, 5),
        )

        close_business_day(
            user=self.director,
            business_day=business_day,
        )

        business_day.refresh_from_db()

        self.assertEqual(
            business_day.status,
            BusinessDay.Status.CLOSED,
        )
        self.assertIsNotNone(business_day.actual_closed_at)

        with self.assertRaises(ValidationError):
            assert_business_day_open(business_day)

    def test_reopen_requires_reason(self):
        business_day = open_business_day(
            user=self.director,
            business_date=date(2026, 10, 5),
        )
        close_business_day(
            user=self.director,
            business_day=business_day,
        )

        with self.assertRaises(ValidationError):
            reopen_business_day(
                user=self.director,
                business_day=business_day,
                reason="",
            )

    def test_reopen_restores_open_state(self):
        business_day = open_business_day(
            user=self.director,
            business_date=date(2026, 10, 5),
        )
        close_business_day(
            user=self.director,
            business_day=business_day,
        )

        reopen_business_day(
            user=self.director,
            business_day=business_day,
            reason="Approved correction.",
        )

        business_day.refresh_from_db()

        self.assertEqual(
            business_day.status,
            BusinessDay.Status.OPEN,
        )
        self.assertEqual(
            business_day.reopen_reason,
            "Approved correction.",
        )

    def test_open_close_reopen_are_audited(self):
        business_day = open_business_day(
            user=self.director,
            business_date=date(2026, 10, 5),
        )
        close_business_day(
            user=self.director,
            business_day=business_day,
        )
        reopen_business_day(
            user=self.director,
            business_day=business_day,
            reason="Approved correction.",
        )

        actions = list(
            AuditLog.objects
            .filter(model_name="BusinessDay")
            .values_list("action", flat=True)
        )

        self.assertIn("CREATE", actions)
        self.assertIn("CLOSE", actions)
        self.assertIn("REOPEN", actions)

    def test_active_employee_can_be_deployed(self):
        business_day = open_business_day(
            user=self.director,
            business_date=date(2026, 10, 5),
        )

        deployment = create_staff_deployment(
            user=self.director,
            employee=self.employee,
            operating_point=self.point,
            business_day=business_day,
            start_time=time(16, 0),
            end_time=time(20, 0),
        )

        self.assertEqual(
            deployment.employee,
            self.employee,
        )

    def test_overlapping_deployment_is_rejected(self):
        business_day = open_business_day(
            user=self.director,
            business_date=date(2026, 10, 5),
        )

        create_staff_deployment(
            user=self.director,
            employee=self.employee,
            operating_point=self.point,
            business_day=business_day,
            start_time=time(16, 0),
            end_time=time(20, 0),
        )

        second_point = OperatingPoint.objects.create(
            code="BDTEST2",
            name="Business Day Test 2",
            is_active=True,
        )

        with self.assertRaises(ValidationError):
            create_staff_deployment(
                user=self.director,
                employee=self.employee,
                operating_point=second_point,
                business_day=business_day,
                start_time=time(18, 0),
                end_time=time(22, 0),
            )

    def test_non_overlapping_multi_point_coverage_is_allowed(self):
        business_day = open_business_day(
            user=self.director,
            business_date=date(2026, 10, 5),
        )

        second_point = OperatingPoint.objects.create(
            code="BDTEST2",
            name="Business Day Test 2",
            is_active=True,
        )

        create_staff_deployment(
            user=self.director,
            employee=self.employee,
            operating_point=self.point,
            business_day=business_day,
            start_time=time(16, 0),
            end_time=time(18, 0),
        )

        deployment = create_staff_deployment(
            user=self.director,
            employee=self.employee,
            operating_point=second_point,
            business_day=business_day,
            start_time=time(18, 0),
            end_time=time(22, 0),
        )

        self.assertEqual(
            deployment.operating_point,
            second_point,
        )

    def test_on_leave_employee_remains_undeployable(self):
        self.employee.employment_status = Employee.EmploymentStatus.ON_LEAVE
        self.employee.save(update_fields=["employment_status", "updated_at"])

        business_day = open_business_day(
            user=self.director,
            business_date=date(2026, 10, 5),
        )

        with self.assertRaises(PermissionDenied):
            create_staff_deployment(
                user=self.director,
                employee=self.employee,
                operating_point=self.point,
                business_day=business_day,
                start_time=time(16, 0),
                end_time=time(20, 0),
            )

    def test_closed_day_cannot_receive_deployment(self):
        business_day = open_business_day(
            user=self.director,
            business_date=date(2026, 10, 5),
        )
        close_business_day(
            user=self.director,
            business_day=business_day,
        )

        with self.assertRaises(ValidationError):
            create_staff_deployment(
                user=self.director,
                employee=self.employee,
                operating_point=self.point,
                business_day=business_day,
                start_time=time(16, 0),
                end_time=time(20, 0),
            )

class HolidayListViewTests(TestCase):
    """Holiday list splitting, edit, toggle."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        from accounts.models import RoleDefinition
        role, _ = RoleDefinition.objects.get_or_create(
            code="DIRECTOR", defaults={"name": "Director"})
        cls.director = User.objects.create_user(
            username="hol_dir", password="pw", role="DIRECTOR",
            role_definition=role, is_active=True, is_active_staff=True)

    def setUp(self):
        from django.utils import timezone
        from datetime import timedelta
        self.client.force_login(self.director)
        today = timezone.localdate()
        self.future = BusinessHoliday.objects.create(
            date=today + timedelta(days=10), name="Future Holiday")
        self.past = BusinessHoliday.objects.create(
            date=today - timedelta(days=10), name="Past Holiday")

    def test_list_splits_upcoming_and_past(self):
        from django.urls import reverse
        r = self.client.get(reverse("business_day:holidays"))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("Future Holiday", body)
        self.assertIn("Past Holiday", body)
        self.assertLess(body.index("Future Holiday"), body.index("Past Holiday"))

    def test_edit_updates_name(self):
        from django.urls import reverse
        r = self.client.post(
            reverse("business_day:holiday_edit", args=[self.future.pk]),
            {"date": self.future.date.isoformat(), "name": "Renamed",
             "is_active": "on", "notes": ""})
        self.assertEqual(r.status_code, 302)
        self.future.refresh_from_db()
        self.assertEqual(self.future.name, "Renamed")

    def test_toggle_flips_is_active(self):
        from django.urls import reverse
        self.assertTrue(self.future.is_active)
        r = self.client.post(
            reverse("business_day:holiday_toggle", args=[self.future.pk]))
        self.assertEqual(r.status_code, 302)
        self.future.refresh_from_db()
        self.assertFalse(self.future.is_active)

    def test_toggle_requires_post(self):
        from django.urls import reverse
        r = self.client.get(
            reverse("business_day:holiday_toggle", args=[self.future.pk]))
        self.assertEqual(r.status_code, 403)

class DailyClosingTests(TestCase):
    """Daily closing page: permissions, fallback, aggregates."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        from accounts.models import RoleDefinition
        User = get_user_model()
        dir_role, _ = RoleDefinition.objects.get_or_create(
            code="DIRECTOR", defaults={"name": "Director"})
        bar_role, _ = RoleDefinition.objects.get_or_create(
            code="BAR_ATTENDANT", defaults={"name": "Bar Attendant"})
        cls.director = User.objects.create_user(
            username="dc_dir", password="pw", role="DIRECTOR",
            role_definition=dir_role, is_active=True, is_active_staff=True)
        cls.staff = User.objects.create_user(
            username="dc_staff", password="pw", role="BAR_ATTENDANT",
            role_definition=bar_role, is_active=True, is_active_staff=True)

    def test_director_sees_page(self):
        from django.urls import reverse
        self.client.force_login(self.director)
        r = self.client.get(reverse("business_day:closing"))
        self.assertEqual(r.status_code, 200)

    def test_non_director_forbidden(self):
        from django.urls import reverse
        self.client.force_login(self.staff)
        r = self.client.get(reverse("business_day:closing"))
        self.assertEqual(r.status_code, 403)

    def test_anonymous_redirects(self):
        from django.urls import reverse
        r = self.client.get(reverse("business_day:closing"))
        self.assertEqual(r.status_code, 302)

    def test_no_business_day_shows_empty_state(self):
        from django.urls import reverse
        self.client.force_login(self.director)
        r = self.client.get(reverse("business_day:closing"))
        self.assertContains(r, "No Business Day Yet")

    def test_open_day_is_shown(self):
        from django.urls import reverse
        from django.utils import timezone
        BusinessDay.objects.create(
            business_date=timezone.localdate(),
            status=BusinessDay.Status.OPEN,
            actual_opened_at=timezone.now(),
            opened_by=self.director,
        )
        self.client.force_login(self.director)
        r = self.client.get(reverse("business_day:closing"))
        self.assertContains(r, "OPEN")
        self.assertContains(r, "Close Business Day")

    def test_falls_back_to_most_recent_closed_day(self):
        from django.urls import reverse
        from django.utils import timezone
        from datetime import timedelta
        BusinessDay.objects.create(
            business_date=timezone.localdate() - timedelta(days=1),
            status=BusinessDay.Status.CLOSED,
            actual_opened_at=timezone.now() - timedelta(days=1),
            actual_closed_at=timezone.now() - timedelta(hours=12),
            opened_by=self.director,
            closed_by=self.director,
        )
        self.client.force_login(self.director)
        r = self.client.get(reverse("business_day:closing"))
        self.assertContains(r, "CLOSED")