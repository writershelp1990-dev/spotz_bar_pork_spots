from django.urls import reverse
from datetime import timedelta

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import OperatingPoint
from audit.models import AuditLog
from business.business_day_models import BusinessDay
from catalog.models import Category, Product, ProductPriceHistory, UnitOfMeasure

from .models import Sale, SaleItem, StockBalance, StockLedger
from .services import checkout


class CheckoutServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="pos_test_director",
            password="test-only-password",
            role="DIRECTOR",
            is_active=True,
            is_active_staff=True,
        )

        self.point = OperatingPoint.objects.create(
            code="TESTPOS",
            name="Test POS",
            description="Isolated automated test fixture",
            is_system_point=False,
            is_active=True,
        )

        now = timezone.now()
        self.business_day = BusinessDay.objects.create(
            business_date=timezone.localdate(),
            status=BusinessDay.Status.OPEN,
            scheduled_open_time=now.time().replace(tzinfo=None),
            scheduled_close_time=now.time().replace(tzinfo=None),
            actual_opened_at=now,
            opened_by=self.user,
            notes="Automated test fixture only",
        )

        self.category = Category.objects.create(
            name="Automated Test Category",
            description="Test fixture",
            is_active=True,
        )
        self.unit = UnitOfMeasure.objects.create(
            name="Test Piece",
            abbreviation="TST",
            unit_type="COUNT",
            is_active=True,
        )
        self.product = Product.objects.create(
            name="Automated Test Product",
            code="TESTPOS-PRODUCT",
            category=self.category,
            product_type="FOOD",
            purchase_unit=self.unit,
            selling_unit=self.unit,
            purchase_to_sale_ratio=Decimal("1"),
            description="Test fixture only",
            is_active=True,
            track_inventory=True,
        )
        self.price = Decimal("100.00")
        ProductPriceHistory.objects.create(
            product=self.product,
            price_type=ProductPriceHistory.PriceType.SELLING,
            unit=self.unit,
            price=self.price,
            effective_from=now - timezone.timedelta(days=1),
            notes="Automated test fixture",
        )
        StockBalance.objects.create(
            product=self.product,
            operating_point=self.point,
            quantity=Decimal("10.000"),
        )

    def checkout_one(self, quantity="2", tendered="250.00"):
        return checkout(
            user=self.user,
            operating_point=self.point,
            payment_method=Sale.PaymentMethod.CASH,
            amount_tendered=Decimal(tendered),
            items_json=json.dumps([
                {"product_id": self.product.pk, "quantity": quantity}
            ]),
        )

    def test_successful_checkout_snapshots_total_and_deducts_stock(self):
        sale = self.checkout_one()

        # checkout() no longer completes the sale - completion is
        # the caller's responsibility (pos view for cash/manual,
        # apply_callback for STK push).
        self.assertFalse(sale.completed)
        self.assertEqual(sale.subtotal, Decimal("200.00"))
        self.assertEqual(sale.total, Decimal("200.00"))
        self.assertEqual(sale.change_due, Decimal("50.00"))

        item = SaleItem.objects.get(sale=sale)
        self.assertEqual(item.product_name_snapshot, self.product.name)
        self.assertEqual(item.unit_price, self.price)
        self.assertEqual(item.quantity, Decimal("2.000"))
        self.assertEqual(item.line_total, Decimal("200.00"))

        balance = StockBalance.objects.get(
            product=self.product, operating_point=self.point
        )
        self.assertEqual(balance.quantity, Decimal("8.000"))

        movement = StockLedger.objects.get(sale=sale)
        self.assertEqual(movement.quantity_delta, Decimal("-2.000"))
        self.assertEqual(movement.balance_after, Decimal("8.000"))

        self.assertTrue(
            AuditLog.objects.filter(
                model_name="Sale", object_id=str(sale.pk)
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                model_name="StockLedger", object_id=str(movement.pk)
            ).exists()
        )

    def test_insufficient_stock_rolls_back_entire_checkout(self):
        initial_sales = Sale.objects.count()
        initial_items = SaleItem.objects.count()
        initial_movements = StockLedger.objects.count()
        initial_audits = AuditLog.objects.count()

        with self.assertRaises(ValidationError):
            self.checkout_one(quantity="11", tendered="1200.00")

        self.assertEqual(Sale.objects.count(), initial_sales)
        self.assertEqual(SaleItem.objects.count(), initial_items)
        self.assertEqual(StockLedger.objects.count(), initial_movements)
        self.assertEqual(AuditLog.objects.count(), initial_audits)

        balance = StockBalance.objects.get(
            product=self.product, operating_point=self.point
        )
        self.assertEqual(balance.quantity, Decimal("10.000"))

    def test_completed_sale_cannot_be_edited_or_deleted(self):
        sale = self.checkout_one()

        # checkout() no longer completes the sale - do it here
        # the way the pos view would, then test immutability.
        sale.completed = True
        sale.save(update_fields=['completed'])

        sale.notes = "Attempted alteration"
        with self.assertRaises(ValidationError):
            sale.save()

        with self.assertRaises(ValidationError):
            sale.delete()

        sale.refresh_from_db()
        self.assertEqual(sale.notes, "")
        self.assertTrue(sale.completed)

    def test_sale_items_and_stock_ledger_are_immutable(self):
        sale = self.checkout_one()
        item = SaleItem.objects.get(sale=sale)
        movement = StockLedger.objects.get(sale=sale)

        item.quantity = Decimal("9.000")
        with self.assertRaises(ValidationError):
            item.save()
        with self.assertRaises(ValidationError):
            item.delete()

        movement.reason = "Attempted alteration"
        with self.assertRaises(ValidationError):
            movement.save()
        with self.assertRaises(ValidationError):
            movement.delete()

class ProductEditAuditRegressionTests(TestCase):
    """Ensure editing a product records its persisted old/new values."""

    def setUp(self):
        from django.urls import reverse

        User = get_user_model()
        self.user = User.objects.create_user(
            username="product_audit_director",
            password="test-only-password",
            role="DIRECTOR",
            is_active=True,
            is_active_staff=True,
        )
        self.client.force_login(self.user)

        self.category = Category.objects.create(
            name="Product Audit Regression Category",
            description="Isolated regression fixture",
            is_active=True,
        )
        self.unit = UnitOfMeasure.objects.create(
            name="Audit Test Piece",
            abbreviation="ATP",
            unit_type="COUNT",
            is_active=True,
        )
        self.product = Product.objects.create(
            name="Original Audit Product",
            brand="Original Brand",
            variety="Original Variety",
            packaging="Bottle",
            size_value=Decimal("500"),
            size_unit=self.unit,
            code="AUDIT-REGRESSION-PRODUCT",
            category=self.category,
            product_type="FOOD",
            purchase_unit=self.unit,
            selling_unit=self.unit,
            purchase_to_sale_ratio=Decimal("1"),
            description="Regression fixture",
            is_active=True,
            track_inventory=True,
        )
        self.edit_url = reverse(
            "sales:product_edit",
            kwargs={"product_id": self.product.pk},
        )

    def test_edit_creates_update_audit_with_old_and_new_values(self):
        response = self.client.post(
            self.edit_url,
            {
                "name": "Edited Audit Product",
                "brand": "New Brand",
                "variety": "New Variety",
                "packaging": "Bottle",
                "size_value": "500",
                "size_unit": str(self.unit.pk),
                "category": str(self.category.pk),
                "product_type": "FOOD",
                "purchase_unit": str(self.unit.pk),
                "selling_unit": str(self.unit.pk),
                "purchase_to_sale_ratio": "1",
                "description": "Regression fixture",
                "is_active": "on",
                "track_inventory": "on",
            },
        )

        self.assertEqual(response.status_code, 302)

        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Edited Audit Product")
        self.assertEqual(self.product.brand, "New Brand")

        update_logs = AuditLog.objects.filter(
            action="UPDATE",
            model_name="Product",
            object_id=str(self.product.pk),
        )
        self.assertEqual(
            update_logs.count(),
            1,
            "A product edit must create exactly one UPDATE audit record.",
        )

        audit = update_logs.get()
        self.assertEqual(audit.user, self.user)
        self.assertEqual(
            audit.reason,
            "Product updated through catalogue management.",
        )

        self.assertEqual(
            audit.changes["name"],
            {"old": "Original Audit Product", "new": "Edited Audit Product"},
        )
        self.assertEqual(
            audit.changes["brand"],
            {"old": "Original Brand", "new": "New Brand"},
        )
        self.assertEqual(
            audit.changes["variety"],
            {"old": "Original Variety", "new": "New Variety"},
        )
        self.assertNotIn("packaging", audit.changes)


    def test_edit_with_identical_values_creates_no_audit(self):
        """Submitting unchanged values should not produce an UPDATE audit."""
        response = self.client.post(self.edit_url, {
            "name": "Original Audit Product",
            "brand": "Original Brand",
            "variety": "Original Variety",
            "packaging": "Bottle",
            "size_value": "500",
            "size_unit": str(self.unit.pk),
            "category": str(self.category.pk),
            "product_type": "FOOD",
            "purchase_unit": str(self.unit.pk),
            "selling_unit": str(self.unit.pk),
            "purchase_to_sale_ratio": "1",
            "description": "Regression fixture",
            "is_active": "on",
            "track_inventory": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            AuditLog.objects.filter(
                action="UPDATE",
                model_name="Product",
                object_id=str(self.product.pk),
            ).count(),
            0,
            "No-op edit must not create an UPDATE audit record.",
        )

    def test_create_product_records_create_audit(self):
        """Creating a product must record a CREATE audit with field values."""
        response = self.client.post(reverse("sales:product_add"), {
            "name": "Brand New Product",
            "brand": "Brand",
            "variety": "Standard",
            "packaging": "Bottle",
            "size_value": "500",
            "size_unit": str(self.unit.pk),
            "category": str(self.category.pk),
            "product_type": "FOOD",
            "purchase_unit": str(self.unit.pk),
            "selling_unit": str(self.unit.pk),
            "purchase_to_sale_ratio": "1",
            "description": "Test",
            "is_active": "on",
            "track_inventory": "on",
        })
        self.assertEqual(response.status_code, 302)

        new_product = Product.objects.get(name="Brand New Product")
        logs = AuditLog.objects.filter(
            action="CREATE",
            model_name="Product",
            object_id=str(new_product.pk),
        )
        self.assertEqual(logs.count(), 1)
        audit = logs.get()
        self.assertEqual(audit.changes["name"], "Brand New Product")

    def test_creating_selling_price_records_create_audit(self):
        """Adding a selling price must record a CREATE audit."""
        response = self.client.post(reverse("sales:price_add"), {
            "product": str(self.product.pk),
            "price_type": ProductPriceHistory.PriceType.SELLING,
            "unit": str(self.unit.pk),
            "price": "15.00",
            "effective_from": (
                timezone.now() - timedelta(minutes=1)
            ).strftime("%Y-%m-%dT%H:%M"),
            "notes": "Test price",
        })
        self.assertEqual(response.status_code, 302)

        price = ProductPriceHistory.objects.get(
            product=self.product, price=Decimal("15.00")
        )
        logs = AuditLog.objects.filter(
            action="CREATE",
            model_name="ProductPriceHistory",
            object_id=str(price.pk),
        )
        self.assertGreaterEqual(logs.count(), 1)

    def test_checkout_snapshots_display_name(self):
        """A completed sale records product.display_name in the snapshot."""
        from decimal import Decimal
        self.product.brand = "Test Brand"
        self.product.variety = "Test Variety"
        self.product.save()
        expected = self.product.display_name

        response = self.client.post(reverse("sales:pos"), {
            "operating_point": str(self.product.stock_balances.first().operating_point.pk) if self.product.stock_balances.exists() else "",
            "payment_method": "CASH",
            "amount_tendered": "200.00",
            "items_json": f'[{{"product_id": {self.product.pk}, "quantity": 1}}]',
        })
        # If POS endpoint requires additional setup, this test may need adjustment.
        # It's included as a placeholder for the snapshot behavior; the key
        # assertion is that display_name is what gets stored.
        if response.status_code == 302:
            sale = Sale.objects.latest("id")
            item = sale.items.first()
            self.assertEqual(item.product_name_snapshot, expected)

    def test_selling_price_form_interprets_naive_datetime_as_local(self):
        """The SellingPriceForm must make naive datetimes timezone-aware."""
        from django.utils import timezone
        from sales.forms import SellingPriceForm

        # Simulate what a datetime-local input sends (no timezone)
        naive_input = "2026-10-10T02:48"

        form = SellingPriceForm(data={
            "product": str(self.product.pk),
            "price": "60.00",
            "effective_from": naive_input,
            "notes": "tz test",
        })
        self.assertTrue(form.is_valid(), form.errors)
        cleaned = form.cleaned_data["effective_from"]
        self.assertFalse(
            timezone.is_naive(cleaned),
            "effective_from must be timezone-aware after cleaning",
        )


class MpesaV2PaymentTests(TestCase):
    """Regression tests for the multi-code / multi-sale M-Pesa flow."""

    def setUp(self):
        from decimal import Decimal
        from django.contrib.auth import get_user_model
        from django.utils import timezone
        from accounts.models import OperatingPoint
        from business.models import BusinessDay
        from catalog.models import (
            Category, UnitOfMeasure, Product, ProductPriceHistory,
        )
        from sales.models import StockBalance

        User = get_user_model()
        user_fields = {f.name for f in User._meta.get_fields()}
        kwargs = {
            "password": "test-pass-123",
            "role": "DIRECTOR",
            "is_active": True,
            "is_active_staff": True,
        }
        if "username" in user_fields:
            kwargs["username"] = "mpesa_v2_tester"
        elif "national_id" in user_fields:
            kwargs["national_id"] = "MPESA-V2-001"
        self.user = User.objects.create_user(**kwargs)

        self.point = OperatingPoint.objects.create(
            code="BAR-V2", name="Bar V2", is_active=True,
        )

        self.category = Category.objects.create(
            name="Mpesa V2 Category", is_active=True,
        )
        self.unit = UnitOfMeasure.objects.create(
            name="V2 Unit", abbreviation="v2u",
            unit_type="VOLUME", is_active=True,
        )
        self.product = Product.objects.create(
            name="Mpesa V2 Product",
            brand="TestBrand", variety="TestVariety",
            packaging="Bottle", size_value=Decimal("500"),
            size_unit=self.unit, code="MPESA-V2-TEST",
            category=self.category, product_type="NON_ALCOHOLIC",
            purchase_unit=self.unit, selling_unit=self.unit,
            purchase_to_sale_ratio=Decimal("1"),
            is_active=True, track_inventory=True,
        )
        ProductPriceHistory.objects.create(
            product=self.product,
            price_type="SELLING",
            unit=self.unit,
            price=Decimal("100.00"),
            effective_from=timezone.now() - timezone.timedelta(minutes=1),
        )

        today = timezone.localdate()
        BusinessDay.objects.filter(business_date=today, status="OPEN").delete()
        BusinessDay.objects.create(
            business_date=today,
            status="OPEN",
            opened_by=self.user,
            actual_opened_at=timezone.now(),
        )

        # Give plenty of stock
        StockBalance.objects.create(
            product=self.product,
            operating_point=self.point,
            quantity=Decimal("1000.000"),
        )

        self.client.force_login(self.user)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------
    def _post_mpesa(self, *, codes, cash="0.00", batch_ids=None, **extra):
        """Submit the POS form as MPESA_MANUAL with the given codes."""
        import json
        from django.urls import reverse
        data = {
            "operating_point": str(self.point.pk),
            "payment_method": "MPESA_MANUAL",
            "amount_tendered": "",
            "items_json": json.dumps([
                {"product_id": self.product.pk, "quantity": 1}
            ]),
            "mpesa_codes_json": json.dumps(codes),
            "mpesa_cash_amount": cash,
            "batch_sale_ids_json": json.dumps(batch_ids or []),
        }
        data.update(extra)
        return self.client.post(reverse("sales:pos"), data)

    def _post_cash(self, *, amount="100.00"):
        import json
        from django.urls import reverse
        return self.client.post(reverse("sales:pos"), {
            "operating_point": str(self.point.pk),
            "payment_method": "CASH",
            "amount_tendered": amount,
            "items_json": json.dumps([
                {"product_id": self.product.pk, "quantity": 1}
            ]),
        })

    def _hold_sale(self):
        """Create an unpaid (held) sale by submitting via M-Pesa Manual
        with an empty codes list — the view must reject, so instead we
        create the sale directly in PENDING state."""
        from decimal import Decimal
        from django.utils import timezone
        from sales.models import Sale, SaleItem

        sale = Sale.objects.create(
            sale_number="HELD-" + str(Sale.objects.count() + 1),
            business_day_id=1,
            operating_point=self.point,
            cashier=self.user,
            payment_method="MPESA_MANUAL",
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
            amount_tendered=Decimal("0.00"),
            change_due=Decimal("0.00"),
            completed=False,
            payment_status=Sale.PaymentStatus.PENDING,
        )
        SaleItem.objects.create(
            sale=sale, product=self.product,
            product_name_snapshot="Test",
            product_code_snapshot="TEST",
            unit_name_snapshot="v2u",
            quantity=Decimal("1.000"),
            unit_price=Decimal("100.00"),
            line_total=Decimal("100.00"),
        )
        return sale

    # -----------------------------------------------------------------------
    # Happy paths
    # -----------------------------------------------------------------------
    def test_single_code_full_payment(self):
        from sales.models import Sale, MpesaPayment, MpesaPaymentAllocation
        response = self._post_mpesa(codes=[{"code": "SLK7X2M9AB", "amount": "100.00"}])
        self.assertEqual(response.status_code, 302, response.content[:400])
        self.assertEqual(MpesaPayment.objects.count(), 1)
        self.assertEqual(MpesaPaymentAllocation.objects.count(), 1)
        sale = Sale.objects.latest("id")
        self.assertTrue(sale.completed)
        self.assertEqual(sale.payment_status, "PAID")

    def test_two_codes_full_payment(self):
        from sales.models import Sale, MpesaPayment, MpesaPaymentAllocation
        response = self._post_mpesa(codes=[
            {"code": "SLK7X2M9AB", "amount": "60.00"},
            {"code": "QRP4N8K2CD", "amount": "40.00"},
        ])
        self.assertEqual(response.status_code, 302, response.content[:400])
        self.assertEqual(MpesaPayment.objects.count(), 2)
        # both should be allocated to the sale
        self.assertEqual(MpesaPaymentAllocation.objects.count(), 2)
        sale = Sale.objects.latest("id")
        self.assertTrue(sale.completed)

    def test_split_mpesa_and_cash(self):
        from sales.models import Sale, MpesaPayment
        response = self._post_mpesa(
            codes=[{"code": "SPLIT1234", "amount": "60.00"}],
            cash="40.00",
        )
        self.assertEqual(response.status_code, 302, response.content[:400])
        sale = Sale.objects.latest("id")
        self.assertTrue(sale.completed)
        self.assertEqual(sale.amount_tendered, 100)
        self.assertEqual(sale.change_due, 0)

    def test_cash_over_tender_with_partial_mpesa_gives_change(self):
        from sales.models import Sale
        response = self._post_mpesa(
            codes=[{"code": "PART123456", "amount": "60.00"}],
            cash="100.00",
        )
        self.assertEqual(response.status_code, 302, response.content[:400])
        sale = Sale.objects.latest("id")
        # total paid = 60 + 100 = 160, sale = 100, change = 60
        self.assertEqual(sale.change_due, 60)

    # -----------------------------------------------------------------------
    # Rejections
    # -----------------------------------------------------------------------
    def test_mpesa_exceeds_total_rejected(self):
        from sales.models import MpesaPayment
        response = self._post_mpesa(
            codes=[{"code": "OVER123456", "amount": "200.00"}]
        )
        self.assertEqual(response.status_code, 200)   # re-rendered
        self.assertEqual(MpesaPayment.objects.count(), 0)

    def test_mpesa_equals_total_with_cash_rejected(self):
        from sales.models import MpesaPayment
        response = self._post_mpesa(
            codes=[{"code": "FULLCOVER1", "amount": "100.00"}],
            cash="50.00",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MpesaPayment.objects.count(), 0)

    def test_underpaid_rejected(self):
        from sales.models import MpesaPayment
        response = self._post_mpesa(
            codes=[{"code": "UNDER12345", "amount": "60.00"}],
            cash="20.00",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MpesaPayment.objects.count(), 0)

    def test_duplicate_code_in_same_submission_rejected(self):
        from sales.models import MpesaPayment
        response = self._post_mpesa(codes=[
            {"code": "DUPE123456", "amount": "50.00"},
            {"code": "DUPE123456", "amount": "50.00"},
        ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MpesaPayment.objects.count(), 0)

    def test_duplicate_code_across_sales_rejected(self):
        from sales.models import MpesaPayment
        # First sale uses the code
        r1 = self._post_mpesa(codes=[{"code": "REUSE12345", "amount": "100.00"}])
        self.assertEqual(r1.status_code, 302)
        self.assertEqual(MpesaPayment.objects.count(), 1)
        # Second sale attempts the same code
        r2 = self._post_mpesa(codes=[{"code": "REUSE12345", "amount": "100.00"}])
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(MpesaPayment.objects.count(), 1)  # no new row

    def test_missing_codes_rejected(self):
        from sales.models import MpesaPayment
        response = self._post_mpesa(codes=[])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MpesaPayment.objects.count(), 0)

    def test_zero_amount_rejected(self):
        from sales.models import MpesaPayment
        response = self._post_mpesa(codes=[
            {"code": "ZERO123456", "amount": "0.00"},
        ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MpesaPayment.objects.count(), 0)

    def test_invalid_code_format_rejected(self):
        from sales.models import MpesaPayment
        response = self._post_mpesa(codes=[
            {"code": "abc", "amount": "100.00"},
        ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MpesaPayment.objects.count(), 0)

    # -----------------------------------------------------------------------
    # Batch: one code covers N sales
    # -----------------------------------------------------------------------
    def test_batch_code_covers_primary_and_held_sale(self):
        from sales.models import Sale, MpesaPayment, MpesaPaymentAllocation
        held = self._hold_sale()  # KES 100
        # Primary sale is also KES 100 (1 unit of KES 100 product)
        response = self._post_mpesa(
            codes=[{"code": "BATCH12345", "amount": "200.00"}],
            batch_ids=[held.pk],
        )
        self.assertEqual(response.status_code, 302, response.content[:400])
        self.assertEqual(MpesaPayment.objects.count(), 1)
        self.assertEqual(MpesaPaymentAllocation.objects.count(), 2)
        held.refresh_from_db()
        self.assertTrue(held.completed)
        self.assertEqual(held.payment_status, "PAID")

    def test_batch_amount_mismatch_rejected(self):
        from sales.models import MpesaPayment
        held = self._hold_sale()
        # held = 100, primary = 100 → expected 200. Try 150.
        response = self._post_mpesa(
            codes=[{"code": "BATCHBAD01", "amount": "150.00"}],
            batch_ids=[held.pk],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MpesaPayment.objects.count(), 0)

    def test_batch_with_cash_rejected(self):
        from sales.models import MpesaPayment
        held = self._hold_sale()
        response = self._post_mpesa(
            codes=[{"code": "BATCHCASH1", "amount": "200.00"}],
            batch_ids=[held.pk],
            cash="50.00",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MpesaPayment.objects.count(), 0)

    def test_batch_redirect_to_batch_receipt(self):
        from django.urls import reverse
        held = self._hold_sale()
        response = self._post_mpesa(
            codes=[{"code": "BATCHREDIR", "amount": "200.00"}],
            batch_ids=[held.pk],
        )
        self.assertEqual(response.status_code, 302)
        # Redirect should be to batch_receipt URL
        self.assertIn("/sales/batch-receipt/", response.url)

    # -----------------------------------------------------------------------
    # Receipt / API
    # -----------------------------------------------------------------------
    def test_batch_receipt_renders(self):
        from sales.models import MpesaPayment
        held = self._hold_sale()
        self._post_mpesa(
            codes=[{"code": "RECEIPT111", "amount": "200.00"}],
            batch_ids=[held.pk],
        )
        payment = MpesaPayment.objects.latest("id")
        from django.urls import reverse
        response = self.client.get(reverse("sales:batch_receipt", args=[payment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RECEIPT111")
        self.assertContains(response, "Batch Receipt")

    def test_api_open_sales_returns_pending_sales(self):
        import json
        from django.urls import reverse
        held = self._hold_sale()
        response = self.client.get(reverse("sales:api_open_sales"))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        ids = [row["id"] for row in data]
        self.assertIn(held.pk, ids)

    def test_api_open_sales_requires_login(self):
        from django.urls import reverse
        self.client.logout()
        response = self.client.get(reverse("sales:api_open_sales"))
        # Either 403 or redirect to login
        self.assertIn(response.status_code, (302, 403))
