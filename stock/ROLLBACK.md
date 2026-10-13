# Stock and Receiving Module — Rollback Guide

**Purpose:** Reference for reviewing rollback options. This document does
not authorize or execute a rollback.

## Before changing migrations

1. Stop and inspect the running application.
2. Confirm the active database and environment.
3. Review `python manage.py showmigrations`.
4. Review migration dependencies before reversing anything.
5. Create a verified database backup and confirm that it can be restored.
6. Review the current Git status and save any uncommitted work.
7. Test the rollback against a separate database before production use.

## Important data-loss warning

Reversing a migration can drop tables or columns and permanently remove
the data stored in them. A successful reverse migration is not proof that
the application can safely operate at the older schema.

The exact effects depend on the migration files actually present in this
checkout. Inspect those files before relying on the descriptions below.

## Receiving and payment workflow

The receiving workflow may involve models such as:

- `StockReceipt`
- `SupplierInvoice`
- `InvoicePayment`

If a migration creates these tables, reversing it may remove receipts,
invoice records, payment records, and other stored values. If a migration
adds `unit_cost` to `StockLedger`, reversing it may remove that column
and its values.

Do not assume that ledger rows, quantities, sales, or audit records are
unaffected without inspecting the actual migration operations and
database constraints.

## Stock workflow

The stock module may include transfers, recipes, production batches,
ingredient consumption, and wastage records.

Reversing the stock app's initial migration may drop its tables. Other
migrations may modify existing sales tables. Check the dependency graph
before choosing a target migration.

Do not delete ledger rows manually as a routine rollback step. Such
deletion can destroy historical evidence and make stock reconciliation
more difficult.

## Safer alternatives

If the goal is only to disable a feature:

- Hide or restrict the relevant interface through a reviewed code change.
- Keep the existing database schema and historical data.
- Test the change before deploying it.
- Preserve audit records and transaction history.

A Git checkout changes the working tree, not the database schema. It does
not reverse migrations already applied to a database.

## Recovery after a mistaken rollback

If a migration was reversed accidentally:

1. Stop writes to the affected database.
2. Determine which tables, columns, and records were removed.
3. Preserve the current database state for investigation.
4. Restore a backup to a separate database first.
5. Compare the restored state with the current database before recovery.
6. Reapply migrations only after confirming that the code and schema are
   compatible.

Restoring a full backup can discard changes made after the backup.
Migrations generally recreate schema; they cannot reconstruct deleted
business records without a backup or another reliable source.

## Git rollback points

Inspect tags that actually exist in the repository before using them:

`git tag --list`

Review a tag before checking it out:

`git show --stat <tag>`

Do not assume that a tag exists or that it corresponds to a particular
database schema until its commit and migration files have been inspected.

## Final warning

This guide is informational. Do not run reverse migrations against a
live database until the effects, dependencies, backup, and recovery
procedure have been verified.