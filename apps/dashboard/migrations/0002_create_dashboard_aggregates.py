from django.db import connection, migrations


SQLITE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS mv_entries_counters (
        id INTEGER PRIMARY KEY,
        total INTEGER NOT NULL DEFAULT 0,
        pending INTEGER NOT NULL DEFAULT 0,
        in_progress INTEGER NOT NULL DEFAULT 0,
        completed INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mv_exits_counters (
        id INTEGER PRIMARY KEY,
        total INTEGER NOT NULL DEFAULT 0,
        pending INTEGER NOT NULL DEFAULT 0,
        in_progress INTEGER NOT NULL DEFAULT 0,
        completed INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mv_bins_counters (
        id INTEGER PRIMARY KEY,
        total INTEGER NOT NULL DEFAULT 0,
        received INTEGER NOT NULL DEFAULT 0,
        stored INTEGER NOT NULL DEFAULT 0,
        dispatched INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mv_dashboard_monthly (
        month DATE PRIMARY KEY,
        entries INTEGER NOT NULL DEFAULT 0,
        exits INTEGER NOT NULL DEFAULT 0,
        bins INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mv_dashboard_daily (
        day DATE PRIMARY KEY,
        entries INTEGER NOT NULL DEFAULT 0,
        exits INTEGER NOT NULL DEFAULT 0,
        bins INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mv_cava_stats (
        cava_id INTEGER PRIMARY KEY,
        total INTEGER NOT NULL DEFAULT 0,
        libre INTEGER NOT NULL DEFAULT 0,
        ocupada INTEGER NOT NULL DEFAULT 0,
        asignada INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mv_cava_hall_stats (
        id INTEGER PRIMARY KEY,
        cava_id INTEGER NOT NULL,
        hall VARCHAR(1) NOT NULL,
        total INTEGER NOT NULL DEFAULT 0,
        libre INTEGER NOT NULL DEFAULT 0,
        ocupada INTEGER NOT NULL DEFAULT 0,
        asignada INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mv_client_stats (
        client_id INTEGER PRIMARY KEY,
        client_name VARCHAR(255) NOT NULL,
        client_slug VARCHAR(255) NOT NULL,
        positions_used INTEGER NOT NULL DEFAULT 0,
        bins_in_warehouse INTEGER NOT NULL DEFAULT 0,
        entries_total INTEGER NOT NULL DEFAULT 0,
        entries_pending INTEGER NOT NULL DEFAULT 0,
        entries_in_progress INTEGER NOT NULL DEFAULT 0,
        entries_completed INTEGER NOT NULL DEFAULT 0,
        exits_total INTEGER NOT NULL DEFAULT 0,
        exits_pending INTEGER NOT NULL DEFAULT 0,
        exits_in_progress INTEGER NOT NULL DEFAULT 0,
        exits_completed INTEGER NOT NULL DEFAULT 0
    )
    """,
]

SQLITE_DROPS = [
    "DROP TABLE IF EXISTS mv_client_stats",
    "DROP TABLE IF EXISTS mv_cava_hall_stats",
    "DROP TABLE IF EXISTS mv_cava_stats",
    "DROP TABLE IF EXISTS mv_dashboard_daily",
    "DROP TABLE IF EXISTS mv_dashboard_monthly",
    "DROP TABLE IF EXISTS mv_bins_counters",
    "DROP TABLE IF EXISTS mv_exits_counters",
    "DROP TABLE IF EXISTS mv_entries_counters",
]

POSTGRES_OBJECTS = [
    "DROP MATERIALIZED VIEW IF EXISTS mv_client_stats CASCADE",
    "DROP MATERIALIZED VIEW IF EXISTS mv_cava_hall_stats CASCADE",
    "DROP MATERIALIZED VIEW IF EXISTS mv_cava_stats CASCADE",
    "DROP MATERIALIZED VIEW IF EXISTS mv_dashboard_daily CASCADE",
    "DROP MATERIALIZED VIEW IF EXISTS mv_dashboard_monthly CASCADE",
    "DROP MATERIALIZED VIEW IF EXISTS mv_bins_counters CASCADE",
    "DROP MATERIALIZED VIEW IF EXISTS mv_exits_counters CASCADE",
    "DROP MATERIALIZED VIEW IF EXISTS mv_entries_counters CASCADE",
    r"""
    CREATE MATERIALIZED VIEW mv_dashboard_monthly AS
    SELECT month,
           SUM(entries) AS entries,
           SUM(exits) AS exits,
           SUM(bins) AS bins
    FROM (
        SELECT date_trunc('month', created_at)::date AS month, COUNT(*) AS entries, 0 AS exits, 0 AS bins
        FROM order_entry
        GROUP BY 1
        UNION ALL
        SELECT date_trunc('month', created_at)::date AS month, 0 AS entries, COUNT(*) AS exits, 0 AS bins
        FROM order_exit
        GROUP BY 1
        UNION ALL
        SELECT date_trunc('month', created_at)::date AS month, 0 AS entries, 0 AS exits, COUNT(*) AS bins
        FROM inventory_bin
        GROUP BY 1
    ) t
    GROUP BY month
    ORDER BY month
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS mv_dashboard_monthly_idx ON mv_dashboard_monthly (month)",
    r"""
    CREATE MATERIALIZED VIEW mv_dashboard_daily AS
    SELECT day,
           SUM(entries) AS entries,
           SUM(exits) AS exits,
           SUM(bins) AS bins
    FROM (
        SELECT created_at::date AS day, COUNT(*) AS entries, 0 AS exits, 0 AS bins
        FROM order_entry
        GROUP BY 1
        UNION ALL
        SELECT created_at::date AS day, 0 AS entries, COUNT(*) AS exits, 0 AS bins
        FROM order_exit
        GROUP BY 1
        UNION ALL
        SELECT created_at::date AS day, 0 AS entries, 0 AS exits, COUNT(*) AS bins
        FROM inventory_bin
        GROUP BY 1
    ) t
    GROUP BY day
    ORDER BY day
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS mv_dashboard_daily_idx ON mv_dashboard_daily (day)",
    r"""
    CREATE MATERIALIZED VIEW mv_entries_counters AS
    SELECT 1::int AS id,
           COUNT(e.id) AS total,
           COUNT(*) FILTER (WHERE e.status = 'pendiente') AS pending,
           COUNT(*) FILTER (WHERE e.status = 'en_progreso') AS in_progress,
           COUNT(*) FILTER (WHERE e.status = 'completada') AS completed
    FROM order_entry e
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS mv_entries_counters_idx ON mv_entries_counters (id)",
    r"""
    CREATE MATERIALIZED VIEW mv_exits_counters AS
    SELECT 1::int AS id,
           COUNT(x.id) AS total,
           COUNT(*) FILTER (WHERE x.status = 'pendiente') AS pending,
           COUNT(*) FILTER (WHERE x.status = 'en_progreso') AS in_progress,
           COUNT(*) FILTER (WHERE x.status = 'completada') AS completed
    FROM order_exit x
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS mv_exits_counters_idx ON mv_exits_counters (id)",
    r"""
    CREATE MATERIALIZED VIEW mv_bins_counters AS
    SELECT 1::int AS id,
           COUNT(b.id) AS total,
           COUNT(*) FILTER (WHERE b.status = 'en_recibo') AS received,
           COUNT(*) FILTER (WHERE b.status = 'almacenado') AS stored,
           COUNT(*) FILTER (WHERE b.status = 'despachado') AS dispatched
    FROM inventory_bin b
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS mv_bins_counters_idx ON mv_bins_counters (id)",
    r"""
    CREATE MATERIALIZED VIEW mv_cava_stats AS
    SELECT s.id AS cava_id,
           COUNT(p.id) FILTER (WHERE p.is_active AND s.is_virtual = false) AS total,
           COUNT(p.id) FILTER (WHERE p.is_active AND s.is_virtual = false AND p.status = 'libre') AS libre,
           COUNT(p.id) FILTER (WHERE p.is_active AND s.is_virtual = false AND p.status = 'ocupado') AS ocupada,
           COUNT(p.id) FILTER (WHERE p.is_active AND s.is_virtual = false AND p.status = 'asignado') AS asignada
    FROM storage_cava s
    LEFT JOIN storage_position p ON p.cava_id = s.id
    WHERE s.is_virtual = false
    GROUP BY s.id
    ORDER BY s.id
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS mv_cava_stats_idx ON mv_cava_stats (cava_id)",
    r"""
    CREATE MATERIALIZED VIEW mv_cava_hall_stats AS
    SELECT ROW_NUMBER() OVER ()::bigint AS id,
           s.id AS cava_id,
           p.hall AS hall,
           COUNT(p.id) FILTER (WHERE p.is_active AND s.is_virtual = false) AS total,
           COUNT(p.id) FILTER (WHERE p.is_active AND s.is_virtual = false AND p.status = 'libre') AS libre,
           COUNT(p.id) FILTER (WHERE p.is_active AND s.is_virtual = false AND p.status = 'ocupado') AS ocupada,
           COUNT(p.id) FILTER (WHERE p.is_active AND s.is_virtual = false AND p.status = 'asignado') AS asignada
    FROM storage_cava s
    LEFT JOIN storage_position p ON p.cava_id = s.id
    WHERE s.is_virtual = false
    GROUP BY s.id, p.hall
    ORDER BY s.id, p.hall
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS mv_cava_hall_stats_idx ON mv_cava_hall_stats (id)",
    r"""
    CREATE MATERIALIZED VIEW mv_client_stats AS
    SELECT c.id AS client_id,
           c.name AS client_name,
           c.slug AS client_slug,
           COALESCE((
             SELECT COUNT(DISTINCT b.position_id)
             FROM inventory_bin b
             INNER JOIN storage_position p ON p.id = b.position_id
             INNER JOIN storage_cava s ON s.id = p.cava_id
             WHERE b.client_id = c.id
               AND b.is_active = TRUE
               AND b.position_id IS NOT NULL
               AND b.status IN ('almacenado', 'en_despacho')
               AND s.is_virtual = false
           ), 0) AS positions_used,
           COALESCE((
             SELECT COUNT(*)
             FROM inventory_bin b2
             WHERE b2.client_id = c.id
               AND b2.is_active = TRUE
               AND b2.status IN ('almacenado', 'en_despacho')
           ), 0) AS bins_in_warehouse,
           COALESCE((SELECT COUNT(*) FROM order_entry e WHERE e.client_id = c.id), 0) AS entries_total,
           COALESCE((SELECT COUNT(*) FROM order_entry e WHERE e.client_id = c.id AND e.status = 'pendiente'), 0) AS entries_pending,
           COALESCE((SELECT COUNT(*) FROM order_entry e WHERE e.client_id = c.id AND e.status = 'en_progreso'), 0) AS entries_in_progress,
           COALESCE((SELECT COUNT(*) FROM order_entry e WHERE e.client_id = c.id AND e.status = 'completada'), 0) AS entries_completed,
           COALESCE((SELECT COUNT(*) FROM order_exit x WHERE x.client_id = c.id), 0) AS exits_total,
           COALESCE((SELECT COUNT(*) FROM order_exit x WHERE x.client_id = c.id AND x.status = 'pendiente'), 0) AS exits_pending,
           COALESCE((SELECT COUNT(*) FROM order_exit x WHERE x.client_id = c.id AND x.status = 'en_progreso'), 0) AS exits_in_progress,
           COALESCE((SELECT COUNT(*) FROM order_exit x WHERE x.client_id = c.id AND x.status = 'completada'), 0) AS exits_completed
    FROM core_client c
    ORDER BY c.name
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS mv_client_stats_idx ON mv_client_stats (client_id)",
]


def create_dashboard_aggregates(apps, schema_editor):
    from apps.dashboard.refresh import refresh_dashboard_aggregates

    with connection.cursor() as cursor:
        if connection.vendor == "sqlite":
            for sql in SQLITE_TABLES:
                cursor.execute(sql)
        else:
            for sql in POSTGRES_OBJECTS:
                cursor.execute(sql)

    if connection.vendor == "sqlite":
        refresh_dashboard_aggregates()


def drop_dashboard_aggregates(apps, schema_editor):
    with connection.cursor() as cursor:
        drops = SQLITE_DROPS if connection.vendor == "sqlite" else POSTGRES_OBJECTS[:8]
        for sql in drops:
            cursor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0001_initial"),
        ("core", "0006_alter_customuser_client_alter_profile_user_type"),
        ("order", "0002_rename_bin_fks"),
        ("inventory", "0005_seed_categories"),
        ("storage", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_dashboard_aggregates, drop_dashboard_aggregates),
    ]
