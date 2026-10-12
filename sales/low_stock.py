"""Smart low-stock alerting. Evaluation + 12h reminders."""
from __future__ import annotations
import logging
from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone
from accounts.models import Employee, User
from .models import (
    StockAlert, StockAlertNotification, StockAlertRule, StockBalance,
)
log = logging.getLogger(__name__)
REMINDER_INTERVAL = timedelta(hours=12)


def _directors():
    return list(User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(role=User.Role.DIRECTOR)
    ).distinct())


def _attendants_for(point):
    return list(User.objects.filter(
        is_active=True, is_active_staff=True,
        employee_profile__operating_point=point,
        employee_profile__employment_status__in=[
            Employee.EmploymentStatus.ACTIVE,
            Employee.EmploymentStatus.OFF_DUTY,
        ],
    ).distinct())


def _recipients_for(point):
    seen, out = set(), []
    for u in _directors() + _attendants_for(point):
        if u.pk not in seen:
            seen.add(u.pk); out.append(u)
    return out


def _describe_product(product):
    parts = []
    if product.brand: parts.append(product.brand)
    if product.variety: parts.append(product.variety)
    parts.append(product.name)
    tail = []
    if product.size_display: tail.append(product.size_display)
    if product.packaging: tail.append(product.packaging)
    if tail: parts.append(" ".join(tail))
    if product.code: parts.append(f"SKU {product.code}")
    return ", ".join(parts)


def _describe_shortage(alert, balance):
    rule = alert.rule
    suggested = rule.target_quantity - balance
    if suggested < 0: suggested = Decimal("0")
    unit = alert.product.selling_unit.abbreviation if alert.product.selling_unit_id else ""
    return {
        "product_line": _describe_product(alert.product),
        "category": str(alert.product.category) if alert.product.category_id else "",
        "operating_point": alert.operating_point.name,
        "current": balance, "unit": unit,
        "minimum": rule.minimum_quantity, "target": rule.target_quantity,
        "suggested": suggested,
    }


def _send_alert_email(alert, balance, recipients, *, is_reminder):
    if not recipients: return
    ctx = _describe_shortage(alert, balance)
    ctx["is_reminder"] = is_reminder
    ctx["alert"] = alert
    subject_prefix = "REMINDER: " if is_reminder else ""
    subject = f"{subject_prefix}LOW STOCK: {ctx['product_line']} @ {ctx['operating_point']}"
    body = render_to_string("sales/emails/low_stock_alert.txt", ctx)
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL,
              [u.email for u in recipients if u.email], fail_silently=False)


def _create_popups(alert, recipients):
    for user in recipients:
        StockAlertNotification.objects.get_or_create(alert=alert, recipient=user)


@transaction.atomic
def evaluate_after_stock_change(*, product, operating_point, actor=None):
    queue = []
    balance_row = (StockBalance.objects.select_for_update()
                   .filter(product=product, operating_point=operating_point).first())
    balance = balance_row.quantity if balance_row else Decimal("0")
    rule = (StockAlertRule.objects
            .filter(product=product, operating_point=operating_point, is_active=True).first())
    if rule is None:
        return
    active = (StockAlert.objects.select_for_update()
              .filter(product=product, operating_point=operating_point,
                      status=StockAlert.Status.ACTIVE).first())
    below_min = balance <= rule.minimum_quantity
    if below_min and active is None:
        alert = StockAlert.objects.create(
            rule=rule, product=product, operating_point=operating_point,
            status=StockAlert.Status.ACTIVE,
            triggered_balance=balance,
            minimum_at_trigger=rule.minimum_quantity,
            target_at_trigger=rule.target_quantity,
        )
        recipients = _recipients_for(operating_point)
        _create_popups(alert, recipients)
        now = timezone.now()
        alert.last_notified_at = now
        alert.next_reminder_at = now + REMINDER_INTERVAL
        alert.save(update_fields=["last_notified_at", "next_reminder_at"])
        queue.append((alert.pk, False))
    elif below_min and active is not None:
        if (active.minimum_at_trigger != rule.minimum_quantity
                or active.target_at_trigger != rule.target_quantity):
            active.minimum_at_trigger = rule.minimum_quantity
            active.target_at_trigger = rule.target_quantity
            active.save(update_fields=["minimum_at_trigger", "target_at_trigger"])
    elif (not below_min) and active is not None:
        active.status = StockAlert.Status.RESOLVED
        active.resolved_at = timezone.now()
        active.resolved_balance = balance
        active.next_reminder_at = None
        active.save(update_fields=["status", "resolved_at",
                                   "resolved_balance", "next_reminder_at"])
        active.notifications.all().delete()
    transaction.on_commit(lambda q=queue: _send_queued_alerts(q))
@transaction.atomic
def run_due_reminders():
    queue = []
    now = timezone.now()
    due = (StockAlert.objects.select_for_update(skip_locked=True)
           .filter(status=StockAlert.Status.ACTIVE, next_reminder_at__lte=now)
           .select_related("product", "product__category", "product__selling_unit",
                           "operating_point", "rule"))
    for alert in due:
        balance = (StockBalance.objects
                   .filter(product=alert.product, operating_point=alert.operating_point)
                   .values_list("quantity", flat=True).first()) or Decimal("0")
        if balance > alert.rule.minimum_quantity:
            alert.status = StockAlert.Status.RESOLVED
            alert.resolved_at = now
            alert.resolved_balance = balance
            alert.next_reminder_at = None
            alert.save(update_fields=["status", "resolved_at",
                                      "resolved_balance", "next_reminder_at"])
            alert.notifications.all().delete()
            continue
        # Do not recreate acknowledged pop-ups during email reminders.
        # The active shortage remains tracked and reminder emails continue.
        alert.last_notified_at = now
        alert.next_reminder_at = now + REMINDER_INTERVAL
        alert.save(update_fields=["last_notified_at", "next_reminder_at"])
        queue.append((alert.pk, True))
    transaction.on_commit(lambda q=queue: _send_queued_alerts(q))
def _send_queued_alerts(queue):
    """Send emails AFTER the enclosing transaction commits, so SMTP
    latency never holds select_for_update locks on StockBalance/StockAlert."""
    for alert_id, is_reminder in queue:
        try:
            alert = (StockAlert.objects
                     .select_related("product", "product__category",
                                     "product__selling_unit",
                                     "operating_point", "rule")
                     .get(pk=alert_id))
            balance = (StockBalance.objects
                       .filter(product=alert.product,
                               operating_point=alert.operating_point)
                       .values_list("quantity", flat=True).first()) or Decimal("0")
            recipients = _recipients_for(alert.operating_point)
            _send_alert_email(alert, balance, recipients, is_reminder=is_reminder)
        except Exception:
            log.exception("Low-stock email failed for alert %s", alert_id)
