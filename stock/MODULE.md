# Stock Module — Reference

**Generated:** 2026-10-13 10:11:36

This document is a starting point for inspecting the stock and receiving
implementation in the current checkout. Model names, services, routes,
permissions, migrations, and test counts must be verified against the
actual source code before this document is treated as an authoritative
technical specification.

## Application overview

The feature area may span two Django apps:

- stock — stock movements, internal transfers, recipes, production,
  and wastage.
- sales — stock ledger, receiving, supplier invoices, payments, sales,
  and reporting.

Confirm the installed apps and implementation in config/settings.py
and the relevant app files.

## Model discovery

Inspect these files if present:

- stock/models.py
- sales/models.py

Look for the actual definitions of:

- Stock transfers and their statuses.
- Recipes and recipe ingredients.
- Production batches and ingredient consumption.
- Stock ledger entries and stock balances.
- Stock receipts, supplier invoices, and invoice payments.
- Stock alerts and audit records.

Do not infer fields or relationships from this document alone.

## Service discovery

Inspect stock/services.py, stock/recipes.py, sales/services.py,
and sales/costing.py if present.

Verify the actual behavior of:

- Creating, approving, completing, and cancelling transfers.
- Recording and verifying deliveries.
- Recording invoice payments and recalculating balances.
- Consuming recipe ingredients during checkout.
- Recording wastage.
- Calculating cost of goods sold and profit.

Confirm that stock-changing operations use transactions where appropriate,
validate available quantities, and prevent duplicate deductions.

## Permissions and data access

Inspect the actual permission helpers, decorators, views, and querysets.

Verify that:

- Staff access is restricted to authorized operating points.
- Director-only actions are enforced server-side.
- Payment and supplier financial details are not exposed to unauthorized
  users.
- Rejected or pending receipts do not incorrectly increase stock.
- Only a successful, verified delivery changes stock balances.
- Audit history is preserved for consequential actions.

## Routes and templates

Inspect the current URL configuration and template directories rather
than relying on assumed route names.

Useful discovery commands:

`powershell
Get-ChildItem stock,sales -Recurse -File |
    Where-Object {
        .Extension -in '.py','.html'
    } |
    Select-Object -ExpandProperty FullName
`

`powershell
Select-String -Path stock\\*.py,sales\\*.py 
    -Pattern 'def receive_stock|def record_delivery|def verify_delivery|def checkout|def create_transfer|def record_wastage'
`

## Tests and verification

Run the focused tests first, then the full suite:

`powershell
python manage.py test stock sales -v 1
`

`powershell
python manage.py test -v 1
`

A test count quoted in an older report is not proof of the current test
count or pass status. Record the actual output from the current checkout.

Check migrations and configuration separately:

`powershell
python manage.py check
python manage.py showmigrations
`

These commands inspect the project; showmigrations does not apply
migrations.

## Costing and accounting questions to verify

- Is the costing method weighted average, FIFO, or another method?
- How are missing or zero unit costs handled?
- How are partial payments and supplier credits represented?
- Can verified receipts be corrected without deleting history?
- Are production output and ingredient consumption recorded atomically?
- Does the profit report distinguish revenue, cost of goods sold, and
  gross profit correctly?

## Documentation maintenance

Update this file when the actual implementation changes. Document verified
model fields, migration names, route names, permissions, and test results.
Clearly distinguish implemented behavior from planned work.