"""Tests for the expenses app — models and the seed command."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from accounts.models import OperatingPoint, RoleDefinition
from .models import Expense, ExpenseCategory

User = get_user_model()


class ExpenseCategoryModelTests(TestCase):

    def test_root_category_can_exist_without_parent(self):
        cat = ExpenseCategory.objects.create(code="R1", name="Utilities")
        self.assertIsNone(cat.parent)
        self.assertTrue(cat.is_root)

    def test_child_category_links_to_parent(self):
        parent = ExpenseCategory.objects.create(code="P1", name="Utilities")
        child = ExpenseCategory.objects.create(code="C1", name="Electricity", parent=parent)
        self.assertEqual(child.parent, parent)
        self.assertFalse(child.is_root)

    def test_str_shows_full_path(self):
        parent = ExpenseCategory.objects.create(code="P2", name="Utilities")
        child = ExpenseCategory.objects.create(code="C2", name="Water", parent=parent)
        self.assertEqual(str(parent), "Utilities")
        self.assertEqual(str(child), "Utilities > Water")

    def test_unique_name_per_parent(self):
        parent = ExpenseCategory.objects.create(code="P3", name="Utilities")
        ExpenseCategory.objects.create(code="C3", name="Electricity", parent=parent)
        with self.assertRaises(IntegrityError):
            ExpenseCategory.objects.create(code="C3b", name="Electricity", parent=parent)

    def test_same_name_allowed_under_different_parents(self):
        p1 = ExpenseCategory.objects.create(code="PA", name="Utilities")
        p2 = ExpenseCategory.objects.create(code="PB", name="Operations")
        ExpenseCategory.objects.create(code="C4", name="Other", parent=p1)
        ExpenseCategory.objects.create(code="C5", name="Other", parent=p2)
        self.assertEqual(ExpenseCategory.objects.filter(name="Other").count(), 2)

    def test_code_is_unique(self):
        ExpenseCategory.objects.create(code="DUP", name="First")
        with self.assertRaises(IntegrityError):
            ExpenseCategory.objects.create(code="DUP", name="Second")


class ExpenseModelTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.dir_role, _ = RoleDefinition.objects.get_or_create(
            code="DIRECTOR", defaults={"name": "Director"})
        cls.director = User.objects.create_user(
            username="ex_dir", password="pw", role="DIRECTOR",
            role_definition=cls.dir_role, is_active=True, is_active_staff=True)
        cls.bar, _ = OperatingPoint.objects.get_or_create(
            code="BAR", defaults={"name": "Bar", "is_active": True})
        cls.cat = ExpenseCategory.objects.create(code="TST_ELEC", name="Electricity")

    def test_default_status_is_pending(self):
        e = Expense.objects.create(
            category=self.cat, amount=Decimal("5000.00"),
            expense_date=timezone.localdate(), payment_method="CASH",
            entered_by=self.director,
        )
        self.assertEqual(e.status, Expense.Status.PENDING)

    def test_negative_amount_rejected_by_validation(self):
        e = Expense(
            category=self.cat, amount=Decimal("-100.00"),
            expense_date=timezone.localdate(), payment_method="CASH",
            entered_by=self.director,
        )
        with self.assertRaises(ValidationError):
            e.full_clean()

    def test_zero_amount_rejected_by_validation(self):
        e = Expense(
            category=self.cat, amount=Decimal("0.00"),
            expense_date=timezone.localdate(), payment_method="CASH",
            entered_by=self.director,
        )
        with self.assertRaises(ValidationError):
            e.full_clean()

    def test_category_protected_from_deletion(self):
        Expense.objects.create(
            category=self.cat, amount=Decimal("1000.00"),
            expense_date=timezone.localdate(), payment_method="CASH",
            entered_by=self.director,
        )
        with self.assertRaises(Exception):
            self.cat.delete()

    def test_ordering_is_reverse_date(self):
        d1 = timezone.localdate() - timezone.timedelta(days=5)
        d2 = timezone.localdate()
        Expense.objects.create(
            category=self.cat, amount=Decimal("100.00"),
            expense_date=d1, payment_method="CASH", entered_by=self.director,
        )
        Expense.objects.create(
            category=self.cat, amount=Decimal("200.00"),
            expense_date=d2, payment_method="CASH", entered_by=self.director,
        )
        rows = list(Expense.objects.all())
        self.assertEqual(rows[0].expense_date, d2)
        self.assertEqual(rows[1].expense_date, d1)

    def test_operating_point_is_optional(self):
        e = Expense.objects.create(
            category=self.cat, amount=Decimal("500.00"),
            expense_date=timezone.localdate(), payment_method="CASH",
            entered_by=self.director,
        )
        self.assertIsNone(e.operating_point)

    def test_operating_point_can_be_set(self):
        e = Expense.objects.create(
            category=self.cat, amount=Decimal("500.00"),
            expense_date=timezone.localdate(), payment_method="CASH",
            entered_by=self.director, operating_point=self.bar,
        )
        self.assertEqual(e.operating_point, self.bar)


class SeedCommandTests(TestCase):

    def test_seed_creates_expected_number_of_categories(self):
        from io import StringIO
        from django.core.management import call_command
        ExpenseCategory.objects.all().delete()
        call_command("seed_expense_categories", stdout=StringIO())
        self.assertEqual(ExpenseCategory.objects.count(), 30)
        self.assertEqual(
            ExpenseCategory.objects.filter(parent__isnull=True).count(), 7)
        self.assertEqual(
            ExpenseCategory.objects.filter(parent__isnull=False).count(), 23)

    def test_seed_is_idempotent(self):
        from io import StringIO
        from django.core.management import call_command
        ExpenseCategory.objects.all().delete()
        call_command("seed_expense_categories", stdout=StringIO())
        call_command("seed_expense_categories", stdout=StringIO())
        self.assertEqual(ExpenseCategory.objects.count(), 30)

    def test_seed_creates_personnel_salaries_category(self):
        from io import StringIO
        from django.core.management import call_command
        ExpenseCategory.objects.all().delete()
        call_command("seed_expense_categories", stdout=StringIO())
        self.assertTrue(
            ExpenseCategory.objects.filter(code="PERS_SAL").exists())
