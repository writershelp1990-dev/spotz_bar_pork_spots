from functools import wraps

from django.conf import settings
from django.db.models import Q
from django.contrib import messages
from django.db.models import Q
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.contrib.auth import login, logout, update_session_auth_hash
from django.db.models import Q
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.contrib.auth.forms import PasswordChangeForm
from django.db.models import Q
from django.core import signing
from django.db.models import Q
from django.core.mail import send_mail
from django.db.models import Q
from django.http import Http404
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import Q
from django.urls import reverse
from django.db.models import Q
from django.utils import timezone
from django.db.models import Q

from .forms import (
    CompleteInvitationForm,
    ProfileForm,
    SpotzAuthenticationForm,
    StaffInviteForm,
)
from .models import Employee, User


INVITE_MAX_AGE = 60 * 60 * 24


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = SpotzAuthenticationForm(request, data=request.POST)

        if form.is_valid():
            user = form.get_user()

            if not user.is_active or not user.is_active_staff:
                form.add_error(None, "This account is inactive. Please contact SPOTZ management.")
            else:
                login(request, user)
                return redirect("dashboard")
    else:
        form = SpotzAuthenticationForm(request)

    return render(request, "accounts/login.html", {"form": form})


@login_required
def logout_view(request):
    if request.method == "POST":
        logout(request)
        return redirect("login")

    return render(request, "accounts/logout_confirm.html")


@login_required
def profile_view(request):
    employee = getattr(request.user, "employee_profile", None)

    if employee is None:
        employee = Employee.objects.create(
            user=request.user,
        )

    if request.method == "POST":
        form = ProfileForm(request.POST, instance=employee, user=request.user)

        if form.is_valid():
            form.save()
            messages.success(request, "Your profile has been updated.")
            return redirect("profile")
    else:
        form = ProfileForm(instance=employee, user=request.user)

    return render(request, "accounts/profile.html", {"form": form})


@login_required
def password_change_view(request):
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)

        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Your password has been changed successfully.")
            return redirect("profile")
    else:
        form = PasswordChangeForm(request.user)

    return render(request, "accounts/password_change.html", {"form": form})



def user_has_permission(user, permission_code):
    """
    Central server-side authorization helper.

    Navigation visibility is never treated as authorization. Every protected
    view must enforce authorization on the server.
    """
    if not user or not user.is_authenticated:
        return False

    return user.user_has_permission(permission_code)


def _director_required(user):
    return bool(
        user
        and user.is_authenticated
        and user.is_active_staff_user
        and user.is_director
    )


def director_required(view_func):
    """
    Director-only server-side view protection.
    """

    @wraps(view_func)
    @login_required
    def wrapped_view(request, *args, **kwargs):
        if not _director_required(request.user):
            raise PermissionDenied("Director authorization is required.")
        return view_func(request, *args, **kwargs)

    return wrapped_view

@director_required

@login_required
def staff_directory(request):
    """Director-only Staff Directory for Phase 1 / 03A."""
    if not request.user.is_director:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    queryset = User.objects.all().select_related("role_definition")

    status = request.GET.get("status", "").strip().upper()
    role_code = request.GET.get("role", "").strip()
    search = request.GET.get("q", "").strip()

    if status:
        queryset = queryset.filter(employment_status=status)

    if role_code:
        queryset = queryset.filter(role_definition__code=role_code)

    if search:
        queryset = queryset.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
            | Q(phone__icontains=search)
            | Q(employee_number__icontains=search)
        )

    status_counts = {
        "INVITED": User.objects.filter(employee_profile__employment_status=Employee.EmploymentStatus.INVITED).count(),
        "ACTIVE": User.objects.filter(employee_profile__employment_status=Employee.EmploymentStatus.ACTIVE).count(),
        "OFF_DUTY": User.objects.filter(employee_profile__employment_status=Employee.EmploymentStatus.OFF_DUTY).count(),
        "ON_LEAVE": User.objects.filter(employee_profile__employment_status=Employee.EmploymentStatus.ON_LEAVE).count(),
        "SUSPENDED": User.objects.filter(employee_profile__employment_status=Employee.EmploymentStatus.SUSPENDED).count(),
        "TERMINATED": User.objects.filter(employee_profile__employment_status=Employee.EmploymentStatus.TERMINATED).count(),
    }

    roles = (
        User.objects
        .exclude(role_definition__isnull=True)
        .values("role_definition__code", "role_definition__name")
        .distinct()
        .order_by("role_definition__name")
    )

    context = {
        "staff_members": queryset.order_by("last_name", "first_name", "email"),
        "status_counts": status_counts,
        "roles": roles,
        "selected_status": status,
        "selected_role": role_code,
        "search_query": search,
    }

    return render(request, "accounts/staff_directory.html", context)


@login_required
def staff_profile(request, pk):
    """Director-only Staff Profile for Phase 1 / 03A."""
    if not request.user.is_director:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    staff_member = get_object_or_404(
        User.objects.select_related("role_definition"),
        pk=pk,
    )

    return render(
        request,
        "accounts/staff_profile.html",
        {"staff_member": staff_member},
    )

@director_required
def manage_staff(request):
    """
    Director-only staff management landing page.

    Phase 1 foundation only: this establishes the protected entry point.
    Detailed employee lifecycle actions remain within the approved Phase 1
    employee-management scope and are not expanded into Phase 2 functionality.
    """
    return render(request, "accounts/manage_staff.html")
@director_required
def invite_staff(request):
    if request.method == "POST":
        form = StaffInviteForm(request.POST)

        if form.is_valid():
            full_name = form.cleaned_data["full_name"].strip()
            email = form.cleaned_data["email"]
            role_definition = form.cleaned_data["role_definition"]
            operating_point = form.cleaned_data["operating_point"]

            name_parts = full_name.split(maxsplit=1)
            first_name = name_parts[0]
            last_name = name_parts[1] if len(name_parts) > 1 else ""

            user = User.objects.create(
                username=email,
                email=email,
                first_name=first_name,
                last_name=last_name,
                role=role_definition.code,
        role_definition=role_definition,
                is_active=True,
                is_active_staff=True,
            )
            user.set_unusable_password()
            user.save()

            Employee.objects.create(
                user=user,
                operating_point=operating_point,
            )

            token = signing.dumps(
                {"uid": user.pk, "email": user.email},
                salt="spotz-staff-invitation",
            )

            invite_url = request.build_absolute_uri(
                reverse("complete_invitation", kwargs={"token": token})
            )

            subject = "SPOTZ BAR & PORK SPOTS - Complete your account"
            message = (
                f"Hello {first_name},\n\n"
                "You have been invited to join the SPOTZ Management System.\n\n"
                "Complete your account using the secure link below:\n\n"
                f"{invite_url}\n\n"
                "This invitation expires automatically after 24 hours.\n"
                "If you did not expect this invitation, please ignore this email.\n\n"
                "SPOTZ BAR & PORK SPOTS"
            )

            send_mail(
                subject,
                message,
                getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@spotz.local"),
                [user.email],
                fail_silently=False,
            )

            request.session["invite_success"] = {
                "full_name": full_name,
                "email": user.email,
                "role": role_definition.name,
                "operating_point": operating_point.name,
            }
            return redirect("invitation_success")
    else:
        form = StaffInviteForm()

    return render(request, "accounts/invite_staff.html", {"form": form})


def invitation_success(request):
    invitation = request.session.pop("invite_success", None)

    if not invitation:
        return redirect("invite_staff")

    return render(
        request,
        "accounts/invite_success.html",
        {"invitation": invitation},
    )


def complete_invitation(request, token):
    try:
        data = signing.loads(
            token,
            salt="spotz-staff-invitation",
            max_age=INVITE_MAX_AGE,
        )
    except signing.SignatureExpired:
        return render(request, "accounts/invitation_expired.html", status=410)
    except signing.BadSignature:
        raise Http404("Invalid invitation link.")

    user = get_object_or_404(User, pk=data["uid"], email=data["email"])

    if user.has_usable_password():
        return render(request, "accounts/invitation_used.html", status=410)

    employee = get_object_or_404(Employee, user=user)

    if request.method == "POST":
        form = CompleteInvitationForm(request.POST)

        if form.is_valid():
            user.set_password(form.cleaned_data["password1"])
            user.is_active = True
            user.is_active_staff = True
            user.save(update_fields=["password", "is_active", "is_active_staff"])

            employee.phone = form.cleaned_data["phone"].strip()
            employee.whatsapp_number = form.cleaned_data["whatsapp_number"].strip()
            employee.mpesa_number = form.cleaned_data["mpesa_number"].strip()
            employee.employment_status = employee.EmploymentStatus.ACTIVE
            employee.save(
                update_fields=[
                    "phone",
                    "whatsapp_number",
                    "mpesa_number",
                    "updated_at",
                ]
            )

            login(request, user)
            messages.success(request, "Your SPOTZ account has been activated.")
            return redirect("dashboard")
    else:
        form = CompleteInvitationForm()

    return render(
        request,
        "accounts/complete_invitation.html",
        {
            "form": form,
            "user_email": user.email,
            "employee": employee,
        },
    )






# ------------------------------------------------------------
# SECURE FORGOT PASSWORD FLOW
# ------------------------------------------------------------

from django.contrib.auth import views as auth_views
from django.db.models import Q
from django.core.cache import cache
from django.db.models import Q
from django.urls import reverse_lazy
from django.db.models import Q


class SpotzPasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    def post(self, request, *args, **kwargs):
        email = request.POST.get("email", "").strip().lower()

        # Short cooldown to reduce repeated reset requests.
        # We intentionally do not reveal whether an account exists.
        cooldown_key = f"spotz-password-reset:{email}"

        if email and cache.get(cooldown_key):
            return redirect(self.success_url)

        if email:
            cache.set(cooldown_key, True, timeout=60)

        return super().post(request, *args, **kwargs)


class SpotzPasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class SpotzPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")


class SpotzPasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"





