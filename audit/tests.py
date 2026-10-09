from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import OperatingPoint, RoleDefinition, RolePermission

from .models import AuditLog
from .services import record_audit


class AuditTestBase(TestCase):
    def setUp(self):
        User = get_user_model()

        self.director_role = RoleDefinition.objects.get(code=User.Role.DIRECTOR)
        self.bar_role = RoleDefinition.objects.get(code=User.Role.BAR_ATTENDANT)

        self.director = User.objects.create_user(
            username="audit-director",
            email="audit-director@example.com",
            password="StrongAuditPassword123!",
            role=User.Role.DIRECTOR,
            role_definition=self.director_role,
            is_active=True,
            is_active_staff=True,
        )

        self.staff = User.objects.create_user(
            username="audit-staff",
            email="audit-staff@example.com",
            password="StrongAuditPassword123!",
            role=User.Role.BAR_ATTENDANT,
            role_definition=self.bar_role,
            is_active=True,
            is_active_staff=True,
        )

        self.point = OperatingPoint.objects.create(
            code="AUDIT_BAR",
            name="Audit Bar",
            is_active=True,
        )


class AuditLogModelTests(AuditTestBase):
    def test_audit_log_is_append_only(self):
        entry = AuditLog.objects.create(
            user=self.director,
            action=AuditLog.Action.CREATE,
            model_name="Employee",
            object_id="10",
            object_repr="Audit Employee",
            operating_point=self.point,
            reason="Created for audit test",
            changes={"status": {"old": "INVITED", "new": "ACTIVE"}},
        )

        with self.assertRaises(PermissionError):
            entry.save()

        with self.assertRaises(PermissionError):
            entry.delete()

    def test_record_audit_captures_request_metadata(self):
        self.client.force_login(self.director)

        response = self.client.post(
            reverse("audit:audit_log"),
            HTTP_USER_AGENT="SPOTZ-Audit-Test-Agent",
        )

        self.assertEqual(response.status_code, 200)

        request = response.wsgi_request

        entry = record_audit(
            request=request,
            action=AuditLog.Action.UPDATE,
            obj=self.point,
            reason="Metadata test",
            changes={"is_active": {"old": True, "new": False}},
        )

        self.assertEqual(entry.user, self.director)
        self.assertEqual(entry.model_name, "OperatingPoint")
        self.assertEqual(entry.object_id, str(self.point.pk))
        self.assertEqual(entry.object_repr, str(self.point))
        self.assertEqual(entry.operating_point, self.point)
        self.assertEqual(entry.reason, "Metadata test")
        self.assertEqual(
            entry.changes,
            {"is_active": {"old": True, "new": False}},
        )
        self.assertEqual(entry.user_agent, "SPOTZ-Audit-Test-Agent")
        self.assertEqual(entry.ip_address, "127.0.0.1")


class AuditViewerAuthorizationTests(AuditTestBase):
    def test_director_can_open_audit_log(self):
        self.client.force_login(self.director)

        response = self.client.get(reverse("audit:audit_log"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "audit/audit_log.html")

    def test_non_director_gets_403(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("audit:audit_log"))

        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("audit:audit_log"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_inactive_staff_cannot_open_audit_log(self):
        self.director.is_active_staff = False
        self.director.save(update_fields=["is_active_staff"])

        self.client.force_login(self.director)

        response = self.client.get(reverse("audit:audit_log"))

        self.assertEqual(response.status_code, 403)


class AuditViewerFilterTests(AuditTestBase):
    def _create_entry(
        self,
        *,
        user,
        action,
        model_name,
        object_repr,
        reason,
        timestamp_offset=0,
    ):
        entry = AuditLog.objects.create(
            user=user,
            action=action,
            model_name=model_name,
            object_id="1",
            object_repr=object_repr,
            operating_point=self.point,
            reason=reason,
            changes={"field": {"old": "A", "new": "B"}},
        )

        if timestamp_offset:
            AuditLog.objects.filter(pk=entry.pk).update(
                timestamp=timezone.now() + timedelta(minutes=timestamp_offset)
            )
            entry.refresh_from_db()

        return entry

    def test_action_filter(self):
        self._create_entry(
            user=self.director,
            action=AuditLog.Action.CREATE,
            model_name="Employee",
            object_repr="Created Employee",
            reason="created",
        )
        self._create_entry(
            user=self.director,
            action=AuditLog.Action.UPDATE,
            model_name="Employee",
            object_repr="Updated Employee",
            reason="updated",
        )

        self.client.force_login(self.director)

        response = self.client.get(
            reverse("audit:audit_log"),
            {"action": AuditLog.Action.CREATE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["audit_count"], 1)

    def test_model_filter(self):
        self._create_entry(
            user=self.director,
            action=AuditLog.Action.CREATE,
            model_name="Employee",
            object_repr="Employee A",
            reason="employee",
        )
        self._create_entry(
            user=self.director,
            action=AuditLog.Action.CREATE,
            model_name="RoleDefinition",
            object_repr="Role A",
            reason="role",
        )

        self.client.force_login(self.director)

        response = self.client.get(
            reverse("audit:audit_log"),
            {"model_name": "Role"},
        )

        self.assertEqual(response.context["audit_count"], 1)

    def test_user_filter(self):
        self._create_entry(
            user=self.director,
            action=AuditLog.Action.UPDATE,
            model_name="Employee",
            object_repr="Director Change",
            reason="director",
        )
        self._create_entry(
            user=self.staff,
            action=AuditLog.Action.UPDATE,
            model_name="Employee",
            object_repr="Staff Change",
            reason="staff",
        )

        self.client.force_login(self.director)

        response = self.client.get(
            reverse("audit:audit_log"),
            {"user": self.staff.pk},
        )

        self.assertEqual(response.context["audit_count"], 1)

    def test_operating_point_filter(self):
        other_point = OperatingPoint.objects.create(
            code="AUDIT_KITCHEN",
            name="Audit Kitchen",
            is_active=True,
        )

        AuditLog.objects.create(
            user=self.director,
            action=AuditLog.Action.CREATE,
            model_name="Employee",
            object_id="1",
            object_repr="Bar Employee",
            operating_point=self.point,
        )
        AuditLog.objects.create(
            user=self.director,
            action=AuditLog.Action.CREATE,
            model_name="Employee",
            object_id="2",
            object_repr="Kitchen Employee",
            operating_point=other_point,
        )

        self.client.force_login(self.director)

        response = self.client.get(
            reverse("audit:audit_log"),
            {"operating_point": other_point.pk},
        )

        self.assertEqual(response.context["audit_count"], 1)

    def test_keyword_search(self):
        self._create_entry(
            user=self.director,
            action=AuditLog.Action.UPDATE,
            model_name="Employee",
            object_repr="John Example",
            reason="Role changed after management review",
        )
        self._create_entry(
            user=self.director,
            action=AuditLog.Action.UPDATE,
            model_name="OperatingPoint",
            object_repr="Audit Bar",
            reason="Location updated",
        )

        self.client.force_login(self.director)

        response = self.client.get(
            reverse("audit:audit_log"),
            {"search": "management review"},
        )

        self.assertEqual(response.context["audit_count"], 1)

    def test_date_time_filters(self):
        now = timezone.now()

        old_entry = AuditLog.objects.create(
            user=self.director,
            action=AuditLog.Action.CREATE,
            model_name="Employee",
            object_id="1",
            object_repr="Old Entry",
        )
        recent_entry = AuditLog.objects.create(
            user=self.director,
            action=AuditLog.Action.CREATE,
            model_name="Employee",
            object_id="2",
            object_repr="Recent Entry",
        )

        AuditLog.objects.filter(pk=old_entry.pk).update(
            timestamp=now - timedelta(days=5)
        )
        AuditLog.objects.filter(pk=recent_entry.pk).update(
            timestamp=now
        )

        self.client.force_login(self.director)

        start = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

        response = self.client.get(
            reverse("audit:audit_log"),
            {"start_datetime": start},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["audit_count"], 1)

    def test_pagination(self):
        AuditLog.objects.bulk_create(
            [
                AuditLog(
                    user=self.director,
                    action=AuditLog.Action.CREATE,
                    model_name="Employee",
                    object_id=str(index),
                    object_repr=f"Employee {index}",
                )
                for index in range(75)
            ]
        )

        self.client.force_login(self.director)

        response = self.client.get(reverse("audit:audit_log"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["audit_count"], 75)
        self.assertEqual(len(response.context["page_obj"].object_list), 50)

        response = self.client.get(
            reverse("audit:audit_log"),
            {"page": 2},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page_obj"].object_list), 25)

    def test_structured_changes_are_visible(self):
        AuditLog.objects.create(
            user=self.director,
            action=AuditLog.Action.UPDATE,
            model_name="Employee",
            object_id="7",
            object_repr="Structured Employee",
            changes={
                "role": {
                    "old": "Bar Attendant",
                    "new": "Manager",
                },
                "operating_point": {
                    "old": "Bar",
                    "new": "Choma",
                },
            },
        )

        self.client.force_login(self.director)

        response = self.client.get(reverse("audit:audit_log"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bar Attendant")
        self.assertContains(response, "Manager")
        self.assertContains(response, "Choma")
