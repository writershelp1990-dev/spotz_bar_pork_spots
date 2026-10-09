# SPOTZ BAR & PORK SPOTS
# Phase 1 — Business-Day Design
# DESIGN ONLY — NO MODEL OR MIGRATION

**Status:** Design proposal for approval  
**Phase:** Phase 1  
**Scope:** Business-day definition, lifecycle, staff coverage and reporting rules  
**Implementation status:** NOT IMPLEMENTED  
**Database status:** NO DATABASE CHANGES  
**Migration status:** NO BUSINESS-DAY MIGRATION  
**Business timezone:** Africa/Nairobi  
**Currency/business context:** Kenya

---

## 1. Purpose

The Business-Day system will provide the financial and operational boundary used by SPOTZ BAR & PORK SPOTS for sales, payments, stock activity, expenses, staff deployment, daily closing, reporting and profit/loss calculations.

This document defines the intended behavior before any `BusinessDay` model, migration, service, URL, view or database change is created.

---

# 2. Core Business-Day Definition

A business day represents one operational trading date for SPOTZ BAR & PORK SPOTS.

The business date is based on the local calendar date in:

**Africa/Nairobi**

A business day is identified by its business date, for example:

- 2026-10-07
- 2026-10-08
- 2026-10-09

The business date is NOT determined simply by the timestamp at which an individual transaction occurs.

Transactions occurring during an approved open business-day window belong to the currently open business day.

Historical transactions remain attached to the business day under which they were legitimately recorded.

---

# 3. Normal Business Operating Schedule

## 3.1 General business opening

The business normally operates every day.

The general business opening time is:

**08:00 Africa/Nairobi**

This represents the normal start of the business day.

The system must nevertheless allow the Director to open a business day earlier than 08:00 when operational circumstances require it.

Therefore:

**08:00 is the normal opening time, not an absolute earliest possible opening time.**

---

# 4. Bar Operating Schedule

The bar has different normal opening times depending on the day.

## Weekdays

Monday through Friday:

**Normal bar opening: 16:00**

## Weekends

Saturday and Sunday:

**Normal bar opening: 14:00**

## Public holidays

On a recognized public holiday:

**Normal bar opening: 14:00**

The bar's schedule is therefore:

| Day | Normal bar opening |
|---|---:|
| Monday | 16:00 |
| Tuesday | 16:00 |
| Wednesday | 16:00 |
| Thursday | 16:00 |
| Friday | 16:00 |
| Saturday | 14:00 |
| Sunday | 14:00 |
| Public holiday | 14:00 |

These are normal operating times.

They are NOT intended to prevent authorized early opening.

---

# 5. Early Opening

The system must allow the Director to open the business day before the normal opening time.

Examples:

- Normal business opening is 08:00, but the Director may open at 07:30.
- Normal bar opening is 16:00, but the Director may authorize the bar to operate at 15:00.
- Normal weekend/holiday bar opening is 14:00, but the Director may authorize an earlier opening.

Early opening must be recorded as an operational exception.

The system should preserve:

- business date
- actual opening timestamp
- user who opened the day
- reason for early opening
- normal scheduled opening time for comparison/reporting

The system must never rewrite the normal schedule merely because an early opening occurred.

---

# 6. Normal Closing Time

The normal closing hour is:

**23:00 Africa/Nairobi**

23:00 is the normal closing target.

It is not an absolute forced shutdown.

---

# 7. Late Closing

The system must allow authorized late closing.

Examples:

- Normal close: 23:00
- Actual close: 23:30
- Actual close: 00:15
- Actual close: 01:00

A late close must be recorded as an exception.

The system should preserve:

- business date
- normal closing time
- actual closing timestamp
- user who closed the day
- reason for late closing

The system must not automatically move transactions after midnight into a new business day merely because the calendar date changed.

If the previous business day remains legitimately open, its transactions continue to belong to that business day until it is closed.

---

# 8. Business-Day Lifecycle

The intended lifecycle is:

**NOT OPEN → OPEN → CLOSING → CLOSED**

A simpler implementation may use:

- OPEN
- CLOSED

with timestamps recording the opening and closing events.

The preferred design is:

### OPEN

The business day is available for authorized operational activity.

Transactions may be recorded subject to their own permissions and controls.

### CLOSING

This state may be used during the future daily-closing workflow when reconciliation is being performed.

No new financial activity should normally be accepted once the closing process has begun unless specifically authorized.

### CLOSED

The business day is financially locked.

Closed business days are locked and cannot be changed through ordinary operations.

Normal users cannot create, edit, void, adjust or otherwise alter transactions belonging to that closed business day.

---

# 9. Who May Open a Business Day

The Director is the primary authorized user for opening a business day.

The design should support future authorization of another senior role if the Director later chooses to delegate this responsibility.

For the current Phase 1 design:

**Director only.**

The opening action must record:

- user
- timestamp
- business date
- scheduled opening reference
- actual opening timestamp
- early-opening indicator
- reason when opened early

---

# 10. Who May Close a Business Day

For the current design:

**Director only.**

Closing must record:

- user
- timestamp
- business date
- normal closing target
- actual closing timestamp
- late-closing indicator
- closing/reconciliation information
- any required closing notes

---

# 11. Who May Reopen a Closed Business Day

A closed business day is locked.

Only the Director may reopen it.

Reopening must require:

- Director authorization
- reason
- timestamp
- user
- affected business date

A reopen action must create an audit record.

The system must never silently reopen a closed day.

---

# 12. Accidentally Open Previous Business Day

The system must detect when a previous business day remains open when a new calendar date has begun.

Example:

Business day:

**2026-10-06**

remains open at:

**2026-10-07 08:00**

The system must NOT automatically close it.

The system must NOT automatically create a second overlapping open business day.

Instead, the Director should see a clear warning such as:

> Previous business day 2026-10-06 is still open.

The Director should then be able to:

1. Continue the previous business day temporarily.
2. Complete its closing process.
3. Correct the situation through an authorized reopening/closing workflow if necessary.

The system should prevent two ordinary business days from being simultaneously open unless a future approved design explicitly supports that behavior.

---

# 13. Business-Day Overlap Rule

There should normally be:

**No more than one OPEN business day for this business.**

A calendar date changing does not automatically create a new open business day.

The Director must explicitly open the new business day.

This prevents:

- duplicate daily totals
- transactions assigned to the wrong day
- overlapping daily reports
- accidental revenue duplication
- closing/reconciliation conflicts

---

# 14. Relationship Between Transactions and Business Day

Future transaction models should contain a direct relationship to the applicable business day.

Examples include:

- Sales
- Payments
- Purchases
- Stock movements
- Stock issues
- Wastage
- Expenses
- Payroll-related operational records where applicable

The business day relationship must be explicit rather than inferred later from timestamps.

Example:

A sale at:

**2026-10-08 00:15**

may still belong to:

**Business Day 2026-10-07**

if the 2026-10-07 business day remains legitimately open and the transaction is recorded before its closing.

This is necessary because late closing is explicitly supported.

---

# 15. Closed-Day Transaction Locking

Once a business day is CLOSED:

Normal users must not be able to:

- create new financial transactions for that day
- edit historical financial transactions
- delete completed transactions
- alter payment allocations
- alter expense records
- change historical stock movements
- alter historical wastage
- silently move transactions to another business day

If a correction is required:

- the correction must follow the appropriate transaction-specific control
- the action must be authorized
- the original historical record must remain preserved
- the audit trail must identify what happened

Closed-day reopening is therefore an exceptional Director action.

---

# 16. Business-Day Reporting

Reports should be capable of being generated by business date.

At minimum, future daily reporting should support:

- total sales
- sales by operating point
- sales by employee where appropriate
- sales by product/category
- payments by method
- cash totals
- M-Pesa totals
- card totals
- other payments
- purchases
- stock movement
- stock issues
- wastage
- expenses
- payroll-related costs
- variances
- estimated gross profit
- estimated net profit

Reports must use the business-day relationship rather than relying solely on calendar timestamps.

---

# 17. Scheduled Hours Versus Actual Hours

The system should preserve the distinction between:

**Scheduled time**

and

**Actual operational time**

For example:

Normal business opening:

08:00

Actual opening:

07:35

The system should retain both.

Likewise:

Normal closing:

23:00

Actual closing:

00:20

The system should retain both.

This makes early-opening and late-closing exceptions visible in management reports.

---

# 18. Public Holidays

Public holidays must be treated as a schedule classification.

On public holidays:

- the normal bar opening target becomes 14:00
- the business remains capable of normal 08:00 operational activity where applicable
- early opening remains possible
- late closing remains possible

The exact public-holiday calendar source and administrative mechanism should be finalized before implementation.

The system should not hard-code individual holiday dates directly into transaction logic.

The preferred future design is for holiday information to be maintained as business-calendar configuration or another controlled source.

---

# 19. Staff Availability and Business-Day Operations

The Business-Day design must work together with employee lifecycle status.

The Director needs a management view showing staff availability for the current business day.

At minimum the Director should be able to see:

- ACTIVE staff
- OFF_DUTY staff
- ON_LEAVE staff
- SUSPENDED staff
- TERMINATED staff
- INVITED staff where relevant

The availability view should make it easy to identify staffing gaps before or during daily operations.

---

# 20. Staff on Leave

A staff member whose effective employment status is ON_LEAVE must not be treated as available for ordinary deployment.

The system should clearly identify:

- employee
- leave period
- operating point normally assigned
- replacement/coverage requirement
- current coverage arrangement where applicable

Leave should not be silently changed to OFF_DUTY simply because a replacement is deployed.

The employment status remains historically accurate.

---

# 21. Staff Off Duty

A staff member who is OFF_DUTY is not normally available for deployment.

The Director must nevertheless be able to authorize a deployment when operationally required, if future employee-deployment rules permit this.

Any such exception should be explicitly recorded.

The system should distinguish:

**Employment status**

from

**Today's deployment/coverage assignment.**

This prevents a temporary deployment from rewriting the employee's actual employment status.

---

# 22. Replacement Staff

The Director must have a controlled way to deploy a replacement when an assigned staff member is:

- OFF_DUTY
- ON_LEAVE

The replacement assignment should record:

- replacement employee
- operating point
- business date
- start time
- end time where applicable
- employee being covered
- reason
- Director/user who authorized the deployment

The original employee's employment status must remain unchanged.

Example:

Employee A is ON_LEAVE.

Employee B is deployed to cover Employee A at CHOMA.

The system records:

**Employee A — ON_LEAVE**

and separately:

**Employee B — coverage deployment for CHOMA**

---

# 23. One Employee Covering Multiple Operating Points

The system must support the Director deploying one employee to cover two or more operating points when operationally appropriate.

Example:

Employee B may be deployed to:

- BAR
- CHOMA

during the same business day.

The deployment design must therefore NOT assume:

**one employee = one operating point per day.**

Instead, future deployment records should allow multiple assignments.

Each assignment should identify:

- employee
- operating point
- business date
- start time
- end time
- assignment type
- whether it is normal deployment or temporary coverage
- Director authorization where required

---

# 24. Multiple-Point Coverage Controls

Although multiple-point deployment is allowed, the system should provide visibility over overlapping assignments.

Example:

Employee B:

BAR: 16:00–19:00

CHOMA: 18:00–22:00

This creates a one-hour overlap.

The system should warn the Director about overlapping deployment assignments.

The system should not automatically prohibit every overlap because management may intentionally use flexible coverage arrangements.

However, the Director should be required to consciously confirm exceptional or overlapping deployment where appropriate.

---

# 25. Staff Coverage Dashboard

The future Director management area should include a staffing/coverage view.

It should show at minimum:

### Available

- ACTIVE and available staff

### Off Duty

- OFF_DUTY staff

### On Leave

- ON_LEAVE staff
- leave start/end dates

### Assigned

- current operating-point assignments

### Coverage Needed

- operating points with unavailable assigned staff

### Replacement

- employees currently covering another employee

### Multi-Point

- employees deployed to more than one operating point

This is a management view and should be Director-controlled.

---

# 26. Operating-Point Coverage

The four current operating points are:

- BAR
- BUTCHERY
- CHOMA
- KITCHEN

Security & Facilities remains a support function and is not treated as one of these four operating points.

The future staff-deployment system should nevertheless allow employees whose work supports the business to be represented appropriately without incorrectly converting Security & Facilities into an operating point.

---

# 27. Business-Day and Staff Deployment Relationship

Staff deployment should be associated with the business date.

This allows the system to answer questions such as:

- Who was assigned to BAR on 2026-10-07?
- Who covered CHOMA while the normal attendant was on leave?
- Which employee worked at two operating points?
- Which staff were OFF_DUTY?
- Which staff were ON_LEAVE?
- Who authorized the replacement?
- When did the deployment start and end?

Historical deployment records must not be overwritten when the employee's current assignment changes later.

---

# 28. Daily Opening Workflow — Future Design

The intended future workflow is:

1. Director opens the Business-Day screen.
2. System identifies the current Nairobi calendar date.
3. System checks for an existing OPEN business day.
4. If a previous day remains open, the Director receives a warning.
5. Director reviews staff availability.
6. Director sees ON_LEAVE and OFF_DUTY staff.
7. Director identifies coverage gaps.
8. Director deploys replacements where necessary.
9. Director may deploy one employee to multiple operating points.
10. System records the actual opening timestamp.
11. If opening is earlier than normal, the system records an early-opening exception.
12. Operations proceed under the selected business date.

---

# 29. Daily Closing Workflow — Future Design

The intended future workflow is:

1. Director opens the closing/reconciliation screen.
2. System gathers activity for the business date.
3. System summarizes sales.
4. System summarizes payments.
5. System summarizes purchases.
6. System summarizes stock movements.
7. System summarizes stock issues.
8. System summarizes wastage.
9. System summarizes expenses.
10. System summarizes relevant payroll costs.
11. System calculates relevant variances.
12. Director reviews the day's figures.
13. Director resolves or acknowledges required exceptions.
14. Director closes the business day.
15. The system records actual closing time.
16. If closing occurs after 23:00, the system records a late-closing exception.
17. The business day becomes CLOSED and financial records are locked.

---

# 30. Audit Requirements

Opening, closing and reopening are controlled events.

Future audit entries should record:

### Open

- user
- business date
- timestamp
- early-opening status
- reason

### Close

- user
- business date
- timestamp
- late-closing status
- reason/notes

### Reopen

- user
- business date
- timestamp
- reason

### Staff deployment

- authorizing user
- employee
- operating point
- business date
- assignment/coverage type
- start/end time
- replacement relationship where applicable
- reason

Audit records must remain append-only.

---

# 31. Security Rules

Business-day state must never be controlled only through the browser interface.

Authorization must be enforced server-side.

A user attempting to bypass the interface must still be unable to:

- close a business day without authorization
- reopen a business day without authorization
- alter closed-day financial records
- create unauthorized deployment records
- modify historical deployment records
- change another employee's employment status without the required permission

---

# 32. No Automatic Historical Rewriting

The system must not rewrite history when:

- an employee changes operating points
- an employee goes on leave
- an employee returns from leave
- an employee becomes OFF_DUTY
- a replacement is deployed
- an operating point changes
- a business schedule changes

Historical records must retain the state applicable when the event occurred.

---

# 33. Recommended Future Data Concepts

This design does NOT create these models.

The eventual implementation may require concepts such as:

- BusinessDay
- BusinessDayOpening/OpeningEvent
- BusinessDayClosing/ClosingEvent
- StaffDeployment
- StaffCoverage
- Holiday/BusinessCalendar configuration

The exact model boundaries must be designed and approved before implementation.

No model should be created merely because it appears in this design document.

---

# 34. Explicit Phase 1 Boundary

This design phase MUST NOT implement:

- `BusinessDay` model
- BusinessDay migration
- StaffDeployment model
- StaffCoverage model
- Holiday model
- Business-day URLs
- Business-day views
- business-day forms
- daily-closing code
- transaction locking code
- sales
- payments
- inventory
- stock movements
- wastage
- expenses
- payroll

Those belong to later controlled implementation phases unless separately approved.

---

# 35. Design Decisions Requiring Approval Before Implementation

The following are the intended decisions to carry into implementation:

1. Business timezone: **Africa/Nairobi**
2. Business normally opens: **08:00 daily**
3. Bar normally opens:
   - **16:00 Monday–Friday**
   - **14:00 Saturday/Sunday**
   - **14:00 public holidays**
4. Early opening is allowed.
5. Normal closing target: **23:00**.
6. Late closing is allowed.
7. One ordinary OPEN business day at a time.
8. Calendar midnight does not automatically close an open business day.
9. Previous-day open status produces a Director warning.
10. Director controls opening.
11. Director controls closing.
12. Director controls reopening.
13. Closed business days are locked.
14. Transactions explicitly belong to a business day.
15. Historical records are never silently rewritten.
16. Director can see staff who are ON_LEAVE and OFF_DUTY.
17. Director can deploy replacement staff for unavailable staff.
18. One employee may cover multiple operating points.
19. Deployment history is separate from employment status.
20. Deployment conflicts/overlaps should be visible to the Director.
21. Opening, closing, reopening and deployment actions require auditability.
22. No BusinessDay model or migration is created during this design phase.

---

# 36. Current Status

**BUSINESS-DAY DESIGN: READY FOR REVIEW**

No implementation has been performed.

No `BusinessDay` model exists as a result of this design.

No migration has been created or applied as a result of this design.

No production PostgreSQL migration has been performed.

The next controlled step after approval is:

**DESIGN APPROVAL → IMPLEMENTATION PLAN → BUILD**

and not direct migration.


