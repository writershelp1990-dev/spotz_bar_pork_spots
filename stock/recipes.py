"""Recipe and production services.

A Recipe attaches ingredient consumption rules to a sellable catalog
product. Products with a Recipe are made-to-order: at checkout, the
recipe's ingredients are deducted instead of the finished product.

A ProductionBatch records a preparation event and consumes the
recipe's ingredients from the source point's stock, writing
PRODUCTION_OUT StockLedger rows.

`consume_recipe_ingredients` is the hook that sales.services.checkout
will call in Stage 6. It is delivered here but not yet wired in.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from catalog.models import Product
from sales.models import StockBalance, StockLedger
from sales.low_stock import evaluate_after_stock_change

from .models import ProductionBatch, ProductionConsumption, Recipe, RecipeItem
from .permissions import is_director, user_can_act_at_point

THREE = Decimal("0.001")


# ===========================================================================
# Helpers
# ===========================================================================
def _quantize(quantity) -> Decimal:
    try:
        q = Decimal(str(quantity)).quantize(THREE)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Quantity must be a valid number.")
    if q <= 0:
        raise ValidationError("Quantity must be greater than zero.")
    return q


def _lock_balances(product_point_pairs):
    """Lock a set of StockBalance rows in stable pk order.

    Input: list of (product, operating_point) tuples.
    Output: dict {(product_id, point_id): StockBalance}.
    Creates missing rows first, then re-fetches under select_for_update
    in ascending pk order to prevent deadlocks.
    """
    # Create-or-get all first
    created_pks = []
    for product, point in product_point_pairs:
        balance, _ = StockBalance.objects.get_or_create(
            product=product, operating_point=point,
            defaults={"quantity": Decimal("0")},
        )
        created_pks.append(balance.pk)

    # Lock in stable order
    locked = list(
        StockBalance.objects.select_for_update()
        .filter(pk__in=created_pks)
        .order_by("pk")
    )
    return {(b.product_id, b.operating_point_id): b for b in locked}


# ===========================================================================
# Recipes
# ===========================================================================
@transaction.atomic
def create_recipe(*, product, name, items, user):
    """Create a Recipe for a sellable product, with the given ingredients.

    items: iterable of dicts with keys:
        ingredient (Product instance)
        quantity   (Decimal-able)
        unit       (UnitOfMeasure instance) — usually ingredient.selling_unit

    Director-only. Rejects if the product already has a recipe, or if any
    ingredient isn't active and inventory-tracked.
    """
    if not is_director(user):
        raise PermissionDenied("Only the Director may create recipes.")

    product = Product.objects.select_for_update().get(pk=product.pk)
    if not product.is_active:
        raise ValidationError("Cannot attach a recipe to an inactive product.")
    if hasattr(product, "recipe"):
        raise ValidationError(
            f"{product.display_name} already has a recipe. Edit it instead."
        )
    if not items:
        raise ValidationError("A recipe must have at least one ingredient.")

    recipe = Recipe.objects.create(
        product=product,
        name=name or product.display_name,
        is_active=True,
    )

    seen_ingredients = set()
    for item in items:
        ing = item["ingredient"]
        if ing.pk in seen_ingredients:
            raise ValidationError(
                f"Ingredient {ing.display_name} appears more than once."
            )
        seen_ingredients.add(ing.pk)

        if not ing.is_active or not ing.track_inventory:
            raise ValidationError(
                f"Ingredient {ing.display_name} must be active and "
                f"inventory-tracked."
            )
        qty = _quantize(item["quantity"])
        unit = item.get("unit") or ing.selling_unit
        RecipeItem.objects.create(
            recipe=recipe, ingredient=ing, quantity=qty, unit=unit,
        )

    record_audit(
        user=user, action=AuditLog.Action.CREATE, obj=recipe,
        reason=f"Recipe created for {product.display_name}",
        changes={
            "product": product.name,
            "ingredients": [
                {"name": it.ingredient.name,
                 "quantity": str(it.quantity),
                 "unit": it.unit.abbreviation}
                for it in recipe.items.all()
            ],
        },
    )
    return recipe


# ===========================================================================
# Production batches
# ===========================================================================
@transaction.atomic
def create_production_batch(*, user, recipe, source_point, preparation_point,
                            quantity_produced, notes=""):
    """Create a ProductionBatch and consume its ingredients atomically.

    `source_point` is where the ingredients come from (usually the kitchen
    or choma's own balance for ingredients already allocated to it).
    `preparation_point` is where the preparation happens.
    `quantity_produced` is the number of finished units (plates/portions)
    produced by this batch.

    Ingredient consumption = RecipeItem.quantity * quantity_produced.

    Rejects if any ingredient's balance at source_point is insufficient.
    On rejection, nothing is written and no balance changes.
    """
    if not user_can_act_at_point(user, source_point):
        raise PermissionDenied(
            f"You are not authorized to prepare food at {source_point.code}."
        )
    if not user_can_act_at_point(user, preparation_point):
        raise PermissionDenied(
            f"You are not authorized for preparation at "
            f"{preparation_point.code}."
        )

    recipe = Recipe.objects.select_related("product").get(pk=recipe.pk)
    if not recipe.is_active:
        raise ValidationError("This recipe is inactive.")

    produced = _quantize(quantity_produced)
    recipe_items = list(
        recipe.items.select_related("ingredient", "unit").order_by("pk")
    )
    if not recipe_items:
        raise ValidationError("This recipe has no ingredients.")

    # Compute total consumption per ingredient for this batch
    planned = []  # list of (ingredient, qty)
    for ri in recipe_items:
        total_qty = (ri.quantity * produced).quantize(THREE)
        planned.append((ri.ingredient, total_qty))

    # Lock all ingredient balances at source_point in stable order
    pairs = [(ing, source_point) for ing, _ in planned]
    balances = _lock_balances(pairs)

    # Validate sufficient stock for each
    for ing, qty in planned:
        bal = balances[(ing.pk, source_point.pk)]
        if bal.quantity < qty:
            raise ValidationError(
                f"Insufficient {ing.display_name} at {source_point.code}: "
                f"available {bal.quantity} {ing.selling_unit.abbreviation}, "
                f"need {qty}."
            )

    batch_number = (
        "PB-" + timezone.localtime(timezone.now()).strftime("%Y%m%d%H%M%S")
        + "-" + str(ProductionBatch.objects.count() + 1).zfill(4)
    )
    batch = ProductionBatch.objects.create(
        batch_number=batch_number,
        recipe=recipe,
        source_point=source_point,
        preparation_point=preparation_point,
        quantity_produced=produced,
        output_product=recipe.product,
        notes=notes,
        produced_by=user,
        status=ProductionBatch.Status.IN_PROGRESS,
    )

    # Deduct each ingredient, write PRODUCTION_OUT, record audit, evaluate
    movements = []
    for ing, qty in planned:
        bal = balances[(ing.pk, source_point.pk)]
        bal.quantity -= qty
        bal.save(update_fields=["quantity", "updated_at"])

        ProductionConsumption.objects.create(
            batch=batch, ingredient=ing, quantity=qty,
            unit=ing.selling_unit,
        )

        movement = StockLedger.objects.create(
            product=ing,
            operating_point=source_point,
            movement_type=StockLedger.MovementType.PRODUCTION_OUT,
            quantity_delta=-qty,
            balance_after=bal.quantity,
            reference=batch.batch_number,
            reason=f"Production: {recipe.name}",
            created_by=user,
        )
        movements.append(movement)
        record_audit(
            user=user, action=AuditLog.Action.CREATE, obj=movement,
            operating_point=source_point,
            reason=f"Production consumption for {recipe.name}",
            changes={
                "batch": batch.batch_number,
                "ingredient": ing.name,
                "quantity_delta": str(-qty),
                "balance_after": str(bal.quantity),
            },
        )
        evaluate_after_stock_change(
            product=ing, operating_point=source_point, actor=user,
        )

    batch.status = ProductionBatch.Status.COMPLETED
    batch.save(update_fields=["status"])

    record_audit(
        user=user, action=AuditLog.Action.CREATE, obj=batch,
        operating_point=preparation_point,
        reason=f"Production batch completed: {recipe.name}",
        changes={
            "batch_number": batch.batch_number,
            "recipe": recipe.name,
            "quantity_produced": str(produced),
            "consumed": len(movements),
        },
    )
    return batch


# ===========================================================================
# The sales hook (delivered now, wired into checkout in Stage 6)
# ===========================================================================
@transaction.atomic
def consume_recipe_ingredients(*, sale, product, quantity, operating_point, user):
    """Deduct the recipe's ingredients for `quantity` units of `product`
    sold via `sale`.

    Returns the list of StockLedger movements created, or an empty list
    if the product has no active recipe.

    Idempotency: refuses to run twice for the same (sale, product). Checks
    for existing PRODUCTION_OUT ledger rows with the sale_number reference
    and this product.

    Called inside the same transaction as checkout, so any failure rolls
    back the entire sale.
    """
    quantity = Decimal(str(quantity)).quantize(THREE)
    if quantity <= 0:
        raise ValidationError("Sold quantity must be greater than zero.")

    try:
        recipe = Recipe.objects.select_related("product").get(
            product=product, is_active=True,
        )
    except Recipe.DoesNotExist:
        return []

    # Duplicate-protection: refuse if this sale already produced movements
    already = StockLedger.objects.filter(
        reference=sale.sale_number,
        movement_type=StockLedger.MovementType.PRODUCTION_OUT,
    )
    if already.exists():
        # The sale has already had ingredients deducted. Do nothing —
        # checkout should not be called twice for the same sale, but if
        # it is, we must not double-deduct.
        return []

    recipe_items = list(
        recipe.items.select_related("ingredient", "unit").order_by("pk")
    )

    planned = []  # (ingredient, qty)
    for ri in recipe_items:
        total_qty = (ri.quantity * quantity).quantize(THREE)
        planned.append((ri.ingredient, total_qty))

    pairs = [(ing, operating_point) for ing, _ in planned]
    balances = _lock_balances(pairs)

    # Validate
    for ing, qty in planned:
        bal = balances[(ing.pk, operating_point.pk)]
        if bal.quantity < qty:
            raise ValidationError(
                f"Insufficient {ing.display_name} at {operating_point.code}: "
                f"available {bal.quantity} {ing.selling_unit.abbreviation}, "
                f"need {qty}."
            )

    movements = []
    for ing, qty in planned:
        bal = balances[(ing.pk, operating_point.pk)]
        bal.quantity -= qty
        bal.save(update_fields=["quantity", "updated_at"])

        movement = StockLedger.objects.create(
            product=ing,
            operating_point=operating_point,
            movement_type=StockLedger.MovementType.PRODUCTION_OUT,
            quantity_delta=-qty,
            balance_after=bal.quantity,
            sale=sale,
            reference=sale.sale_number,
            reason=f"Recipe consumption for {product.display_name}",
            created_by=user,
        )
        movements.append(movement)
        record_audit(
            user=user, action=AuditLog.Action.CREATE, obj=movement,
            operating_point=operating_point,
            reason=f"Ingredient consumed for sale {sale.sale_number}",
            changes={
                "sale_number": sale.sale_number,
                "product_sold": product.name,
                "ingredient": ing.name,
                "quantity_delta": str(-qty),
                "balance_after": str(bal.quantity),
            },
        )
        evaluate_after_stock_change(
            product=ing, operating_point=operating_point, actor=user,
        )

    return movements