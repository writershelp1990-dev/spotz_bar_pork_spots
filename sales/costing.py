"""Cost of goods sold and profit-and-loss computation.

Weighted-average method: for a given product/operating-point over a
date range, COGS = total_units_sold * average_unit_cost, where
average_unit_cost is the weighted average of every receipt's unit_cost
that contributed to stock on hand during the period.

This is an approximation. It does not track individual cost layers
(FIFO) and assumes cost is stable within the period. For audit-grade
accounting, migrate to a StockLayer model.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum
from django.utils import timezone

from .models import Sale, SaleItem, StockReceipt, StockLedger

CENT = Decimal("0.01")


def _quant(v):
    if v is None:
        return Decimal("0.00")
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def average_unit_cost(*, product, operating_point, since, until):
    """Return the weighted-average unit cost of a product at a point
    over the inclusive date range [since, until].

    Weighted average = sum(receipt.total_cost) / sum(receipt.quantity)
    across every verified receipt for that product at that operating
    point whose received_at date falls in the range.

    Returns Decimal("0.00") when there are no receipts.
    """
    qs = (StockReceipt.objects
          .filter(
              product=product,
              operating_point=operating_point,
              status=StockReceipt.Status.VERIFIED,
              received_at__date__gte=since,
              received_at__date__lte=until,
          ))
    agg = qs.aggregate(
        total_cost=Sum("total_cost"),
        total_qty=Sum("quantity"),
    )
    cost = agg["total_cost"] or Decimal("0")
    qty = agg["total_qty"] or Decimal("0")
    if qty <= 0:
        return Decimal("0.00")
    return _quant(cost / qty)


def pnl_for_range(*, since: date, until: date, operating_point=None):
    """Return a dict of P&L figures for the inclusive date range.

    Revenue: sum of completed sales in the range.
    COGS: weighted-average cost of goods sold, per product.
    Gross profit: revenue minus COGS.
    """
    sale_qs = Sale.objects.filter(
        sold_at__date__gte=since,
        sold_at__date__lte=until,
    )
    if operating_point is not None:
        sale_qs = sale_qs.filter(operating_point=operating_point)

    revenue = sale_qs.aggregate(s=Sum("total"))["s"] or Decimal("0")

    # Per product / per point breakdown
    items = (SaleItem.objects
             .filter(sale__in=sale_qs)
             .values("product", "sale__operating_point")
             .annotate(qty=Sum("quantity"))
             .annotate(line_rev=Sum("line_total")))

    from catalog.models import Product
    from accounts.models import OperatingPoint

    product_map = {p.pk: p for p in Product.objects.filter(
        pk__in={it["product"] for it in items})}
    op_map = {o.pk: o for o in OperatingPoint.objects.filter(
        pk__in={it["sale__operating_point"] for it in items})}

    rows = []
    total_cogs = Decimal("0")
    for it in items:
        p = product_map.get(it["product"])
        op = op_map.get(it["sale__operating_point"])
        if p is None or op is None:
            continue
        qty = it["qty"] or Decimal("0")
        line_rev = it["line_rev"] or Decimal("0")
        avg = average_unit_cost(product=p, operating_point=op,
                                since=since, until=until)
        cogs = _quant(qty * avg)
        gross = _quant(line_rev - cogs)
        rows.append({
            "product": p,
            "operating_point": op,
            "quantity_sold": qty,
            "average_unit_cost": avg,
            "revenue": _quant(line_rev),
            "cogs": cogs,
            "gross_profit": gross,
        })
        total_cogs += cogs

    revenue = _quant(revenue)
    total_cogs = _quant(total_cogs)
    gross = _quant(revenue - total_cogs)
    margin = (gross / revenue) if revenue > 0 else Decimal("0")

    return {
        "since": since,
        "until": until,
        "revenue": revenue,
        "cogs": total_cogs,
        "gross_profit": gross,
        "gross_margin": _quant(margin * 100),
        "rows": sorted(rows, key=lambda r: r["gross_profit"], reverse=True),
    }
