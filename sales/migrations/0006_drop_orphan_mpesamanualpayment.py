from django.db import migrations


class Migration(migrations.Migration):
    """
    Drop the orphaned sales_mpesamanualpayment table.

    The MpesaManualPayment model was removed from sales/models.py during
    the Phase B -> v2 refactor, but the table was left behind because no
    migration explicitly deleted it. This migration cleans it up.

    DROP TABLE IF EXISTS makes this idempotent (safe on fresh installs
    where the table was never created).
    """

    dependencies = [
        ("sales", "0005_mpesapayment_mpesapaymentallocation"),
    ]

    operations = [
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS sales_mpesamanualpayment CASCADE;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]