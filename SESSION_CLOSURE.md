# SPOTZ Management System — Session Closure

**Generated:** 2026-10-13 10:11:36

## Purpose

This document records the areas to verify before considering the current
development session complete. It is not a claim that every listed feature
has been independently verified in the live environment.

## Property and assets

Review the Property & Assets app for:

- Durable asset registration and location tracking.
- Area-based staff authorization.
- Daily property checks and condition reports.
- New-item reporting and asset relocation.
- Director reporting and audit history.

Verify the current models, migration status, browser workflows, and tests.

## Low-stock notifications

Verify:

- Threshold configuration per product and operating point.
- Correct alert creation and resolution after restocking.
- Notification recipients and permission scoping.
- The scheduled task configuration and its actual execution history.

Do not assume a scheduled task is running merely because its command exists.

## Stock movements and production

Verify:

- Internal transfers and their approval workflow.
- Stock ledger entries and stock balances.
- Recipe ingredient consumption.
- Production batches and output stock.
- Wastage records and reconciliation.
- Prevention of negative stock and duplicate deductions.

Internal allocations are not customer sales. Confirm that each workflow
updates stock exactly once and preserves an auditable history.

## Receiving, invoices, and payments

Verify:

- Attendant delivery submissions and Director verification.
- Receipt rejection without an unintended stock increase.
- Supplier invoice creation and reuse.
- Partial payments, full payments, and outstanding balances.
- Permissions on invoice and payment information.
- Cost-of-goods-sold calculations and profit reports.

Never reverse a live migration merely to hide a feature. Review the
rollback guide and back up the database before any schema rollback.

## Tests and migration status

Run:

`powershell
python manage.py check
python manage.py showmigrations
python manage.py test -v 1
`

Save the actual command output and confirm whether migrations are applied
to the intended database. A passing test database does not establish
that the live database has the same schema or data.

## Git review

Inspect the current working tree and available tags:

`powershell
git status --short
git log -5 --oneline
git tag --list
`

Review existing tags before relying on them as rollback points. Create
new commits or tags only after verifying the intended files and tests.

## Recommended completion criteria

- [ ] Current migrations inspected and verified.
- [ ] Database backup created and restore procedure tested.
- [ ] Focused tests pass.
- [ ] Full test suite result recorded.
- [ ] Receiving, stock, payment, and reporting workflows checked in a browser.
- [ ] Role permissions checked using appropriate test accounts.
- [ ] Git changes reviewed and committed intentionally.
- [ ] Documentation matches the implementation currently in the repository.

## Reference documents

- stock/ROLLBACK.md — rollback risks and recovery planning.
- stock/MODULE.md — stock-module inspection guide.

## Scope of this documentation step

This step writes documentation only. It does not apply or reverse database
migrations, delete business data, run tests, amend commits, or create tags.