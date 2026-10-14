"""Generate PENDING Expense rows from RecurringExpense templates.

Run daily (or on-demand) to create the bills that have come due.
Idempotent: a template cannot generate two expenses for the same
due date. Override dates with --up-to for testing / backfill.
"""
import calendar
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from expenses.models import Expense, RecurringExpense


def advance_due_date(current, frequency, due_day):
    """Return the next occurrence after `current` for a given frequency.

    current: a date (the previous due date)
    frequency: one of RecurringExpense.Frequency values
    due_day: day of month (1-31) for monthly/quarterly/annual,
             or day of week (0=Mon .. 6=Sun) for weekly
    """
    if frequency == RecurringExpense.Frequency.WEEKLY:
        delta = (due_day - current.weekday()) % 7
        if delta == 0:
            delta = 7
        return current + timedelta(days=delta)

    if frequency == RecurringExpense.Frequency.MONTHLY:
        year, month = current.year, current.month + 1
        if month > 12:
            month = 1
            year += 1
        last = calendar.monthrange(year, month)[1]
        return date(year, month, min(due_day, last))

    if frequency == RecurringExpense.Frequency.QUARTERLY:
        year, month = current.year, current.month + 3
        while month > 12:
            month -= 12
            year += 1
        last = calendar.monthrange(year, month)[1]
        return date(year, month, min(due_day, last))

    if frequency == RecurringExpense.Frequency.ANNUAL:
        year = current.year + 1
        last = calendar.monthrange(year, current.month)[1]
        return date(year, current.month, min(due_day, last))

    raise ValueError("Unknown frequency: " + str(frequency))


class Command(BaseCommand):
    help = "Generate PENDING expenses from due recurring templates."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Print what would be generated, write nothing.")
        parser.add_argument("--up-to", type=str, default="",
                            help="Simulate today as YYYY-MM-DD (default: actual today).")
        parser.add_argument("--verbose", action="store_true",
                            help="Print each generated expense.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        verbose = options["verbose"]
        up_to_str = (options.get("up_to") or "").strip()

        if up_to_str:
            try:
                today = date.fromisoformat(up_to_str)
            except (TypeError, ValueError):
                self.stderr.write("Invalid --up-to date. Use YYYY-MM-DD.")
                return
        else:
            today = timezone.localdate()

        templates = list(
            RecurringExpense.objects
            .filter(is_active=True, next_due_date__lte=today)
            .select_related("category", "operating_point")
            .order_by("next_due_date", "id")
        )

        total_generated = 0
        total_skipped = 0
        total_advanced = 0

        for template in templates:
            # Cache related objects as locals — accessing FKs after a save()
            # inside a transaction can trigger a fresh query and deadlock.
            _category = template.category
            _point = template.operating_point
            _payee = template.payee
            _description = template.description
            _reference = template.reference
            _payment_method = template.payment_method
            _amount = template.amount
            _frequency = template.frequency
            _due_day = template.due_day
            _entered_by = template.created_by

            # Loop in case the template is several periods behind
            while template.next_due_date <= today:
                # Respect end_date
                if template.end_date and template.next_due_date > template.end_date:
                    if not dry_run:
                        template.is_active = False
                        template.save(update_fields=["is_active", "updated_at"])
                    total_skipped += 1
                    break

                # Idempotency: already generated for this due date?
                already = Expense.objects.filter(
                    recurring_source=template,
                    expense_date=template.next_due_date,
                ).exists()

                if already:
                    # Just advance the schedule, no new expense
                    new_due = advance_due_date(
                        template.next_due_date,
                        template.frequency,
                        template.due_day,
                    )
                    if not dry_run:
                        template.next_due_date = new_due
                        template.save(update_fields=["next_due_date", "updated_at"])
                    total_advanced += 1
                    continue

                if dry_run:
                    self.stdout.write(
                        "  [DRY] " + str(_category)
                        + " " + str(template.next_due_date)
                        + " " + str(template.amount))
                else:
                    Expense.objects.create(
                        category=_category,
                        amount=_amount,
                        expense_date=template.next_due_date,
                        payment_method=_payment_method,
                        payee=_payee,
                        description=_description,
                        reference=_reference,
                        operating_point=_point,
                        entered_by=_entered_by,
                        status=Expense.Status.PENDING,
                        recurring_source=template,
                    )
                    new_due = advance_due_date(
                        template.next_due_date,
                        template.frequency,
                        template.due_day,
                    )
                    template.last_generated_on = today
                    template.next_due_date = new_due
                    template.save(update_fields=[
                        "last_generated_on", "next_due_date", "updated_at",
                    ])
                    total_generated += 1
                    if verbose:
                        self.stdout.write(
                            "  generated: " + str(_category)
                            + " " + str(template.next_due_date))

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                "Dry run complete. Would generate " + str(total_generated)
                + " expense(s), skip " + str(total_skipped)
                + ", advance " + str(total_advanced) + " already-generated slot(s)."))
        else:
            self.stdout.write(self.style.SUCCESS(
                "Generated " + str(total_generated) + " expense(s), "
                + "skipped " + str(total_skipped)
                + ", advanced " + str(total_advanced) + " already-generated slot(s)."))
