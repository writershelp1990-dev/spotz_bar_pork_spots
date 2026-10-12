from celery import shared_task
from .low_stock import run_due_reminders

@shared_task(name="sales.tasks.send_due_low_stock_reminders")
def send_due_low_stock_reminders():
    run_due_reminders()
