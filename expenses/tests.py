"""Tests for the expenses app — models and the seed command."""
from datetime import date as _date_import
from expenses.models import RecurringExpense as _RecurringExpense_import
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


class ExpenseViewTests(TestCase):
    """Expense CRUD, approval workflow, filters, permissions."""

    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal
        from django.contrib.auth import get_user_model
        from django.utils import timezone
        from accounts.models import Employee, OperatingPoint, RoleDefinition

        User = get_user_model()
        dir_role, _ = RoleDefinition.objects.get_or_create(
            code="DIRECTOR", defaults={"name": "Director"})
        bar_role, _ = RoleDefinition.objects.get_or_create(
            code="BAR_ATTENDANT", defaults={"name": "Bar Attendant"})
        kit_role, _ = RoleDefinition.objects.get_or_create(
            code="KITCHEN_ATTENDANT", defaults={"name": "Kitchen Attendant"})

        cls.director = User.objects.create_user(
            username="exv_dir", password="pw", role="DIRECTOR",
            role_definition=dir_role, is_active=True, is_active_staff=True)

        cls.bar, _ = OperatingPoint.objects.get_or_create(
            code="BAR", defaults={"name": "Bar", "is_active": True})
        cls.kitchen, _ = OperatingPoint.objects.get_or_create(
            code="KITCHEN", defaults={"name": "Kitchen", "is_active": True})

        cls.bar_user = User.objects.create_user(
            username="exv_bar", password="pw", role="BAR_ATTENDANT",
            role_definition=bar_role, is_active=True, is_active_staff=True)
        Employee.objects.create(
            user=cls.bar_user, operating_point=cls.bar,
            employment_status=Employee.EmploymentStatus.ACTIVE)

        cls.kit_user = User.objects.create_user(
            username="exv_kit", password="pw", role="KITCHEN_ATTENDANT",
            role_definition=kit_role, is_active=True, is_active_staff=True)
        Employee.objects.create(
            user=cls.kit_user, operating_point=cls.kitchen,
            employment_status=Employee.EmploymentStatus.ACTIVE)

        cls.utilities, _ = ExpenseCategory.objects.get_or_create(
            code="TST_UTIL", defaults={"name": "Utilities"})
        cls.electricity, _ = ExpenseCategory.objects.get_or_create(
            code="TST_ELEC", defaults={"name": "Electricity", "parent": cls.utilities})

    def _make_expense(self, user, amount="1000.00", point=None, category=None):
        from decimal import Decimal
        from django.utils import timezone
        return Expense.objects.create(
            category=category or self.electricity,
            amount=Decimal(amount),
            expense_date=timezone.localdate(),
            payment_method="CASH",
            entered_by=user,
            operating_point=point,
        )

    # ---- Permissions ----

    def test_anonymous_redirects_on_every_url(self):
        from django.urls import reverse
        urls = [
            reverse("expenses:expense_list"),
            reverse("expenses:expense_create"),
        ]
        for u in urls:
            r = self.client.get(u)
            self.assertEqual(r.status_code, 302, msg=f"{u} did not redirect")

    def test_active_staff_can_create(self):
        from django.urls import reverse
        from decimal import Decimal
        self.client.force_login(self.bar_user)
        r = self.client.post(reverse("expenses:expense_create"), {
            "category": self.electricity.pk,
            "amount": "500.00",
            "expense_date": "2026-10-14",
            "payment_method": "CASH",
            "payee": "KPLC",
            "description": "Test",
            "reference": "",
            "operating_point": self.bar.pk,
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Expense.objects.filter(payee="KPLC").exists())

    def test_staff_sees_own_and_point_expenses_only(self):
        from django.urls import reverse
        mine = self._make_expense(self.bar_user, point=self.bar)
        point_expense = self._make_expense(self.kit_user, point=self.bar)
        other = self._make_expense(self.kit_user, point=self.kitchen)

        self.client.force_login(self.bar_user)
        r = self.client.get(reverse("expenses:expense_list"))
        body = r.content.decode()
        self.assertIn("Electricity", body)  # category name shown in table
        # bar_user can see own + point of bar; not kitchen
        visible_pks = {e.pk for e in r.context["entries"]}
        self.assertIn(mine.pk, visible_pks)
        self.assertIn(point_expense.pk, visible_pks)
        self.assertNotIn(other.pk, visible_pks)

    def test_director_sees_all(self):
        from django.urls import reverse
        a = self._make_expense(self.bar_user, point=self.bar)
        b = self._make_expense(self.kit_user, point=self.kitchen)
        self.client.force_login(self.director)
        r = self.client.get(reverse("expenses:expense_list"))
        visible = {e.pk for e in r.context["entries"]}
        self.assertIn(a.pk, visible)
        self.assertIn(b.pk, visible)

    def test_staff_cannot_view_other_points_expense(self):
        from django.urls import reverse
        other = self._make_expense(self.kit_user, point=self.kitchen)
        self.client.force_login(self.bar_user)
        r = self.client.get(reverse("expenses:expense_detail", args=[other.pk]))
        self.assertEqual(r.status_code, 403)

    def test_non_director_cannot_approve(self):
        from django.urls import reverse
        e = self._make_expense(self.bar_user, point=self.bar)
        self.client.force_login(self.bar_user)
        r = self.client.post(reverse("expenses:expense_approve", args=[e.pk]))
        self.assertEqual(r.status_code, 403)
        e.refresh_from_db()
        self.assertEqual(e.status, Expense.Status.PENDING)

    def test_non_director_cannot_reject(self):
        from django.urls import reverse
        e = self._make_expense(self.bar_user, point=self.bar)
        self.client.force_login(self.bar_user)
        r = self.client.post(
            reverse("expenses:expense_reject", args=[e.pk]),
            {"reason": "no"})
        self.assertEqual(r.status_code, 403)

    def test_non_director_cannot_delete(self):
        from django.urls import reverse
        e = self._make_expense(self.bar_user, point=self.bar)
        self.client.force_login(self.bar_user)
        r = self.client.post(reverse("expenses:expense_delete", args=[e.pk]))
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Expense.objects.filter(pk=e.pk).exists())

    # ---- CRUD ----

    def test_create_creates_pending_expense(self):
        from decimal import Decimal
        from django.urls import reverse
        self.client.force_login(self.director)
        self.client.post(reverse("expenses:expense_create"), {
            "category": self.electricity.pk,
            "amount": "2000.00",
            "expense_date": "2026-10-14",
            "payment_method": "MPESA",
            "payee": "Vendor",
            "description": "",
            "reference": "",
            "operating_point": "",
        })
        e = Expense.objects.get(payee="Vendor")
        self.assertEqual(e.status, Expense.Status.PENDING)
        self.assertEqual(e.entered_by, self.director)
        self.assertEqual(e.amount, Decimal("2000.00"))

    def test_edit_own_pending_expense(self):
        from decimal import Decimal
        from django.urls import reverse
        e = self._make_expense(self.bar_user, point=self.bar)
        self.client.force_login(self.bar_user)
        r = self.client.post(reverse("expenses:expense_edit", args=[e.pk]), {
            "category": self.electricity.pk,
            "amount": "1234.56",
            "expense_date": e.expense_date.isoformat(),
            "payment_method": "CASH",
            "payee": e.payee,
            "description": "",
            "reference": "",
            "operating_point": self.bar.pk,
        })
        self.assertEqual(r.status_code, 302)
        e.refresh_from_db()
        self.assertEqual(e.amount, Decimal("1234.56"))

    def test_staff_cannot_edit_other_users_expense(self):
        from django.urls import reverse
        e = self._make_expense(self.kit_user, point=self.bar)
        self.client.force_login(self.bar_user)
        r = self.client.get(reverse("expenses:expense_edit", args=[e.pk]))
        self.assertEqual(r.status_code, 403)

    def test_cannot_edit_approved_expense(self):
        from django.urls import reverse
        from django.utils import timezone
        e = self._make_expense(self.bar_user, point=self.bar)
        e.status = Expense.Status.APPROVED
        e.approved_by = self.director
        e.approved_at = timezone.now()
        e.save()
        self.client.force_login(self.director)
        r = self.client.get(reverse("expenses:expense_edit", args=[e.pk]))
        self.assertEqual(r.status_code, 403)

    def test_director_approve_sets_status_and_approver(self):
        from django.urls import reverse
        e = self._make_expense(self.bar_user, point=self.bar)
        self.client.force_login(self.director)
        r = self.client.post(
            reverse("expenses:expense_approve", args=[e.pk]),
            {"notes": "OK"})
        self.assertEqual(r.status_code, 302)
        e.refresh_from_db()
        self.assertEqual(e.status, Expense.Status.APPROVED)
        self.assertEqual(e.approved_by, self.director)
        self.assertIsNotNone(e.approved_at)
        self.assertEqual(e.approval_notes, "OK")

    def test_director_reject_sets_status_and_reason(self):
        from django.urls import reverse
        e = self._make_expense(self.bar_user, point=self.bar)
        self.client.force_login(self.director)
        r = self.client.post(
            reverse("expenses:expense_reject", args=[e.pk]),
            {"reason": "Out of budget"})
        self.assertEqual(r.status_code, 302)
        e.refresh_from_db()
        self.assertEqual(e.status, Expense.Status.REJECTED)
        self.assertEqual(e.approval_notes, "Out of budget")

    def test_director_delete_removes_pending_expense(self):
        from django.urls import reverse
        e = self._make_expense(self.bar_user, point=self.bar)
        e_pk = e.pk
        self.client.force_login(self.director)
        r = self.client.post(reverse("expenses:expense_delete", args=[e_pk]))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Expense.objects.filter(pk=e_pk).exists())

    def test_cannot_delete_approved_expense(self):
        from django.urls import reverse
        from django.utils import timezone
        e = self._make_expense(self.bar_user, point=self.bar)
        e.status = Expense.Status.APPROVED
        e.approved_by = self.director
        e.approved_at = timezone.now()
        e.save()
        self.client.force_login(self.director)
        r = self.client.post(reverse("expenses:expense_delete", args=[e.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Expense.objects.filter(pk=e.pk).exists())

    # ---- Filters ----

    def test_filter_by_period(self):
        from django.urls import reverse
        from django.utils import timezone
        from datetime import timedelta
        old = self._make_expense(self.bar_user, point=self.bar)
        old.expense_date = timezone.localdate() - timedelta(days=60)
        old.save()
        self.client.force_login(self.director)
        r = self.client.get(reverse("expenses:expense_list") + "?days=7")
        self.assertNotIn(old.pk, {e.pk for e in r.context["entries"]})

    def test_filter_by_category(self):
        from django.urls import reverse
        other_cat, _ = ExpenseCategory.objects.get_or_create(
            code="TST_WATER", defaults={"name": "Water", "parent": self.utilities})
        e1 = self._make_expense(self.bar_user, category=self.electricity)
        e2 = self._make_expense(self.bar_user, category=other_cat)
        self.client.force_login(self.director)
        r = self.client.get(
            reverse("expenses:expense_list") + "?category=TST_ELEC")
        pks = {e.pk for e in r.context["entries"]}
        self.assertIn(e1.pk, pks)
        self.assertNotIn(e2.pk, pks)

    def test_filter_by_status(self):
        from django.urls import reverse
        e = self._make_expense(self.bar_user, point=self.bar)
        self.client.force_login(self.director)
        r = self.client.get(reverse("expenses:expense_list") + "?status=APPROVED")
        self.assertNotIn(e.pk, {x.pk for x in r.context["entries"]})

    def test_empty_state_renders(self):
        from django.urls import reverse
        Expense.objects.all().delete()
        self.client.force_login(self.director)
        r = self.client.get(reverse("expenses:expense_list"))
        self.assertContains(r, "No expenses")

    # ---- Form validation ----

    def test_negative_amount_rejected_by_form(self):
        from django.urls import reverse
        from decimal import Decimal
        self.client.force_login(self.director)
        r = self.client.post(reverse("expenses:expense_create"), {
            "category": self.electricity.pk,
            "amount": "-100",
            "expense_date": "2026-10-14",
            "payment_method": "CASH",
            "payee": "",
            "description": "",
            "reference": "",
            "operating_point": "",
        })
        self.assertEqual(r.status_code, 200)  # re-rendered with errors
        self.assertFalse(
            Expense.objects.filter(amount=Decimal("-100")).exists())

    def test_category_required(self):
        from django.urls import reverse
        self.client.force_login(self.director)
        r = self.client.post(reverse("expenses:expense_create"), {
            "amount": "100",
            "expense_date": "2026-10-14",
            "payment_method": "CASH",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Expense.objects.filter(amount=100).exists())

    def test_date_required(self):
        from django.urls import reverse
        self.client.force_login(self.director)
        r = self.client.post(reverse("expenses:expense_create"), {
            "category": self.electricity.pk,
            "amount": "100",
            "payment_method": "CASH",
        })
        self.assertEqual(r.status_code, 200)

class RecurringAlgorithmTests(TestCase):
    """Pure-function tests for advance_due_date."""

    def _advance(self, current_iso, freq, due_day):
        from datetime import date
        from expenses.management.commands.generate_due_recurring_expenses import advance_due_date
        from expenses.models import RecurringExpense
        current = date.fromisoformat(current_iso)
        freq_enum = getattr(RecurringExpense.Frequency, freq)
        return advance_due_date(current, freq_enum, due_day)

    # ---- WEEKLY ----

    def test_weekly_same_weekday(self):
        # 2026-10-13 is a Tuesday (weekday 1)
        result = self._advance("2026-10-13", "WEEKLY", 1)
        self.assertEqual(result.isoformat(), "2026-10-20")

    def test_weekly_different_weekday(self):
        # From Tuesday 2026-10-13, next Monday (weekday 0) is 2026-10-19
        result = self._advance("2026-10-13", "WEEKLY", 0)
        self.assertEqual(result.isoformat(), "2026-10-19")

    # ---- MONTHLY ----

    def test_monthly_normal(self):
        result = self._advance("2026-10-15", "MONTHLY", 15)
        self.assertEqual(result.isoformat(), "2026-11-15")

    def test_monthly_rolls_year(self):
        result = self._advance("2026-12-10", "MONTHLY", 10)
        self.assertEqual(result.isoformat(), "2027-01-10")

    def test_monthly_clamps_to_feb_28(self):
        # Due on the 31st; from 2026-01-31, next month is February
        result = self._advance("2026-01-31", "MONTHLY", 31)
        self.assertEqual(result.isoformat(), "2026-02-28")

    def test_monthly_clamps_to_feb_29_leap_year(self):
        # 2028 is a leap year
        result = self._advance("2028-01-31", "MONTHLY", 31)
        self.assertEqual(result.isoformat(), "2028-02-29")

    # ---- QUARTERLY ----

    def test_quarterly_normal(self):
        result = self._advance("2026-01-15", "QUARTERLY", 15)
        self.assertEqual(result.isoformat(), "2026-04-15")

    def test_quarterly_rolls_year(self):
        result = self._advance("2026-11-10", "QUARTERLY", 10)
        self.assertEqual(result.isoformat(), "2027-02-10")

    def test_quarterly_clamps(self):
        # Due on 31st, quarterly from Oct 31 -> Jan 31 (Jan has 31 days) -> Apr 30
        result = self._advance("2026-01-31", "QUARTERLY", 31)
        self.assertEqual(result.isoformat(), "2026-04-30")

    # ---- ANNUAL ----

    def test_annual_normal(self):
        result = self._advance("2026-03-15", "ANNUAL", 15)
        self.assertEqual(result.isoformat(), "2027-03-15")

    def test_annual_clamps_leap(self):
        # Feb 29 in a non-leap year
        result = self._advance("2028-02-29", "ANNUAL", 29)
        self.assertEqual(result.isoformat(), "2029-02-28")


class RecurringCommandTests(TestCase):
    """Integration tests for generate_due_recurring_expenses."""


    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal
        from datetime import date
        from django.contrib.auth import get_user_model
        from accounts.models import RoleDefinition
        User = get_user_model()
        dir_role, _ = RoleDefinition.objects.get_or_create(
            code="DIRECTOR", defaults={"name": "Director"})
        cls.director = User.objects.create_user(
            username="rec_dir", password="pw", role="DIRECTOR",
            role_definition=dir_role, is_active=True, is_active_staff=True)
        cls.cat, _ = ExpenseCategory.objects.get_or_create(
            code="TST_REC", defaults={"name": "Recurring test"})

    def _template(self, **overrides):
        from decimal import Decimal
        from datetime import date
        defaults = {
            "category": self.cat,
            "amount": Decimal("4500.00"),
            "payee": "Test payee",
            "payment_method": "CASH",
            "frequency": _RecurringExpense_import.Frequency.MONTHLY,
            "due_day": 25,
            "is_active": True,
            "start_date": _date_import(2026, 1, 1),
            "next_due_date": _date_import(2026, 10, 25),
            "created_by": self.director,
        }
        defaults.update(overrides)
        return _RecurringExpense_import.objects.create(**defaults)

    def _run(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("generate_due_recurring_expenses", *args, stdout=out)
        return out.getvalue()

    def test_generates_expense_when_due(self):
        t = self._template()
        Expense.objects.all().delete()
        self._run("--up-to", "2026-10-25")
        self.assertEqual(
            Expense.objects.filter(recurring_source=t).count(), 1)

    def test_generated_expense_is_pending(self):
        t = self._template()
        Expense.objects.all().delete()
        self._run("--up-to", "2026-10-25")
        e = Expense.objects.get(recurring_source=t)
        self.assertEqual(e.status, Expense.Status.PENDING)
        self.assertEqual(e.category, self.cat)
        self.assertEqual(str(e.amount), "4500.00")

    def test_advances_next_due_date(self):
        t = self._template()
        self._run("--up-to", "2026-10-25")
        t.refresh_from_db()
        self.assertEqual(t.next_due_date.isoformat(), "2026-11-25")

    def test_no_generation_before_due(self):
        self._template()
        Expense.objects.all().delete()
        self._run("--up-to", "2026-10-24")
        self.assertEqual(Expense.objects.count(), 0)

    def test_idempotent_when_already_generated(self):
        t = self._template()
        self._run("--up-to", "2026-10-25")
        first_count = Expense.objects.filter(recurring_source=t).count()
        # Second run with same up-to — should not double-generate
        self._run("--up-to", "2026-10-25")
        second_count = Expense.objects.filter(recurring_source=t).count()
        self.assertEqual(first_count, second_count)

    def test_catches_up_after_dormancy(self):
        t = self._template(next_due_date=_date_import(2026, 7, 25))
        Expense.objects.all().delete()
        self._run("--up-to", "2026-10-25")
        # Should have generated Aug 25, Sep 25, Oct 25 = 3 expenses
        self.assertEqual(
            Expense.objects.filter(recurring_source=t).count(), 3)
        t.refresh_from_db()
        self.assertEqual(t.next_due_date.isoformat(), "2026-11-25")

    def test_inactive_template_skipped(self):
        self._template(is_active=False)
        Expense.objects.all().delete()
        self._run("--up-to", "2026-10-25")
        self.assertEqual(Expense.objects.count(), 0)

    def test_end_date_deactivates_template(self):
        t = self._template(end_date=_date_import(2026, 10, 1))
        Expense.objects.all().delete()
        self._run("--up-to", "2026-10-25")
        t.refresh_from_db()
        self.assertFalse(t.is_active)
        self.assertEqual(Expense.objects.filter(recurring_source=t).count(), 0)

    def test_dry_run_writes_nothing(self):
        self._template()
        Expense.objects.all().delete()
        self._run("--up-to", "2026-10-25", "--dry-run")
        self.assertEqual(Expense.objects.count(), 0)

    def test_weekly_frequency(self):
        # 2026-10-13 is Tuesday (weekday 1)
        t = self._template(
            frequency=_RecurringExpense_import.Frequency.WEEKLY,
            due_day=1,
            next_due_date=_date_import(2026, 10, 13),
        )
        Expense.objects.all().delete()
        self._run("--up-to", "2026-10-13")
        self.assertEqual(
            Expense.objects.filter(recurring_source=t).count(), 1)
        t.refresh_from_db()
        self.assertEqual(t.next_due_date.isoformat(), "2026-10-20")