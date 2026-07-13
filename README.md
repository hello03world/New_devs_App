# PropertyFlow — Revenue Dashboard

A multi-tenant property‑management revenue dashboard. Property managers log in as a
client (tenant) and view per‑property revenue summaries built from reservation data.

This repository is a **debugging exercise**: three production bugs were reported by
clients and fixed. See **[FINDINGS.md](FINDINGS.md)** for the full root‑cause analysis
and **[verify_fixes.sh](verify_fixes.sh)** to reproduce the buggy‑vs‑fixed numbers.

---

## Tech stack

| Layer     | Technology |
|-----------|------------|
| Frontend  | React 18 + TypeScript, Vite, Tailwind CSS (nginx in Docker) |
| Backend   | FastAPI (Python 3.11), Uvicorn, SQLAlchemy |
| Database  | PostgreSQL 15 (seeded on first start) |
| Cache     | Redis |
| Auth      | JWT (test accounts are built in for the challenge) |

---

## Quick start (Docker — recommended)

Requires Docker + Docker Compose.

```bash
docker-compose up --build
```

Then open:

| Service            | URL                              |
|--------------------|----------------------------------|
| Frontend (app)     | http://localhost:3000            |
| Backend API docs   | http://localhost:8000/docs       |
| Backend health     | http://localhost:8000/health     |

Compose also starts Postgres (host port **5433**) and Redis (host port **6380**).
The database is initialised automatically from [`database/schema.sql`](database/schema.sql)
and [`database/seed.sql`](database/seed.sql).

To stop and reset:

```bash
docker-compose down          # stop
docker-compose down -v        # stop + wipe the database volume
```

---

## Login credentials

Two client (tenant) accounts are provided:

| Client                        | Email                     | Password        |
|-------------------------------|---------------------------|-----------------|
| Sunset Properties (tenant‑a)  | `sunset@propertyflow.com` | `client_a_2024` |
| Ocean Rentals (tenant‑b)      | `ocean@propertyflow.com`  | `client_b_2024` |

---

## Local development (without Docker)

You still need Postgres and Redis running (the Docker route above is easiest for those).

**Backend** — uses [`uv`](https://github.com/astral-sh/uv):

```bash
make uv-install          # cd backend && uv sync
make back                # uvicorn app.main:app --reload --port 8000
```

Or with plain pip:

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Frontend** — Vite dev server (http://localhost:5173):

```bash
cd frontend
npm install
npm run dev              # or: make front
```

### Environment variables

Compose sets these for you; for local runs configure them yourself:

| Variable        | Used by  | Default / example                                       |
|-----------------|----------|---------------------------------------------------------|
| `DATABASE_URL`  | backend  | `postgresql://postgres:postgres@db:5432/propertyflow`   |
| `REDIS_URL`     | backend  | `redis://redis:6379/0`                                  |
| `SECRET_KEY`    | backend  | `debug_challenge_secret`                                |
| `VITE_API_URL`  | frontend | `http://localhost:8000`                                 |

Frontend variables live in `frontend/.env` (git‑ignored). A minimal example:

```env
VITE_API_URL=http://localhost:8000
VITE_BACKEND_URL=http://localhost:8000
```

---

## Project structure

```
.
├── backend/                 FastAPI service
│   └── app/
│       ├── api/v1/dashboard.py       revenue summary endpoint
│       ├── services/cache.py         Redis revenue cache
│       ├── services/reservations.py  revenue calculations
│       └── core/                     auth, db pool, tenant context
├── frontend/                React + Vite app
│   └── src/components/RevenueSummary.tsx
├── database/
│   ├── schema.sql           tables (tenants, properties, reservations)
│   └── seed.sql             sample tenants / properties / reservations
├── docker-compose.yml
├── FINDINGS.md              bug analysis + fixes
└── verify_fixes.sh          reproducible buggy-vs-fixed verification
```

---

## The debugging task

Three issues were reported and fixed. Summary (full detail in [FINDINGS.md](FINDINGS.md)):

| # | Symptom | Root cause | Fix |
|---|---------|-----------|-----|
| 1 | One client sometimes saw **another company's** revenue | Redis cache key `revenue:{property_id}` — but property IDs are only unique per tenant, so IDs collided across tenants | Scope the cache key by tenant: `revenue:{tenant_id}:{property_id}` |
| 2 | **March total** didn't match a client's own records | Month boundaries computed in naive UTC, ignoring the property's timezone, so an edge‑of‑month check‑in landed in the wrong month | Anchor month boundaries to the property's timezone via `zoneinfo` (DST‑safe) |
| 3 | Totals **off by a few cents** | Money routed through binary `float` | Keep money in `Decimal`, round to cents once, serialize as a string |

### Verify the fixes

Runs a throwaway seeded Postgres and prints the buggy‑vs‑fixed numbers (requires Docker):

```bash
./verify_fixes.sh
```

Expected highlights: March revenue for Sunset's `prop-001` corrects from `1000.00`
(naive UTC) to `2250.00` (timezone‑aware), and the two tenants' `prop-001` are shown to
be genuinely different properties — which is why the un‑scoped cache key leaked.
