"""Seed the standard Spotz expense category tree.

Idempotent: uses get_or_create(code=...) so running twice is safe.
Categories can be renamed or deactivated later without this command
recreating them.
"""
from django.core.management.base import BaseCommand

from expenses.models import ExpenseCategory


# (parent_code, child_code, child_name, sort_order)
# parent_code=None means this is a root category.
CATEGORY_TREE = [
    # Root categories
    (None, "UTIL", "Utilities", 10),
    (None, "SEC", "Security", 20),
    (None, "PERS", "Personnel", 30),
    (None, "OPS", "Operations", 40),
    (None, "ENT", "Entertainment", 50),
    (None, "LIC", "Licenses", 60),
    (None, "OTH", "Other", 70),

    # Utilities
    ("UTIL", "UTIL_ELEC", "Electricity", 10),
    ("UTIL", "UTIL_WATER", "Water", 20),
    ("UTIL", "UTIL_WIFI", "Wi-Fi", 30),
    ("UTIL", "UTIL_DSTV", "DSTV", 40),

    # Security
    ("SEC", "SEC_SAL", "Security salaries", 10),
    ("SEC", "SEC_SVC", "Security services", 20),
    ("SEC", "SEC_EQP", "Security equipment", 30),

    # Personnel
    ("PERS", "PERS_SAL", "Salaries", 10),
    ("PERS", "PERS_ALLOW", "Allowances", 20),
    ("PERS", "PERS_CASUAL", "Casual labor", 30),

    # Operations
    ("OPS", "OPS_CLEAN", "Cleaning", 10),
    ("OPS", "OPS_TRANSPORT", "Transport", 20),
    ("OPS", "OPS_REPAIR", "Repairs", 30),
    ("OPS", "OPS_MAINT", "Maintenance", 40),
    ("OPS", "OPS_GAS", "Gas", 50),
    ("OPS", "OPS_CHARCOAL", "Charcoal", 60),
    ("OPS", "OPS_CONSUM", "Consumables", 70),

    # Entertainment
    ("ENT", "ENT_DJ", "DJ", 10),
    ("ENT", "ENT_MUSIC", "Music", 20),
    ("ENT", "ENT_EVENTS", "Events", 30),

    # Licenses
    ("LIC", "LIC_BIZ", "Business licenses", 10),
    ("LIC", "LIC_OTHER", "Other permits", 20),

    # Other
    ("OTH", "OTH_MISC", "Miscellaneous", 10),
]


class Command(BaseCommand):
    help = "Seed the standard expense category tree."

    def handle(self, *args, **options):
        created = 0
        existing = 0
        for parent_code, code, name, order in CATEGORY_TREE:
            parent = None
            if parent_code is not None:
                parent = ExpenseCategory.objects.filter(code=parent_code).first()
                if parent is None:
                    self.stdout.write(self.style.WARNING(
                        "Parent " + parent_code + " missing, skipping " + code))
                    continue
            obj, was_created = ExpenseCategory.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "parent": parent,
                    "sort_order": order,
                },
            )
            if was_created:
                created += 1
                self.stdout.write("  + " + code + " " + name)
            else:
                existing += 1

        self.stdout.write(self.style.SUCCESS(
            "Categories: " + str(created) + " created, "
            + str(existing) + " already existed."))
