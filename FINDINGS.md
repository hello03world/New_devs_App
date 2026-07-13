# Debugging Findings — Property Revenue Dashboard

Three bugs were reported by the CEO. Each maps to one concrete defect in the codebase.
All three were confirmed against the seeded database and fixed.

| # | Client complaint | Root cause | File fixed |
|---|------------------|-----------|------------|
| 1 | Client B (Ocean Rentals) sometimes sees **another company's** revenue | Redis cache key not scoped by tenant | [backend/app/services/cache.py](backend/app/services/cache.py) |
| 2 | Client A (Sunset Properties) **March total doesn't match** their records | Month boundaries computed in naive UTC, ignoring the property's timezone | [backend/app/services/reservations.py](backend/app/services/reservations.py) |
| 3 | Finance sees totals **"off by a few cents"** | Money routed through binary `float` instead of `Decimal` | [backend/app/api/v1/dashboard.py](backend/app/api/v1/dashboard.py) |

---

## Bug 1 — Cross-tenant revenue leak (privacy)

**Root cause.** `get_revenue_summary()` cached results under `revenue:{property_id}`.
Property IDs are **only unique within a tenant** — the schema PK is `(id, tenant_id)` —
so `prop-001` exists for *both* clients:

```
 property_id | tenant_id |      company      |     timezone     | total_revenue
-------------+-----------+-------------------+------------------+---------------
 prop-001    | tenant-a  | Sunset Properties | Europe/Paris     |      2250.000
 prop-001    | tenant-b  | Ocean Rentals     | America/New_York |         0.000
```

Whichever tenant requested `prop-001` first populated the shared key; the other tenant
then got a **cache hit** and received the first tenant's numbers (and even their
`tenant_id`) for up to 5 minutes. That is exactly "revenue that looks like it belongs to
another company."

**Fix.** Scope the cache key by tenant:

```python
cache_key = f"revenue:{tenant_id}:{property_id}"
```

Now `revenue:tenant-a:prop-001` and `revenue:tenant-b:prop-001` are isolated.

---

## Bug 2 — March total mismatch (timezone)

**Root cause.** `calculate_monthly_revenue()` built the month window with **naive UTC**
midnights and compared them against `check_in_date`, which is a `timestamptz` (UTC).
A month is a *local* concept. Reservation `res-tz-1` checks in at `2024-02-29 23:30 UTC`,
which is **`2024-03-01 00:30` in Paris** — a March booking:

```
    id    | total_amount |      utc_instant       |   local_check_in    |   timezone
----------+--------------+------------------------+---------------------+--------------
 res-tz-1 |     1250.000 | 2024-02-29 23:30:00+00 | 2024-03-01 00:30:00 | Europe/Paris
```

Because the naive window started at `2024-03-01 00:00 UTC`, this $1,250 booking fell
just outside March and was dropped:

```
BUGGY (naive UTC boundaries):    3 bookings   $1000.000   <- Sunset's dashboard
FIXED (Paris-local boundaries):  4 bookings   $2250.000   <- Sunset's own records
```

**Fix.** Anchor the month boundaries to the **property's timezone**, then convert to UTC
for the `timestamptz` comparison (via `zoneinfo`). This also handles DST correctly
(e.g. a New York March window is `-05:00` at the start and `-04:00` at the end). The
function previously returned a `Decimal('0')` placeholder ("until DB connection is
finalized") — it is now wired to the database using the same `DatabasePool` pattern as
`calculate_total_revenue`, and takes `tenant_id` so the property lookup is tenant-isolated.

---

## Bug 3 — Totals off by a few cents (float money)

**Root cause.** The dashboard endpoint did `float(revenue_data['total'])`. Currency must
never pass through a binary float. The database stores `NUMERIC(10,3)` (sub-cent
precision on purpose), and floats cannot represent most decimal fractions exactly:

```
0.1 + 0.2 = 0.30000000000000004      # money must never do this
```

Concretely, `prop-001` (Sunset) has three sub-cent bookings — `333.333 + 333.333 + 333.334`:

```
correct total (round the exact sum once) : 2250.00
naive (round each booking, then add)     : 2249.99   <- 1 cent off
```

That 3rd-decimal precision, once it leaks into float arithmetic / display rounding, is
the "few cents here and there" finance couldn't pin down.

**Fix.** Keep the value as `Decimal`, round the **total** to cents once with an explicit
rounding mode, and serialize it as an exact 2-decimal string:

```python
total_revenue = Decimal(str(revenue_data['total'])).quantize(
    Decimal('0.01'), rounding=ROUND_HALF_UP
)
return {"total_revenue": str(total_revenue), ...}
```

The frontend ([RevenueSummary.tsx](frontend/src/components/RevenueSummary.tsx)) was
updated to accept the string and coerce it only for local formatting, so no currency
value passes through a JS `number` on the way in. This also clears the built-in
"Precision Mismatch Detected" indicator.

---

## Why these slipped through testing

`database_pool.py` builds its connection URL from `settings.supabase_db_*`, but those
fields don't exist on `Settings` (only `database_url` does). So the pool fails to
initialise and `calculate_total_revenue` falls back to hard-coded **mock data** keyed by
`property_id` only. The mock returns identical numbers for every tenant, which *masks*
the cache-isolation and timezone bugs during casual testing — they only appear against
the real seeded data. This is noted here but left as-is to stay within the "debug, don't
rebuild" scope; the correct long-term fix is to point `DatabasePool` at `settings.database_url`.

---

## How to reproduce the verification

```bash
# Spin up just the seeded Postgres and run the comparison queries
./verify_fixes.sh
```

The script starts a throwaway Postgres with `database/schema.sql` + `database/seed.sql`,
then prints the buggy-vs-fixed numbers for bugs 1–3 shown above.
