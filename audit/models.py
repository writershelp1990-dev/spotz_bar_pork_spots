from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        DELETE = "DELETE", "Delete"
        LOGIN = "LOGIN", "Login"
        LOGOUT = "LOGOUT", "Logout"
        VOID = "VOID", "Void"
        APPROVE = "APPROVE", "Approve"
        REJECT = "REJECT", "Reject"
        ADJUST = "ADJUST", "Adjust"
        CLOSE = "CLOSE", "Close"
        REOPEN = "REOPEN", "Reopen"
        OTHER = "OTHER", "Other"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audit_logs",
        null=True,
        blank=True,
    )

    action = models.CharField(
        max_length=20,
        choices=Action.choices,
    )

    model_name = models.CharField(
        max_length=100,
    )

    object_id = models.CharField(
        max_length=100,
        blank=True,
    )

    object_repr = models.CharField(
        max_length=255,
        blank=True,
    )

    operating_point = models.ForeignKey(
        "accounts.OperatingPoint",
        on_delete=models.PROTECT,
        related_name="audit_logs",
        null=True,
        blank=True,
    )

    reason = models.TextField(
        blank=True,
    )

    changes = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Structured record of important field changes. "
            "Never use this field to replace the original transaction record."
        ),
    )

    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
    )

    user_agent = models.TextField(
        blank=True,
    )

    timestamp = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-timestamp", "-id"]

        indexes = [
            models.Index(
                fields=["user", "-timestamp"],
                name="audit_user_date_idx",
            ),
            models.Index(
                fields=["action", "-timestamp"],
                name="audit_action_date_idx",
            ),
            models.Index(
                fields=["model_name", "object_id"],
                name="audit_model_object_idx",
            ),
            models.Index(
                fields=["operating_point", "-timestamp"],
                name="audit_point_date_idx",
            ),
        ]

    def __str__(self):
        username = self.user.username if self.user else "SYSTEM"

        return (
            f"{self.timestamp:%Y-%m-%d %H:%M:%S} - "
            f"{username} - "
            f"{self.action} - "
            f"{self.model_name} - "
            f"{self.object_id or '-'}"
        )

    def delete(self, *args, **kwargs):
        """
        Audit records are intentionally protected from normal deletion.

        Historical audit information must remain available for
        accountability and investigation.
        """
        raise PermissionError(
            "AuditLog records are append-only and cannot be deleted."
        )

    def save(self, *args, **kwargs):
        """
        AuditLog is intended to be append-only.

        Existing audit entries cannot be modified through normal save().
        """
        if self.pk:
            raise PermissionError(
                "Existing AuditLog records cannot be modified."
            )

        super().save(*args, **kwargs)
