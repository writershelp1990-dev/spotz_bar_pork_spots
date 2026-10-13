"""Signals keeping StaffDeployment in sync with Employee assignment and
BusinessDay lifecycle. Both triggers are idempotent.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import Employee
from business.business_day_models import BusinessDay
from business.services import auto_deploy_employee, auto_deploy_all_for_day

log = logging.getLogger(__name__)


@receiver(post_save, sender=Employee)
def employee_post_save(sender, instance, created, **kwargs):
    if instance.operating_point_id is None:
        return

    def _deploy():
        try:
            auto_deploy_employee(instance)
        except Exception:
            log.exception(
                "auto_deploy_employee failed for employee pk=%s", instance.pk)

    transaction.on_commit(_deploy)


@receiver(post_save, sender=BusinessDay)
def business_day_post_save(sender, instance, created, **kwargs):
    if instance.status != BusinessDay.Status.OPEN:
        return

    def _sweep():
        try:
            auto_deploy_all_for_day(instance)
        except Exception:
            log.exception(
                "auto_deploy_all_for_day failed for business_day pk=%s",
                instance.pk)

    transaction.on_commit(_sweep)
