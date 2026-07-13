from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")


async def _get_property_timezone(session, property_id: str, tenant_id: str) -> str:
    """Return the IANA timezone for a property, defaulting to UTC."""
    from sqlalchemy import text

    result = await session.execute(
        text("""
            SELECT timezone FROM properties
            WHERE id = :property_id AND tenant_id = :tenant_id
        """),
        {"property_id": property_id, "tenant_id": tenant_id},
    )
    row = result.fetchone()
    return row.timezone if row and row.timezone else "UTC"


async def calculate_monthly_revenue(
    property_id: str, tenant_id: str, month: int, year: int, db_session=None
) -> Decimal:
    """
    Calculates revenue for a specific month, in the property's LOCAL timezone.

    TIMEZONE CORRECTNESS: check_in_date is stored as a timestamptz (UTC). A month
    is a *local* concept: a stay checking in on 2024-03-01 00:30 in Paris is a
    March booking even though its UTC instant is 2024-02-29 23:30. Building the
    month boundaries as naive UTC midnights (the previous behaviour) pushed those
    edge-of-month check-ins into the wrong month, so a client's monthly totals
    disagreed with their own records. We anchor the boundaries to the property's
    timezone and convert to UTC for the timestamptz comparison.
    """
    from app.core.database_pool import DatabasePool
    from sqlalchemy import text

    db_pool = DatabasePool()
    await db_pool.initialize()

    if not db_pool.session_factory:
        raise Exception("Database pool not available")

    async with db_pool.get_session() as session:
        tz = ZoneInfo(await _get_property_timezone(session, property_id, tenant_id))

        # Local-midnight boundaries in the property's timezone...
        start_local = datetime(year, month, 1, tzinfo=tz)
        if month < 12:
            end_local = datetime(year, month + 1, 1, tzinfo=tz)
        else:
            end_local = datetime(year + 1, 1, 1, tzinfo=tz)

        # ...converted to UTC so they line up with the stored timestamptz values.
        start_utc = start_local.astimezone(UTC)
        end_utc = end_local.astimezone(UTC)

        print(f"DEBUG: Querying revenue for {property_id} ({tz}) "
              f"[{start_local} .. {end_local}) -> [{start_utc} .. {end_utc}) UTC")

        result = await session.execute(
            text("""
                SELECT SUM(total_amount) AS total
                FROM reservations
                WHERE property_id = :property_id
                AND tenant_id = :tenant_id
                AND check_in_date >= :start_utc
                AND check_in_date < :end_utc
            """),
            {
                "property_id": property_id,
                "tenant_id": tenant_id,
                "start_utc": start_utc,
                "end_utc": end_utc,
            },
        )
        row = result.fetchone()
        return Decimal(str(row.total)) if row and row.total is not None else Decimal("0")

async def calculate_total_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Aggregates revenue from database.
    """
    try:
        # Import database pool
        from app.core.database_pool import DatabasePool
        
        # Initialize pool if needed
        db_pool = DatabasePool()
        await db_pool.initialize()
        
        if db_pool.session_factory:
            async with db_pool.get_session() as session:
                # Use SQLAlchemy text for raw SQL
                from sqlalchemy import text
                
                query = text("""
                    SELECT 
                        property_id,
                        SUM(total_amount) as total_revenue,
                        COUNT(*) as reservation_count
                    FROM reservations 
                    WHERE property_id = :property_id AND tenant_id = :tenant_id
                    GROUP BY property_id
                """)
                
                result = await session.execute(query, {
                    "property_id": property_id, 
                    "tenant_id": tenant_id
                })
                row = result.fetchone()
                
                if row:
                    total_revenue = Decimal(str(row.total_revenue))
                    return {
                        "property_id": property_id,
                        "tenant_id": tenant_id,
                        "total": str(total_revenue),
                        "currency": "USD", 
                        "count": row.reservation_count
                    }
                else:
                    # No reservations found for this property
                    return {
                        "property_id": property_id,
                        "tenant_id": tenant_id,
                        "total": "0.00",
                        "currency": "USD",
                        "count": 0
                    }
        else:
            raise Exception("Database pool not available")
            
    except Exception as e:
        print(f"Database error for {property_id} (tenant: {tenant_id}): {e}")
        
        # Create property-specific mock data for testing when DB is unavailable
        # This ensures each property shows different figures
        mock_data = {
            'prop-001': {'total': '1000.00', 'count': 3},
            'prop-002': {'total': '4975.50', 'count': 4}, 
            'prop-003': {'total': '6100.50', 'count': 2},
            'prop-004': {'total': '1776.50', 'count': 4},
            'prop-005': {'total': '3256.00', 'count': 3}
        }
        
        mock_property_data = mock_data.get(property_id, {'total': '0.00', 'count': 0})
        
        return {
            "property_id": property_id,
            "tenant_id": tenant_id, 
            "total": mock_property_data['total'],
            "currency": "USD",
            "count": mock_property_data['count']
        }
