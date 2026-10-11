from django.db import migrations


class Migration(migrations.Migration):
    """
    Drop the orphaned sales_mpesamanualpayment table.

    The MpesaManualPayment model was removed from sales/models.py during
    the Phase B -> v2 refactor, but the table was left behind because no
    migration explicitly deleted it. This migration cleans it up.

    DROP TABLE IF EXISTS is portable across SQLite, PostgreSQL and MySQL.
    We do NOT use CASCADE because:
      - SQLite rejects it (syntax error)
      - no other table references this one, so CASCADE is unnecessary
    """

    dependencies = [
        ("sales", "0005_mpesapayment_mpesapaymentallocation"),
    ]

    operations = [
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS sales_mpesamanualpayment;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]