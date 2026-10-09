from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from .forms import RoleDefinitionForm
from .models import PermissionDefinition, RoleDefinition, RolePermission


def _director_required(user):
    return bool(
        user
        and user.is_authenticated
        and user.is_active_staff_user
        and user.is_director
    )


def director_role_required(view_func):
    @login_required
    def wrapped_view(request, *args, **kwargs):
        if not _director_required(request.user):
            raise PermissionDenied("Director authorization is required.")
        return view_func(request, *args, **kwargs)

    return wrapped_view


def _build_role_code(name):
    """
    Generate a compatibility-safe role code.

    User.role is currently max_length=30, so dynamic role codes are
    deliberately limited to 30 characters until that legacy field is
    eventually retired.
    """
    code = slugify(name).replace("-", "_").upper()

    if not code:
        return ""

    return code[:30]


@director_role_required
def manage_roles(request):
    roles = RoleDefinition.objects.prefetch_related(
        "role_permissions__permission"
    ).order_by("is_system_role", "name")

    return render(
        request,
        "accounts/manage_roles.html",
        {
            "roles": roles,
        },
    )


@director_role_required
def create_role(request):
    permissions = PermissionDefinition.objects.filter(
        is_active=True
    ).order_by("code")

    if request.method == "POST":
        form = RoleDefinitionForm(request.POST, permissions=permissions)

        if form.is_valid():
            with transaction.atomic():
                name = form.cleaned_data["name"].strip()
                description = form.cleaned_data["description"].strip()
                selected_permissions = form.cleaned_data["permissions"]

                code = _build_role_code(name)

                if not code:
                    form.add_error(
                        "name",
                        "The role name must contain at least one letter or number.",
                    )
                elif RoleDefinition.objects.filter(code=code).exists():
                    form.add_error(
                        "name",
                        f"A role using the code '{code}' already exists.",
                    )
                else:
                    role = RoleDefinition.objects.create(
                        code=code,
                        name=name,
                        description=description,
                        is_system_role=False,
                        is_active=form.cleaned_data["is_active"],
                    )

                    RolePermission.objects.bulk_create(
                        [
                            RolePermission(
                                role=role,
                                permission=permission,
                            )
                            for permission in selected_permissions
                        ]
                    )

                    messages.success(
                        request,
                        f"Role '{role.name}' was created successfully.",
                    )
                    return redirect("manage_roles")

    else:
        form = RoleDefinitionForm(permissions=permissions)

    return render(
        request,
        "accounts/role_form.html",
        {
            "form": form,
            "page_title": "Create Role",
            "submit_label": "Create Role",
        },
    )


@director_role_required
def edit_role(request, role_id):
    role = get_object_or_404(RoleDefinition, pk=role_id)

    permissions = PermissionDefinition.objects.filter(
        is_active=True
    ).order_by("code")

    if request.method == "POST":
        form = RoleDefinitionForm(
            request.POST,
            permissions=permissions,
            instance=role,
        )

        if form.is_valid():
            with transaction.atomic():
                role.name = form.cleaned_data["name"].strip()
                role.description = form.cleaned_data["description"].strip()
                role.is_active = form.cleaned_data["is_active"]

                # System role codes are protected because existing users
                # and authorization logic depend on them.
                if not role.is_system_role:
                    new_code = _build_role_code(role.name)

                    if not new_code:
                        form.add_error(
                            "name",
                            "The role name must contain at least one letter or number.",
                        )
                        return render(
                            request,
                            "accounts/role_form.html",
                            {
                                "form": form,
                                "page_title": "Edit Role",
                                "submit_label": "Save Changes",
                                "role": role,
                            },
                        )

                    duplicate = RoleDefinition.objects.filter(
                        code=new_code
                    ).exclude(pk=role.pk).exists()

                    if duplicate:
                        form.add_error(
                            "name",
                            f"A role using the code '{new_code}' already exists.",
                        )
                        return render(
                            request,
                            "accounts/role_form.html",
                            {
                                "form": form,
                                "page_title": "Edit Role",
                                "submit_label": "Save Changes",
                                "role": role,
                            },
                        )

                    role.code = new_code

                role.save()

                RolePermission.objects.filter(role=role).delete()

                RolePermission.objects.bulk_create(
                    [
                        RolePermission(
                            role=role,
                            permission=permission,
                        )
                        for permission in form.cleaned_data["permissions"]
                    ]
                )

            messages.success(
                request,
                f"Role '{role.name}' was updated successfully.",
            )
            return redirect("manage_roles")

    else:
        form = RoleDefinitionForm(
            permissions=permissions,
            instance=role,
        )

    return render(
        request,
        "accounts/role_form.html",
        {
            "form": form,
            "page_title": "Edit Role",
            "submit_label": "Save Changes",
            "role": role,
        },
    )


@director_role_required
def toggle_role(request, role_id):
    if request.method != "POST":
        raise PermissionDenied("Role status changes must use POST.")

    role = get_object_or_404(RoleDefinition, pk=role_id)

    role.is_active = not role.is_active
    role.save(update_fields=["is_active", "updated_at"])

    status = "activated" if role.is_active else "deactivated"

    messages.success(
        request,
        f"Role '{role.name}' was {status}.",
    )

    return redirect("manage_roles")
