#!/usr/bin/env bash
# Reproduces the buggy-vs-fixed numbers for all three revenue-dashboard bugs
# against the real seeded database. Requires Docker.
set -euo pipefail

NAME=pf-verify-db
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

echo "Starting seeded Postgres..."
docker run -d --name "$NAME" \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=propertyflow \
  -v "$ROOT/database/schema.sql:/docker-entrypoint-initdb.d/1-schema.sql" \
  -v "$ROOT/database/seed.sql:/docker-entrypoint-initdb.d/2-seed.sql" \
  postgres:15-alpine >/dev/null

for _ in $(seq 1 30); do
  docker exec "$NAME" pg_isready -U postgres -d propertyflow >/dev/null 2>&1 && break
  sleep 1
done
sleep 2

docker exec -i "$NAME" psql -U postgres -d propertyflow -P pager=off <<'SQL'
\echo '\n========== BUG 1: property IDs collide across tenants (why the shared cache key leaks) =========='
SELECT p.id AS property_id, p.tenant_id, t.name AS company, p.timezone,
       COALESCE(SUM(r.total_amount),0) AS total_revenue, COUNT(r.id) AS reservations
FROM properties p JOIN tenants t ON t.id=p.tenant_id
LEFT JOIN reservations r ON r.property_id=p.id AND r.tenant_id=p.tenant_id
WHERE p.id='prop-001' GROUP BY p.id,p.tenant_id,t.name,p.timezone ORDER BY p.tenant_id;

\echo '\n========== BUG 2: timezone — March for tenant-a prop-001 (Europe/Paris) =========='
\echo '-- the edge-of-month reservation:'
SELECT r.id, r.total_amount, r.check_in_date AS utc_instant,
       (r.check_in_date AT TIME ZONE p.timezone) AS local_check_in, p.timezone
FROM reservations r JOIN properties p ON p.id=r.property_id AND p.tenant_id=r.tenant_id
WHERE r.id='res-tz-1';
\echo '-- BUGGY (naive UTC boundaries):'
SELECT COUNT(*) AS bookings, COALESCE(SUM(total_amount),0) AS march_revenue
FROM reservations WHERE property_id='prop-001' AND tenant_id='tenant-a'
  AND check_in_date >= '2024-03-01 00:00:00+00' AND check_in_date < '2024-04-01 00:00:00+00';
\echo '-- FIXED (property-timezone boundaries):'
SELECT COUNT(*) AS bookings, COALESCE(SUM(total_amount),0) AS march_revenue
FROM reservations r JOIN properties p ON p.id=r.property_id AND p.tenant_id=r.tenant_id
WHERE r.property_id='prop-001' AND r.tenant_id='tenant-a'
  AND r.check_in_date >= (TIMESTAMP '2024-03-01 00:00:00' AT TIME ZONE p.timezone)
  AND r.check_in_date <  (TIMESTAMP '2024-04-01 00:00:00' AT TIME ZONE p.timezone);
SQL

echo ""
echo "========== BUG 3: money as float vs Decimal =========="
python3 - <<'PY'
from decimal import Decimal, ROUND_HALF_UP
vals = ["333.333","333.333","333.334"]   # prop-001 / tenant-a sub-cent bookings
print(f"  round-each-then-add (buggy): {sum(Decimal(v).quantize(Decimal('0.01'), ROUND_HALF_UP) for v in vals)}")
print(f"  add-then-round-once (fixed): {sum(Decimal(v) for v in vals).quantize(Decimal('0.01'), ROUND_HALF_UP)}")
print(f"  classic float failure      : 0.1 + 0.2 = {0.1 + 0.2!r}")
PY
echo ""
echo "Done."
