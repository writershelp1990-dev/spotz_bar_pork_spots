"""Compute daily sales projections for the next N days."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import OperatingPoint
from sales.models import ProjectionConfig, SalesProjection
from sales.projections import compute_projection_for


class Command(BaseCommand):
    help = "Compute daily sales projections for the next N days"

    def add_arguments(self, parser):
        parser.add_argument("--days-ahead", type=int, default=7,
                            help="How many future days to project (1-30). Default 7.")
        parser.add_argument("--point", type=str, default="",
                            help="Only compute for this operating point code.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Print computed values without writing.")
        parser.add_argument("--verbose", action="store_true",
                            help="Print each computed projection.")

    def handle(self, *args, **options):
        config = ProjectionConfig.get_solo()
        days_ahead = max(1, min(int(options["days_ahead"]), 30))
        point_code = (options["point"] or "").strip()
        dry_run = options["dry_run"]
        verbose = options["verbose"]

        points = OperatingPoint.objects.filter(is_active=True).order_by("code")
        if point_code:
            points = points.filter(code__iexact=point_code)

        if not points.exists():
            self.stdout.write(self.style.WARNING("No active points matched."))
            return

        today = timezone.localdate()
        created = 0
        updated = 0
        skipped = 0

        for point in points:
            for i in range(1, days_ahead + 1):
                target = today + timedelta(days=i)

                existing = SalesProjection.objects.filter(
                    date=target, operating_point=point).first()
                if existing and existing.is_override:
                    skipped += 1
                    if verbose:
                        msg = "  [skip-override] " + point.code + " " + str(target)
                        self.stdout.write(msg)
                    continue

                amount, source_days, source_data = compute_projection_for(
                    target, point, config)

                if dry_run:
                    msg = ("  [DRY] " + point.code + " " + str(target)
                           + ": " + str(amount)
                           + " (from " + str(source_days) + " source days)")
                    self.stdout.write(msg)
                    continue

                obj, was_created = SalesProjection.objects.update_or_create(
                    date=target,
                    operating_point=point,
                    defaults={
                        "projected_amount": amount,
                        "is_override": False,
                        "source_days": source_days,
                        "source_data": source_data,
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

                if verbose:
                    msg = ("  " + point.code + " " + str(target)
                           + ": " + str(amount)
                           + " (from " + str(source_days) + " source days)")
                    self.stdout.write(msg)

        if dry_run:
            total = days_ahead * points.count()
            self.stdout.write(self.style.SUCCESS(
                "Dry run complete. Would write " + str(total) + " rows."))
        else:
            summary = ("Projections: " + str(created) + " created, "
                       + str(updated) + " updated, "
                       + str(skipped) + " skipped (overrides).")
            self.stdout.write(self.style.SUCCESS(summary))
