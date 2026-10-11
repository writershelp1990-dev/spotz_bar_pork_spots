from django.db import migrations


class Migration(migrations.Migration):
    """
    Remove MpesaManualPayment from Django's migration state.

    The table was already dropped by migration 0006 using RunSQL, which
    does NOT update Django's migration state. As a result, the ORM still
    believes the model exists and tries to query sales_mpesamanualpayment
    during cascade delete collection (raising ProgrammingError).

    This migration tells Django's state that the model is gone, without
    touching the database (the table is already dropped).
    """

    dependencies = [
        ("sales", "0006_drop_orphan_mpesamanualpayment"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="MpesaManualPayment"),
            ],
            database_operations=[],   # table already dropped
        ),
    ]