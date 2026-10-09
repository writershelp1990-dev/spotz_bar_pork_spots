from django.core.exceptions import PermissionDenied
from django.core import mail
from django.core.signing import dumps
from django.test import TestCase
from django.urls import reverse
from .models import Employee, OperatingPoint, User, PermissionDefinition, RoleDefinition, RolePermission

class AuthenticationTests(TestCase):

    def setUp(self):
        self.point = OperatingPoint.objects.create(code='BAR', name='Bar')
        self.user = User.objects.create_user(username='director@example.com', email='director@example.com', password='StrongPassword123!', first_name='Director', role=User.Role.DIRECTOR)
        Employee.objects.create(user=self.user, operating_point=self.point)

    def test_login_success(self):
        response = self.client.post(reverse('login'), {'username': 'director@example.com', 'password': 'StrongPassword123!'})
        self.assertRedirects(response, reverse('dashboard'))
        self.assertIn('_auth_user_id', self.client.session)

    def test_invalid_login(self):
        response = self.client.post(reverse('login'), {'username': 'director@example.com', 'password': 'WrongPassword!'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please enter a correct email address and password')

    def test_logout(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_profile_can_be_updated_by_user(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('profile'), {'first_name': 'Updated', 'last_name': 'Director', 'phone': '0712345678', 'whatsapp_number': '0712345678', 'mpesa_number': '0712345678'})
        self.assertRedirects(response, reverse('profile'))
        self.user.refresh_from_db()
        employee = self.user.employee_profile
        self.assertEqual(self.user.first_name, 'Updated')
        self.assertEqual(employee.whatsapp_number, '0712345678')
        self.assertEqual(employee.mpesa_number, '0712345678')

    def test_invitation_email_is_sent(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('invite_staff'), {'full_name': 'Bar Attendant', 'email': 'bar@example.com', 'role_definition': RoleDefinition.objects.get(code='BAR_ATTENDANT').pk, 'operating_point': self.point.pk})
        self.assertRedirects(response, reverse('invitation_success'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('24 hours', mail.outbox[0].body)
        invited = User.objects.get(email='bar@example.com')
        self.assertFalse(invited.has_usable_password())

    def test_invitation_completion_activates_account(self):
        invited = User.objects.create(username='staff@example.com', email='staff@example.com', first_name='Staff', role=User.Role.BAR_ATTENDANT)
        invited.set_unusable_password()
        invited.save()
        Employee.objects.create(user=invited, operating_point=self.point)
        token = dumps({'uid': invited.pk, 'email': invited.email}, salt='spotz-staff-invitation')
        response = self.client.post(reverse('complete_invitation', kwargs={'token': token}), {'password1': 'StrongPassword456!', 'password2': 'StrongPassword456!', 'phone': '0799999999', 'whatsapp_number': '0799999999', 'mpesa_number': '0799999999'})
        self.assertRedirects(response, reverse('dashboard'))
        invited.refresh_from_db()
        self.assertTrue(invited.has_usable_password())
        self.assertTrue(invited.check_password('StrongPassword456!'))

    def test_invitation_cannot_be_reused(self):
        invited = User.objects.create(username='used@example.com', email='used@example.com', role=User.Role.BAR_ATTENDANT)
        invited.set_password('AlreadyActivated123!')
        invited.save()
        Employee.objects.create(user=invited, operating_point=self.point)
        token = dumps({'uid': invited.pk, 'email': invited.email}, salt='spotz-staff-invitation')
        response = self.client.get(reverse('complete_invitation', kwargs={'token': token}))
        self.assertEqual(response.status_code, 410)

class AuthorizationFoundationTests(TestCase):

    def setUp(self):
        from .models import PermissionDefinition, RoleDefinition, RolePermission, User
        self.director_role = RoleDefinition.objects.get(code=User.Role.DIRECTOR)
        self.bar_role = RoleDefinition.objects.get(code=User.Role.BAR_ATTENDANT)
        self.staff_permission = PermissionDefinition.objects.get(code='manage:staff')
        RolePermission.objects.get_or_create(role=self.director_role, permission=self.staff_permission)
        self.director = User.objects.create_user(username='authorization_director', password='TestPassword123!', role=User.Role.DIRECTOR, role_definition=self.director_role, is_active=True, is_active_staff=True)
        self.bar_staff = User.objects.create_user(username='authorization_bar', password='TestPassword123!', role=User.Role.BAR_ATTENDANT, role_definition=self.bar_role, is_active=True, is_active_staff=True)

    def test_director_has_manage_staff_permission(self):
        self.assertTrue(self.director.user_has_permission('manage:staff'))

    def test_bar_attendant_does_not_have_manage_staff_permission(self):
        self.assertFalse(self.bar_staff.user_has_permission('manage:staff'))

    def test_manage_staff_requires_director(self):
        self.client.force_login(self.bar_staff)
        response = self.client.get('/accounts/staff/')
        self.assertEqual(response.status_code, 403)

    def test_director_can_open_manage_staff(self):
        self.client.force_login(self.director)
        response = self.client.get('/accounts/staff/')
        self.assertEqual(response.status_code, 200)

    def test_inactive_staff_cannot_use_permissions(self):
        self.director.is_active_staff = False
        self.director.save(update_fields=['is_active_staff'])
        self.assertFalse(self.director.user_has_permission('manage:staff'))

class DynamicRoleManagementTests(TestCase):

    def setUp(self):
        self.director_role = RoleDefinition.objects.get(code='DIRECTOR')
        self.staff_permission = PermissionDefinition.objects.get(code='manage:staff')
        self.roles_permission = PermissionDefinition.objects.get(code='manage:roles')
        RolePermission.objects.get_or_create(role=self.director_role, permission=self.staff_permission)
        RolePermission.objects.get_or_create(role=self.director_role, permission=self.roles_permission)
        self.director = User.objects.create_user(username='role-director@example.com', email='role-director@example.com', password='StrongTestPassword123!', role=User.Role.DIRECTOR, role_definition=self.director_role, is_active=True, is_active_staff=True)
        self.client.force_login(self.director)

    def test_director_can_open_role_management(self):
        response = self.client.get('/accounts/staff/roles/')
        self.assertEqual(response.status_code, 200)

    def test_director_can_create_manager_without_permissions(self):
        response = self.client.post('/accounts/staff/roles/create/', {'name': 'Manager', 'description': 'Custom management role.', 'is_active': 'on', 'permissions': []})
        self.assertEqual(response.status_code, 302)
        manager = RoleDefinition.objects.get(code='MANAGER')
        self.assertEqual(manager.name, 'Manager')
        self.assertFalse(manager.is_system_role)
        self.assertTrue(manager.is_active)
        self.assertEqual(manager.role_permissions.count(), 0)

    def test_system_role_cannot_be_duplicated_by_name(self):
        response = self.client.post('/accounts/staff/roles/create/', {'name': 'Director', 'description': 'Attempted duplicate.', 'is_active': 'on', 'permissions': []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RoleDefinition.objects.filter(code='DIRECTOR').count(), 1)

    def test_non_director_cannot_manage_roles(self):
        bar_role = RoleDefinition.objects.get(code='BAR_ATTENDANT')
        bar_user = User.objects.create_user(username='bar-role-test@example.com', email='bar-role-test@example.com', password='StrongTestPassword123!', role=User.Role.BAR_ATTENDANT, role_definition=bar_role, is_active=True, is_active_staff=True)
        self.client.force_login(bar_user)
        response = self.client.get('/accounts/staff/roles/')
        self.assertEqual(response.status_code, 403)

class OperatingPointManagementTests(TestCase):

    def setUp(self):
        self.director_password = 'DirectorTestPass123!'
        self.director = User.objects.create_user(username='op-director@example.com', email='op-director@example.com', password=self.director_password, role=User.Role.DIRECTOR, is_active=True, is_active_staff=True)
        self.bar_attendant = User.objects.create_user(username='op-bar@example.com', email='op-bar@example.com', password='BarTestPass123!', role=User.Role.BAR_ATTENDANT, is_active=True, is_active_staff=True)
        self.client.force_login(self.director)

    def test_director_can_open_operating_points(self):
        response = self.client.get(reverse('manage_operating_points'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/manage_operating_points.html')

    def test_non_director_receives_403(self):
        self.client.force_login(self.bar_attendant)
        response = self.client.get(reverse('manage_operating_points'))
        self.assertEqual(response.status_code, 403)

    def test_director_can_create_custom_operating_point(self):
        response = self.client.post(reverse('create_operating_point'), {'code': 'rooftop', 'name': 'Rooftop', 'description': 'Upper rooftop service area', 'is_active': 'on'})
        self.assertRedirects(response, reverse('manage_operating_points'))
        operating_point = OperatingPoint.objects.get(code='ROOFTOP')
        self.assertEqual(operating_point.name, 'Rooftop')
        self.assertEqual(operating_point.description, 'Upper rooftop service area')
        self.assertTrue(operating_point.is_active)
        self.assertFalse(operating_point.is_system_point)

    def test_operating_point_code_is_normalized_to_uppercase(self):
        response = self.client.post(reverse('create_operating_point'), {'code': 'outdoor_bar', 'name': 'Outdoor Bar', 'description': '', 'is_active': 'on'})
        self.assertRedirects(response, reverse('manage_operating_points'))
        self.assertTrue(OperatingPoint.objects.filter(code='OUTDOOR_BAR').exists())
        self.assertFalse(OperatingPoint.objects.filter(code='outdoor_bar').exists())

    def test_invalid_operating_point_code_is_rejected(self):
        response = self.client.post(reverse('create_operating_point'), {'code': 'ROOFTOP-BAR', 'name': 'Rooftop Bar', 'description': '', 'is_active': 'on'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OperatingPoint.objects.filter(name='Rooftop Bar').exists())
        form = response.context['form']
        self.assertTrue(form.errors)
        self.assertIn('code', form.errors)

    def test_director_can_edit_custom_operating_point(self):
        operating_point = OperatingPoint.objects.create(code='ROOFTOP', name='Rooftop', description='Original description', is_system_point=False, is_active=True)
        response = self.client.post(reverse('edit_operating_point', kwargs={'pk': operating_point.pk}), {'code': 'rooftop', 'name': 'Rooftop Lounge', 'description': 'Updated description', 'is_active': 'on'})
        self.assertRedirects(response, reverse('manage_operating_points'))
        operating_point.refresh_from_db()
        self.assertEqual(operating_point.code, 'ROOFTOP')
        self.assertEqual(operating_point.name, 'Rooftop Lounge')
        self.assertEqual(operating_point.description, 'Updated description')
        self.assertTrue(operating_point.is_active)

    def test_system_operating_point_code_cannot_be_changed(self):
        operating_point = OperatingPoint.objects.create(code='BAR', name='Bar', description='System bar', is_system_point=True, is_active=True)
        response = self.client.post(reverse('edit_operating_point', kwargs={'pk': operating_point.pk}), {'code': 'MAIN_BAR', 'name': 'Main Bar', 'description': 'Updated system bar', 'is_active': 'on'})
        self.assertRedirects(response, reverse('manage_operating_points'))
        operating_point.refresh_from_db()
        self.assertEqual(operating_point.code, 'BAR')
        self.assertEqual(operating_point.name, 'Main Bar')
        self.assertEqual(operating_point.description, 'Updated system bar')

    def test_director_can_deactivate_and_reactivate_operating_point(self):
        operating_point = OperatingPoint.objects.create(code='ROOFTOP', name='Rooftop', is_system_point=False, is_active=True)
        deactivate_response = self.client.post(reverse('toggle_operating_point', kwargs={'pk': operating_point.pk}))
        self.assertRedirects(deactivate_response, reverse('manage_operating_points'))
        operating_point.refresh_from_db()
        self.assertFalse(operating_point.is_active)
        reactivate_response = self.client.post(reverse('toggle_operating_point', kwargs={'pk': operating_point.pk}))
        self.assertRedirects(reactivate_response, reverse('manage_operating_points'))
        operating_point.refresh_from_db()
        self.assertTrue(operating_point.is_active)

    def test_get_request_cannot_toggle_operating_point(self):
        operating_point = OperatingPoint.objects.create(code='ROOFTOP', name='Rooftop', is_system_point=False, is_active=True)
        response = self.client.get(reverse('toggle_operating_point', kwargs={'pk': operating_point.pk}))
        self.assertEqual(response.status_code, 403)
        operating_point.refresh_from_db()
        self.assertTrue(operating_point.is_active)

    def test_operating_points_cannot_be_deleted_through_management_interface(self):
        operating_point = OperatingPoint.objects.create(code='ROOFTOP', name='Rooftop', is_system_point=False, is_active=True)
        delete_url = f'/accounts/operating-points/{operating_point.pk}/delete/'
        response = self.client.post(delete_url)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(OperatingPoint.objects.filter(pk=operating_point.pk).exists())

class DeploymentAuthorizationTests(TestCase):

    def setUp(self):
        from .employee_management import _authorize_employee_deployment
        from .models import DeploymentHistory, EmployeeLeave, EmployeeSuspension
        self.authorize_deployment = _authorize_employee_deployment
        self.DeploymentHistory = DeploymentHistory
        self.EmployeeLeave = EmployeeLeave
        self.EmployeeSuspension = EmployeeSuspension
        self.director_role = RoleDefinition.objects.get(code='DIRECTOR')
        self.bar_role = RoleDefinition.objects.get(code='BAR_ATTENDANT')
        self.director = User.objects.create_user(username='deployment-director@example.com', email='deployment-director@example.com', password='StrongDeploymentPass123!', role=User.Role.DIRECTOR, role_definition=self.director_role, is_active=True, is_active_staff=True)
        self.staff_user = User.objects.create_user(username='deployment-staff@example.com', email='deployment-staff@example.com', password='StrongDeploymentPass123!', role=User.Role.BAR_ATTENDANT, role_definition=self.bar_role, is_active=True, is_active_staff=True)
        self.current_point = OperatingPoint.objects.create(code='DEPLOY_BAR', name='Deployment Bar', is_active=True)
        self.target_point = OperatingPoint.objects.create(code='DEPLOY_CHOMA', name='Deployment Choma', is_active=True)
        self.inactive_point = OperatingPoint.objects.create(code='DEPLOY_INACTIVE', name='Inactive Deployment Point', is_active=False)
        self.employee = Employee.objects.create(user=self.staff_user, operating_point=self.current_point, employment_status=Employee.EmploymentStatus.ACTIVE)

    def test_active_employee_deployment_is_allowed(self):
        self.authorize_deployment(self.employee, self.target_point)

    def test_suspended_employee_deployment_is_rejected(self):
        from datetime import timedelta
        from django.utils import timezone
        today = timezone.localdate()
        self.EmployeeSuspension.objects.create(employee=self.employee, start_date=today, end_date=today + timedelta(days=2), reason='Deployment authorization test suspension.', issued_by=self.director)
        with self.assertRaises(PermissionDenied):
            self.authorize_deployment(self.employee, self.target_point)

    def test_on_leave_employee_deployment_is_rejected(self):
        from datetime import timedelta
        from django.utils import timezone
        today = timezone.localdate()
        leave_field = self.EmployeeLeave._meta.get_field('leave_type')
        if leave_field.choices:
            leave_type = leave_field.choices[0][0]
        else:
            leave_type = 'ANNUAL'
        self.EmployeeLeave.objects.create(employee=self.employee, leave_type=leave_type, start_date=today, end_date=today + timedelta(days=2), reason='Deployment authorization test leave.', approved_by=self.director)
        with self.assertRaises(PermissionDenied):
            self.authorize_deployment(self.employee, self.target_point)

    def test_off_duty_employee_deployment_is_rejected(self):
        self.employee.employment_status = Employee.EmploymentStatus.OFF_DUTY
        self.employee.save(update_fields=['employment_status'])
        with self.assertRaises(PermissionDenied):
            self.authorize_deployment(self.employee, self.target_point)

    def test_invited_employee_deployment_is_rejected(self):
        self.employee.employment_status = Employee.EmploymentStatus.INVITED
        self.employee.save(update_fields=['employment_status'])
        with self.assertRaises(PermissionDenied):
            self.authorize_deployment(self.employee, self.target_point)

    def test_terminated_employee_deployment_is_rejected(self):
        self.employee.employment_status = Employee.EmploymentStatus.TERMINATED
        self.employee.save(update_fields=['employment_status'])
        with self.assertRaises(PermissionDenied):
            self.authorize_deployment(self.employee, self.target_point)

    def test_inactive_operating_point_deployment_is_rejected(self):
        with self.assertRaises(PermissionDenied):
            self.authorize_deployment(self.employee, self.inactive_point)

    def test_successful_deployment_creates_history(self):
        self.client.force_login(self.director)
        response = self.client.post(reverse('employee_edit', kwargs={'pk': self.employee.pk}), {'role_definition': self.bar_role.pk, 'employment_status': Employee.EmploymentStatus.ACTIVE, 'date_joined': '', 'operating_point': self.target_point.pk})
        self.assertRedirects(response, reverse('employee_detail', kwargs={'pk': self.employee.pk}))
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.operating_point_id, self.target_point.pk)
        history = self.DeploymentHistory.objects.get(employee=self.employee)
        self.assertEqual(history.from_operating_point_id, self.current_point.pk)
        self.assertEqual(history.to_operating_point_id, self.target_point.pk)
        self.assertEqual(history.changed_by_id, self.director.pk)

    def test_inactive_operating_point_is_rejected_by_employee_edit(self):
        self.client.force_login(self.director)
        response = self.client.post(reverse('employee_edit', kwargs={'pk': self.employee.pk}), {'role_definition': self.bar_role.pk, 'employment_status': Employee.EmploymentStatus.ACTIVE, 'date_joined': '', 'operating_point': self.inactive_point.pk})
        self.assertEqual(response.status_code, 200)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.operating_point_id, self.current_point.pk)
        self.assertFalse(self.DeploymentHistory.objects.filter(employee=self.employee).exists())

    def test_non_director_cannot_change_deployment(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(reverse('employee_edit', kwargs={'pk': self.employee.pk}), {'role_definition': self.bar_role.pk, 'employment_status': Employee.EmploymentStatus.ACTIVE, 'date_joined': '', 'operating_point': self.target_point.pk})
        self.assertEqual(response.status_code, 403)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.operating_point_id, self.current_point.pk)
        self.assertFalse(self.DeploymentHistory.objects.filter(employee=self.employee).exists())
