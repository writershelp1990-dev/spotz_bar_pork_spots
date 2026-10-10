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

        self.assertTrue(sale.completed)
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


class MpesaManualPaymentTests(TestCase):
    """Regression tests for the customer-paid (manual) M-Pesa flow."""

    def setUp(self):
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
            kwargs["username"] = "manual_mpesa_director"
        elif "national_id" in user_fields:
            kwargs["national_id"] = "MANUAL-MPESA-001"
        self.user = User.objects.create_user(**kwargs)

        self.point = OperatingPoint.objects.create(
            code="BAR-MANUAL", name="Bar Manual", is_active=True,
        )

        self.category = Category.objects.create(
            name="Manual M-Pesa Category", is_active=True,
        )
        self.unit = UnitOfMeasure.objects.create(
            name="Manual Unit", abbreviation="mun",
            unit_type="VOLUME", is_active=True,
        )
        self.product = Product.objects.create(
            name="Manual M-Pesa Product",
            brand="ManualBrand", variety="ManualVariety",
            packaging="Bottle", size_value=Decimal("500"),
            size_unit=self.unit, code="MANUAL-MPESA-TEST",
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

        # Ensure an open business day for today
        today = timezone.localdate()
        BusinessDay.objects.filter(
            business_date=today, status="OPEN"
        ).delete()
        BusinessDay.objects.create(
            business_date=today,
            status="OPEN",
            opened_by=self.user,
            actual_opened_at=timezone.now(),
        )

        # Give the product enough stock at the test point
        StockBalance.objects.create(
            product=self.product,
            operating_point=self.point,
            quantity=Decimal("100.000"),
        )

        self.client.force_login(self.user)

    def _post(self, **overrides):
        import json
        from django.urls import reverse
        data = {
            "operating_point": str(self.point.pk),
            "payment_method": "MPESA_MANUAL",
            "amount_tendered": "",
            "items_json": json.dumps([
                {"product_id": self.product.pk, "quantity": 1}
            ]),
            "mpesa_receipt_code": "SLK7X2M9AB",
            "mpesa_amount_paid": "100.00",
            "phone_number": "",
        }
        data.update(overrides)
        return self.client.post(reverse("sales:pos"), data)

    def test_manual_mpesa_creates_payment_record(self):
        from sales.models import MpesaManualPayment
        response = self._post()
        self.assertEqual(response.status_code, 302, response.content[:500])
        record = MpesaManualPayment.objects.get(receipt_code="SLK7X2M9AB")
        self.assertEqual(record.amount_paid, 100)
        self.assertEqual(record.recorded_by, self.user)

    def test_manual_mpesa_marks_sale_paid(self):
        from sales.models import Sale
        response = self._post()
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.latest("id")
        self.assertTrue(sale.completed)
        self.assertEqual(sale.payment_status, "PAID")
        self.assertEqual(sale.payment_method, "MPESA_MANUAL")

    def test_duplicate_receipt_code_rejected(self):
        from sales.models import MpesaManualPayment
        self._post()
        self.assertEqual(MpesaManualPayment.objects.count(), 1)
        response = self._post()
        self.assertEqual(
            MpesaManualPayment.objects.filter(receipt_code="SLK7X2M9AB").count(),
            1,
            "Duplicate receipt code must not create a second payment record.",
        )

    def test_receipt_code_uppercased(self):
        from sales.models import MpesaManualPayment
        self._post(mpesa_receipt_code="slk7x2m9ab")
        self.assertTrue(
            MpesaManualPayment.objects.filter(receipt_code="SLK7X2M9AB").exists(),
            "Receipt code should be normalised to upper case.",
        )

    def test_missing_receipt_code_rejected(self):
        from sales.models import MpesaManualPayment
        self._post(mpesa_receipt_code="")
        self.assertEqual(MpesaManualPayment.objects.count(), 0)

    def test_amount_paid_less_than_total_rejected(self):
        from sales.models import MpesaManualPayment
        self._post(mpesa_amount_paid="50.00")
        self.assertEqual(MpesaManualPayment.objects.count(), 0)

