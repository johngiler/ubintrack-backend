"""Refresco de agregados del dashboard (PostgreSQL materialized views / tablas SQLite)."""

from django.db import connection


def _is_sqlite() -> bool:
    return connection.vendor == "sqlite"


def refresh_dashboard_aggregates() -> None:
    if _is_sqlite():
        _refresh_sqlite()
    else:
        _refresh_postgres()


def _refresh_postgres() -> None:
    statements = [
        "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_dashboard_monthly",
        "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_dashboard_daily",
        "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_cava_stats",
        "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_cava_hall_stats",
        "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_entries_counters",
        "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_exits_counters",
        "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_bins_counters",
        "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_client_stats",
    ]
    fallbacks = {
        "mv_dashboard_monthly": "SELECT refresh_mv_dashboard_monthly()",
        "mv_dashboard_daily": "SELECT refresh_mv_dashboard_daily()",
    }
    with connection.cursor() as cursor:
        for sql in statements:
            try:
                cursor.execute(sql)
            except Exception:
                mv_name = sql.split()[-1]
                if mv_name in fallbacks:
                    try:
                        cursor.execute(fallbacks[mv_name])
                        continue
                    except Exception:
                        pass
                cursor.execute(f"REFRESH MATERIALIZED VIEW {mv_name}")


def _refresh_sqlite() -> None:
    queries = [
        "DELETE FROM mv_entries_counters",
        """
        INSERT INTO mv_entries_counters (id, total, pending, in_progress, completed)
        SELECT 1,
               COUNT(e.id),
               SUM(CASE WHEN e.status = 'pendiente' THEN 1 ELSE 0 END),
               SUM(CASE WHEN e.status = 'en_progreso' THEN 1 ELSE 0 END),
               SUM(CASE WHEN e.status = 'completada' THEN 1 ELSE 0 END)
        FROM order_entry e
        """,
        "DELETE FROM mv_exits_counters",
        """
        INSERT INTO mv_exits_counters (id, total, pending, in_progress, completed)
        SELECT 1,
               COUNT(x.id),
               SUM(CASE WHEN x.status = 'pendiente' THEN 1 ELSE 0 END),
               SUM(CASE WHEN x.status = 'en_progreso' THEN 1 ELSE 0 END),
               SUM(CASE WHEN x.status = 'completada' THEN 1 ELSE 0 END)
        FROM order_exit x
        """,
        "DELETE FROM mv_bins_counters",
        """
        INSERT INTO mv_bins_counters (id, total, received, stored, dispatched)
        SELECT 1,
               COUNT(b.id),
               SUM(CASE WHEN b.status = 'en_recibo' THEN 1 ELSE 0 END),
               SUM(CASE WHEN b.status = 'almacenado' THEN 1 ELSE 0 END),
               SUM(CASE WHEN b.status = 'despachado' THEN 1 ELSE 0 END)
        FROM inventory_bin b
        """,
        "DELETE FROM mv_dashboard_monthly",
        """
        INSERT INTO mv_dashboard_monthly (month, entries, exits, bins)
        SELECT month,
               SUM(entries),
               SUM(exits),
               SUM(bins)
        FROM (
            SELECT strftime('%Y-%m-01', created_at) AS month,
                   COUNT(*) AS entries, 0 AS exits, 0 AS bins
            FROM order_entry GROUP BY 1
            UNION ALL
            SELECT strftime('%Y-%m-01', created_at) AS month,
                   0, COUNT(*), 0
            FROM order_exit GROUP BY 1
            UNION ALL
            SELECT strftime('%Y-%m-01', created_at) AS month,
                   0, 0, COUNT(*)
            FROM inventory_bin GROUP BY 1
        ) t
        GROUP BY month
        """,
        "DELETE FROM mv_dashboard_daily",
        """
        INSERT INTO mv_dashboard_daily (day, entries, exits, bins)
        SELECT day,
               SUM(entries),
               SUM(exits),
               SUM(bins)
        FROM (
            SELECT date(created_at) AS day, COUNT(*) AS entries, 0 AS exits, 0 AS bins
            FROM order_entry GROUP BY 1
            UNION ALL
            SELECT date(created_at), 0, COUNT(*), 0 FROM order_exit GROUP BY 1
            UNION ALL
            SELECT date(created_at), 0, 0, COUNT(*) FROM inventory_bin GROUP BY 1
        ) t
        GROUP BY day
        """,
        "DELETE FROM mv_cava_stats",
        """
        INSERT INTO mv_cava_stats (cava_id, total, libre, ocupada, asignada)
        SELECT s.id,
               SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 AND p.status = 'libre' THEN 1 ELSE 0 END),
               SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 AND p.status = 'ocupado' THEN 1 ELSE 0 END),
               SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 AND p.status = 'asignado' THEN 1 ELSE 0 END)
        FROM storage_cava s
        LEFT JOIN storage_position p ON p.cava_id = s.id
        WHERE s.is_virtual = 0
        GROUP BY s.id
        """,
        "DELETE FROM mv_cava_hall_stats",
        """
        INSERT INTO mv_cava_hall_stats (id, cava_id, hall, total, libre, ocupada, asignada)
        SELECT ROW_NUMBER() OVER (ORDER BY s.id, p.hall),
               s.id,
               p.hall,
               SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 AND p.status = 'libre' THEN 1 ELSE 0 END),
               SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 AND p.status = 'ocupado' THEN 1 ELSE 0 END),
               SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 AND p.status = 'asignado' THEN 1 ELSE 0 END)
        FROM storage_cava s
        LEFT JOIN storage_position p ON p.cava_id = s.id
        WHERE s.is_virtual = 0 AND p.hall IS NOT NULL
        GROUP BY s.id, p.hall
        """,
        "DELETE FROM mv_client_stats",
        """
        INSERT INTO mv_client_stats (
            client_id, client_name, client_slug,
            positions_used, bins_in_warehouse,
            entries_total, entries_pending, entries_in_progress, entries_completed,
            exits_total, exits_pending, exits_in_progress, exits_completed
        )
        SELECT c.id,
               c.name,
               c.slug,
               COALESCE((
                   SELECT COUNT(DISTINCT b.position_id)
                   FROM inventory_bin b
                   INNER JOIN storage_position p ON p.id = b.position_id
                   INNER JOIN storage_cava s ON s.id = p.cava_id
                   WHERE b.client_id = c.id
                     AND b.is_active = 1
                     AND b.position_id IS NOT NULL
                     AND b.status IN ('almacenado', 'en_despacho')
                     AND s.is_virtual = 0
               ), 0),
               COALESCE((
                   SELECT COUNT(*)
                   FROM inventory_bin b2
                   WHERE b2.client_id = c.id
                     AND b2.is_active = 1
                     AND b2.status IN ('almacenado', 'en_despacho')
               ), 0),
               COALESCE((SELECT COUNT(*) FROM order_entry e WHERE e.client_id = c.id), 0),
               COALESCE((SELECT COUNT(*) FROM order_entry e WHERE e.client_id = c.id AND e.status = 'pendiente'), 0),
               COALESCE((SELECT COUNT(*) FROM order_entry e WHERE e.client_id = c.id AND e.status = 'en_progreso'), 0),
               COALESCE((SELECT COUNT(*) FROM order_entry e WHERE e.client_id = c.id AND e.status = 'completada'), 0),
               COALESCE((SELECT COUNT(*) FROM order_exit x WHERE x.client_id = c.id), 0),
               COALESCE((SELECT COUNT(*) FROM order_exit x WHERE x.client_id = c.id AND x.status = 'pendiente'), 0),
               COALESCE((SELECT COUNT(*) FROM order_exit x WHERE x.client_id = c.id AND x.status = 'en_progreso'), 0),
               COALESCE((SELECT COUNT(*) FROM order_exit x WHERE x.client_id = c.id AND x.status = 'completada'), 0)
        FROM core_client c
        ORDER BY c.name
        """,
    ]
    with connection.cursor() as cursor:
        for sql in queries:
            cursor.execute(sql)
