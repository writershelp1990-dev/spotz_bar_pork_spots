"""Projection algorithm — pure functions plus DB-backed history lookups."""
from decimal import Decimal

from django.db.models import Sum


# Recency weights per history length (oldest first, newest last → highest weight)
_RECENCY_WEIGHTS = {
    1: [Decimal("1.0")],
    2: [Decimal("0.40"), Decimal("0.60")],
    3: [Decimal("0.25"), Decimal("0.30"), Decimal("0.45")],
    4: [Decimal("0.15"), Decimal("0.20"), Decimal("0.30"), Decimal("0.35")],
    5: [Decimal("0.10"), Decimal("0.15"), Decimal("0.20"), Decimal("0.25"), Decimal("0.30")],
}


def _weights_for(n):
    """Return a list of n weights summing to 1.0, newest getting the most."""
    if n <= 0:
        return []
    if n in _RECENCY_WEIGHTS:
        return _RECENCY_WEIGHTS[n]
    # Linear ramp for n >= 6
    base = [Decimal(i + 1) for i in range(n)]
    total = sum(base, Decimal("0"))
    return [(w / total) for w in base]


def weighted_mean(values):
    """values ordered oldest → newest. Returns a Decimal rounded to 2dp."""
    if not values:
        return Decimal("0.00")
    weights = _weights_for(len(values))
    total = sum((v * w for v, w in zip(values, weights)), Decimal("0"))
    return total.quantize(Decimal("0.01"))


def trim_outliers(values):
    """Drop the single highest and single lowest, if there are >= 4 values."""
    if len(values) < 4:
        return list(values)
    s = sorted(values)
    return s[1:-1]


def _same_weekday_history(point, target_date, limit):
    """Return list of (date, Decimal) for the last `limit` same-weekday sales
    at `point`, most recent first, excluding target_date itself."""
    from .models import Sale
    # Django's week_day lookup: Sunday=1, Monday=2, ..., Saturday=7
    django_weekday = (target_date.weekday() + 1) % 7 + 1
    qs = (
        Sale.objects
        .filter(
            operating_point=point,
            payment_status=Sale.PaymentStatus.PAID,
            business_day__business_date__week_day=django_weekday,
            business_day__business_date__lt=target_date,
        )
        .values("business_day__business_date")
        .annotate(total=Sum("total"))
        .order_by("-business_day__business_date")[:limit]
    )
    return [(row["business_day__business_date"], row["total"]) for row in qs]


def _holiday_history(point, target_date, limit):
    """Return list of (date, Decimal) for the last `limit` holiday sales at `point`."""
    from business.business_day_models import BusinessHoliday
    from .models import Sale
    holiday_dates = list(
        BusinessHoliday.objects
        .filter(is_active=True, date__lt=target_date)
        .values_list("date", flat=True)
    )
    if not holiday_dates:
        return []
    qs = (
        Sale.objects
        .filter(
            operating_point=point,
            payment_status=Sale.PaymentStatus.PAID,
            business_day__business_date__in=holiday_dates,
        )
        .values("business_day__business_date")
        .annotate(total=Sum("total"))
        .order_by("-business_day__business_date")[:limit]
    )
    return [(row["business_day__business_date"], row["total"]) for row in qs]


def compute_projection_for(target_date, point, config):
    """Return (amount, source_days, source_data) for one (date, point).

    amount: Decimal rounded to 2dp
    source_days: how many historical values were used
    source_data: list of {"date": str, "amount": str} for auditability
    """
    from business.business_day_models import BusinessHoliday

    limit = config.history_days
    history = []

    if config.holiday_adjustment:
        is_holiday = BusinessHoliday.objects.filter(
            date=target_date, is_active=True).exists()
        if is_holiday:
            history = _holiday_history(point, target_date, limit)
            if len(history) < 3:
                history = _same_weekday_history(point, target_date, limit)

    if not history:
        history = _same_weekday_history(point, target_date, limit)

    if len(history) < 3:
        return (config.cold_start_fallback, 0, [])

    values_oldest_first = [amt for _, amt in reversed(history)]
    if config.outlier_trim:
        values_oldest_first = trim_outliers(values_oldest_first)

    amount = weighted_mean(values_oldest_first)
    source_data = [{"date": str(d), "amount": str(a)} for d, a in history]
    return (amount, len(history), source_data)
