
from datetime import timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.employee_management import (
    _authorize_employee_deployment,
    effective_employment_status,
)
from accounts.models import (
    DeploymentHistory,
    Employee,
    EmployeeLeave,
    EmployeeSuspension,
    EmployeeTermination,
    EmployeeWarning,
    OperatingPoint,
    RoleDefinition,
    User,
)


class EmployeeManagementLifecycleTests(TestCase):
    def setUp(self):
        self.director_role = RoleDefinition.objects.get(code=User.Role.DIRECTOR)
        self.bar_role = RoleDefinition.objects.get(code=User.Role.BAR_ATTENDANT)

        self.director = User.objects.create_user(
            username="director",
            email="director@example.com",
            password="DirectorPass123!",
            role=User.Role.DIRECTOR,
            role_definition=self.director_role,
            is_active=True,
            is_active_staff=True,
        )

        self.staff_user = User.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="StaffPass123!",
            role=User.Role.BAR_ATTENDANT,
            role_definition=self.bar_role,
            is_active=True,
            is_active_staff=True,
        )

        self.point = OperatingPoint.objects.create(
            code="BAR_TEST",
            name="Bar Test",
            is_active=True,
        )
        self.point_two = OperatingPoint.objects.create(
            code="ROOF_TEST",
            name="Roof Test",
            is_active=True,
        )
        self.inactive_point = OperatingPoint.objects.create(
            code="CLOSED_TEST",
            name="Closed Test",
            is_active=False,
        )

        self.employee = Employee.objects.create(
            user=self.staff_user,
            operating_point=self.point,
            employment_status=Employee.EmploymentStatus.ACTIVE,
            date_joined=timezone.localdate(),
        )

        self.client.force_login(self.director)

    def post_data(self, url_name, data):
        return self.client.post(
            reverse(url_name, kwargs={"pk": self.employee.pk}),
            data,
        )

    def leave_type(self):
        field = EmployeeLeave._meta.get_field("leave_type")
        choices = list(field.choices or [])
        if choices:
            return choices[0][0]
        return "OTHER"

    def termination_data(self):
        return {
            "termination_date": timezone.localdate().isoformat(),
            "reason": "Lifecycle test termination",
            "director_comment": "Controlled test",
        }

    def suspension_data(self, start=None, end=None):
        start = start or timezone.localdate()
        end = end or start
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "reason": "Lifecycle test suspension",
            "director_comment": "Controlled test",
        }

    def leave_data(self, start=None, end=None):
        start = start or timezone.localdate()
        end = end or start
        return {
            "leave_type": self.leave_type(),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "reason": "Lifecycle test leave",
        }

    def edit_data(self, status, point=None, role=None):
        return {
            "role_definition": (role or self.bar_role).pk,
            "employment_status": status,
            "date_joined": timezone.localdate().isoformat(),
            "operating_point": (point or self.point).pk,
        }

    def test_active_employee_can_move_to_off_duty(self):
        response = self.post_data(
            "employee_edit",
            self.edit_data(Employee.EmploymentStatus.OFF_DUTY),
        )

        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(
            self.employee.employment_status,
            Employee.EmploymentStatus.OFF_DUTY,
        )

    def test_off_duty_employee_can_return_to_active(self):
        self.employee.employment_status = Employee.EmploymentStatus.OFF_DUTY
        self.employee.save(update_fields=["employment_status"])

        response = self.post_data(
            "employee_edit",
            self.edit_data(Employee.EmploymentStatus.ACTIVE),
        )

        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(
            self.employee.employment_status,
            Employee.EmploymentStatus.ACTIVE,
        )

    def test_invited_employee_cannot_be_activated_by_edit(self):
        self.employee.employment_status = Employee.EmploymentStatus.INVITED
        self.employee.save(update_fields=["employment_status"])

        response = self.post_data(
            "employee_edit",
            self.edit_data(Employee.EmploymentStatus.ACTIVE),
        )

        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(
            self.employee.employment_status,
            Employee.EmploymentStatus.INVITED,
        )

    def test_invited_employee_cannot_be_terminated(self):
        self.employee.employment_status = Employee.EmploymentStatus.INVITED
        self.employee.save(update_fields=["employment_status"])

        response = self.post_data(
            "employee_terminate",
            self.termination_data(),
        )

        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(
            self.employee.employment_status,
            Employee.EmploymentStatus.INVITED,
        )
        self.assertFalse(
            EmployeeTermination.objects.filter(
                employee=self.employee
            ).exists()
        )

    def test_suspension_requires_active_employee(self):
        self.employee.employment_status = Employee.EmploymentStatus.OFF_DUTY
        self.employee.save(update_fields=["employment_status"])

        response = self.post_data(
            "employee_suspend",
            self.suspension_data(),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            EmployeeSuspension.objects.filter(
                employee=self.employee
            ).exists()
        )

    def test_leave_requires_active_employee(self):
        self.employee.employment_status = Employee.EmploymentStatus.OFF_DUTY
        self.employee.save(update_fields=["employment_status"])

        response = self.post_data(
            "employee_leave",
            self.leave_data(),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            EmployeeLeave.objects.filter(
                employee=self.employee
            ).exists()
        )

    def test_suspension_blocks_operational_deployment(self):
        self.employee.employment_status = Employee.EmploymentStatus.SUSPENDED
        self.employee.save(update_fields=["employment_status"])

        with self.assertRaises(PermissionDenied):
            _authorize_employee_deployment(
                self.employee,
                self.point_two,
            )

    def test_leave_blocks_operational_deployment(self):
        self.employee.employment_status = Employee.EmploymentStatus.ON_LEAVE
        self.employee.save(update_fields=["employment_status"])

        with self.assertRaises(PermissionDenied):
            _authorize_employee_deployment(
                self.employee,
                self.point_two,
            )

    def test_off_duty_blocks_operational_deployment(self):
        self.employee.employment_status = Employee.EmploymentStatus.OFF_DUTY
        self.employee.save(update_fields=["employment_status"])

        with self.assertRaises(PermissionDenied):
            _authorize_employee_deployment(
                self.employee,
                self.point_two,
            )

    def test_invited_blocks_operational_deployment(self):
        self.employee.employment_status = Employee.EmploymentStatus.INVITED
        self.employee.save(update_fields=["employment_status"])

        with self.assertRaises(PermissionDenied):
            _authorize_employee_deployment(
                self.employee,
                self.point_two,
            )

    def test_terminated_blocks_operational_deployment(self):
        self.employee.employment_status = Employee.EmploymentStatus.TERMINATED
        self.employee.save(update_fields=["employment_status"])

        with self.assertRaises(PermissionDenied):
            _authorize_employee_deployment(
                self.employee,
                self.point_two,
            )

    def test_inactive_operating_point_cannot_be_assigned(self):
        with self.assertRaises(PermissionDenied):
            _authorize_employee_deployment(
                self.employee,
                self.inactive_point,
            )

    def test_employee_edit_cannot_deploy_to_inactive_point(self):
        response = self.post_data(
            "employee_edit",
            self.edit_data(
                Employee.EmploymentStatus.ACTIVE,
                point=self.inactive_point,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.employee.refresh_from_db()
        self.assertEqual(
            self.employee.operating_point_id,
            self.point.pk,
        )
        self.assertFalse(
            DeploymentHistory.objects.filter(
                employee=self.employee
            ).exists()
        )

    def test_employee_edit_does_not_mutate_before_deployment_authorization(self):
        original_point = self.employee.operating_point

        response = self.post_data(
            "employee_edit",
            self.edit_data(
                Employee.EmploymentStatus.ACTIVE,
                point=self.point_two,
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(
            self.employee.operating_point_id,
            self.point_two.pk,
        )

        history = DeploymentHistory.objects.get(
            employee=self.employee
        )
        self.assertEqual(
            history.from_operating_point_id,
            original_point.pk,
        )
        self.assertEqual(
            history.to_operating_point_id,
            self.point_two.pk,
        )

    def test_suspension_can_be_created_and_sets_status_when_current(self):
        response = self.post_data(
            "employee_suspend",
            self.suspension_data(),
        )

        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()

        self.assertEqual(
            self.employee.employment_status,
            Employee.EmploymentStatus.SUSPENDED,
        )
        self.assertTrue(
            EmployeeSuspension.objects.filter(
                employee=self.employee,
                activated_early=False,
            ).exists()
        )

    def test_future_suspension_does_not_change_current_status(self):
        tomorrow = timezone.localdate() + timedelta(days=1)

        response = self.post_data(
            "employee_suspend",
            self.suspension_data(
                start=tomorrow,
                end=tomorrow + timedelta(days=2),
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(
            self.employee.employment_status,
            Employee.EmploymentStatus.ACTIVE,
        )

    def test_suspension_overlap_is_rejected(self):
        start = timezone.localdate()
        end = start + timedelta(days=2)

        EmployeeSuspension.objects.create(
            employee=self.employee,
            start_date=start,
            end_date=end,
            reason="Existing",
            issued_by=self.director,
        )

        response = self.post_data(
            "employee_suspend",
            self.suspension_data(
                start=start + timedelta(days=1),
                end=end + timedelta(days=1),
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            EmployeeSuspension.objects.filter(
                employee=self.employee
            ).count(),
            1,
        )

    def test_leave_overlap_with_suspension_is_rejected(self):
        start = timezone.localdate()
        end = start + timedelta(days=2)

        EmployeeSuspension.objects.create(
            employee=self.employee,
            start_date=start,
            end_date=end,
            reason="Existing",
            issued_by=self.director,
        )

        response = self.post_data(
            "employee_leave",
            self.leave_data(start=start, end=end),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            EmployeeLeave.objects.filter(
                employee=self.employee
            ).exists()
        )

    def test_leave_can_be_created_and_sets_status_when_current(self):
        response = self.post_data(
            "employee_leave",
            self.leave_data(),
        )

        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(
            self.employee.employment_status,
            Employee.EmploymentStatus.ON_LEAVE,
        )

    def test_early_suspension_activation_records_actor_and_timestamp(self):
        self.employee.employment_status = Employee.EmploymentStatus.SUSPENDED
        self.employee.save(update_fields=["employment_status"])

        suspension = EmployeeSuspension.objects.create(
            employee=self.employee,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=3),
            reason="Existing",
            issued_by=self.director,
        )

        before = timezone.now()

        response = self.client.post(
            reverse(
                "employee_activate",
                kwargs={"pk": self.employee.pk},
            )
        )

        after = timezone.now()

        self.assertEqual(response.status_code, 302)

        suspension.refresh_from_db()
        self.employee.refresh_from_db()

        self.assertTrue(suspension.activated_early)
        self.assertEqual(
            suspension.activated_by_id,
            self.director.pk,
        )
        self.assertIsNotNone(suspension.activated_at)
        self.assertGreaterEqual(suspension.activated_at, before)
        self.assertLessEqual(suspension.activated_at, after)
        self.assertEqual(
            self.employee.employment_status,
            Employee.EmploymentStatus.ACTIVE,
        )

    def test_off_duty_can_be_reactivated(self):
        self.employee.employment_status = Employee.EmploymentStatus.OFF_DUTY
        self.employee.save(update_fields=["employment_status"])

        response = self.client.post(
            reverse(
                "employee_activate",
                kwargs={"pk": self.employee.pk},
            )
        )

        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(
            self.employee.employment_status,
            Employee.EmploymentStatus.ACTIVE,
        )

    def test_terminated_employee_cannot_be_reactivated(self):
        self.employee.employment_status = Employee.EmploymentStatus.TERMINATED
        self.employee.save(update_fields=["employment_status"])

        response = self.client.post(
            reverse(
                "employee_activate",
                kwargs={"pk": self.employee.pk},
            )
        )

        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(
            self.employee.employment_status,
            Employee.EmploymentStatus.TERMINATED,
        )

    def test_termination_is_terminal(self):
        response = self.post_data(
            "employee_terminate",
            self.termination_data(),
        )

        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(
            self.employee.employment_status,
            Employee.EmploymentStatus.TERMINATED,
        )

        response = self.post_data(
            "employee_edit",
            self.edit_data(Employee.EmploymentStatus.ACTIVE),
        )

        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(
            self.employee.employment_status,
            Employee.EmploymentStatus.TERMINATED,
        )

    def test_director_cannot_change_own_lifecycle(self):
        director_employee = Employee.objects.create(
            user=self.director,
            operating_point=self.point,
            employment_status=Employee.EmploymentStatus.ACTIVE,
        )

        response = self.client.post(
            reverse(
                "employee_edit",
                kwargs={"pk": director_employee.pk},
            ),
            self.edit_data(Employee.EmploymentStatus.OFF_DUTY),
        )

        self.assertEqual(response.status_code, 302)
        director_employee.refresh_from_db()
        self.assertEqual(
            director_employee.employment_status,
            Employee.EmploymentStatus.ACTIVE,
        )

    def test_warning_records_are_immutable(self):
        warning = EmployeeWarning.objects.create(
            employee=self.employee,
            warning_type=EmployeeWarning.WarningType.CONDUCT,
            severity=EmployeeWarning.Severity.MEDIUM,
            incident_date=timezone.localdate(),
            reason="Original",
            issued_by=self.director,
        )

        warning.reason = "Changed"

        with self.assertRaises(PermissionError):
            warning.save()

        with self.assertRaises(PermissionError):
            warning.delete()

    def test_suspension_records_are_immutable(self):
        suspension = EmployeeSuspension.objects.create(
            employee=self.employee,
            start_date=timezone.localdate(),
            end_date=timezone.localdate(),
            reason="Original",
            issued_by=self.director,
        )

        suspension.reason = "Changed"

        with self.assertRaises(PermissionError):
            suspension.save()

        with self.assertRaises(PermissionError):
            suspension.delete()

    def test_suspension_activation_metadata_is_one_way(self):
        suspension = EmployeeSuspension.objects.create(
            employee=self.employee,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=2),
            reason="Original",
            issued_by=self.director,
        )

        suspension.activated_early = True
        suspension.activated_by = self.director
        suspension.activated_at = timezone.now()
        suspension.save()

        suspension.refresh_from_db()

        suspension.activated_at = timezone.now() + timedelta(days=1)

        with self.assertRaises(PermissionError):
            suspension.save()

    def test_leave_records_cannot_be_deleted(self):
        leave = EmployeeLeave.objects.create(
            employee=self.employee,
            leave_type=self.leave_type(),
            start_date=timezone.localdate(),
            end_date=timezone.localdate(),
            reason="Original",
            approved_by=self.director,
        )

        with self.assertRaises(PermissionError):
            leave.delete()

    def test_deployment_history_cannot_be_deleted(self):
        history = DeploymentHistory.objects.create(
            employee=self.employee,
            from_operating_point=self.point,
            to_operating_point=self.point_two,
            reason="Test",
            changed_by=self.director,
        )

        with self.assertRaises(PermissionError):
            history.delete()

    def test_termination_history_cannot_be_deleted(self):
        self.employee.employment_status = Employee.EmploymentStatus.OFF_DUTY
        self.employee.save(update_fields=["employment_status"])

        termination = EmployeeTermination.objects.create(
            employee=self.employee,
            termination_date=timezone.localdate(),
            reason="Original",
            terminated_by=self.director,
        )

        with self.assertRaises(PermissionError):
            termination.delete()

    def test_lifecycle_audit_carries_operating_point(self):
        response = self.post_data(
            "employee_edit",
            self.edit_data(Employee.EmploymentStatus.OFF_DUTY),
        )

        self.assertEqual(response.status_code, 302)

        from audit.models import AuditLog

        audit = AuditLog.objects.filter(
            model_name="Employee",
            object_id=str(self.employee.pk),
        ).order_by("-id").first()

        self.assertIsNotNone(audit)
        self.assertEqual(
            audit.operating_point_id,
            self.point.pk,
        )

    def test_effective_status_expires_suspension(self):
        yesterday = timezone.localdate() - timedelta(days=1)

        self.employee.employment_status = Employee.EmploymentStatus.SUSPENDED
        self.employee.save(update_fields=["employment_status"])

        EmployeeSuspension.objects.create(
            employee=self.employee,
            start_date=yesterday - timedelta(days=2),
            end_date=yesterday,
            reason="Expired",
            issued_by=self.director,
        )

        self.assertEqual(
            effective_employment_status(self.employee),
            Employee.EmploymentStatus.ACTIVE,
        )
