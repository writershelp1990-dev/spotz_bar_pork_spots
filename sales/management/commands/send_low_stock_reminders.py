from django.core.management.base import BaseCommand
from sales.low_stock import run_due_reminders


class Command(BaseCommand):
    help = "Send any due low-stock reminders (pop-ups + emails)."

    def handle(self, *args, **options):
        run_due_reminders()
        self.stdout.write(self.style.SUCCESS("Low-stock reminders processed."))
