"""Agregados del dashboard: consultas en vivo por owner y refresh de MVs."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.db import connection
from django.db.models import Count, Q
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone

from apps.core import choices
from apps.core.models import Client
from apps.inventory.models import Bin
from apps.order.models import Entry, Exit
from apps.storage.models import Cava, Position, Storage


DAYS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']


def _entries_qs(workspace):
    return Entry.objects.filter(client__workspace=workspace)


def _exits_qs(workspace):
    return Exit.objects.filter(client__workspace=workspace)


def _bins_qs(workspace):
    return Bin.objects.filter(client__workspace=workspace, is_active=True)


def _positions_qs(workspace):
    return Position.objects.filter(
        cava__storage__workspace=workspace,
        cava__is_virtual=False,
        is_active=True,
    )


def build_dashboard_stats(workspace) -> dict:
    """KPIs del dashboard para un owner (consulta en vivo)."""
    entries = _entries_qs(workspace).aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(status=choices.OrderStatus.PENDIENTE)),
        in_progress=Count('id', filter=Q(status=choices.OrderStatus.EN_PROGRESO)),
        completed=Count('id', filter=Q(status=choices.OrderStatus.COMPLETADA)),
    )
    exits = _exits_qs(workspace).aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(status=choices.OrderStatus.PENDIENTE)),
        in_progress=Count('id', filter=Q(status=choices.OrderStatus.EN_PROGRESO)),
        completed=Count('id', filter=Q(status=choices.OrderStatus.COMPLETADA)),
    )
    bins = _bins_qs(workspace).aggregate(
        total=Count('id'),
        received=Count('id', filter=Q(status=choices.BinStatus.EN_RECIBO)),
        stored=Count('id', filter=Q(status=choices.BinStatus.ALMACENADO)),
        dispatched=Count('id', filter=Q(status=choices.BinStatus.DESPACHADO)),
    )
    positions = _positions_qs(workspace).aggregate(
        total=Count('id'),
        libre=Count('id', filter=Q(status=choices.PositionStatus.LIBRE)),
        ocupada=Count('id', filter=Q(status=choices.PositionStatus.OCUPADO)),
        asignada=Count('id', filter=Q(status=choices.PositionStatus.ASIGNADO)),
    )

    positions_total = positions['total'] or 0
    positions_free = positions['libre'] or 0
    positions_occupied = positions['ocupada'] or 0
    positions_assigned = positions['asignada'] or 0

    total_percentage = occupied_percentage = assigned_percentage = free_percentage = 0.0
    if positions_total > 0:
        total_percentage = (
            (positions_occupied + positions_assigned) / positions_total) * 100
        occupied_percentage = (positions_occupied / positions_total) * 100
        assigned_percentage = (positions_assigned / positions_total) * 100
        free_percentage = (positions_free / positions_total) * 100

    return {
        'entries': {
            'total_count': entries['total'] or 0,
            'pending_count': entries['pending'] or 0,
            'in_progress_count': entries['in_progress'] or 0,
            'completed_count': entries['completed'] or 0,
        },
        'bins': {
            'total_count': bins['total'] or 0,
            'received_count': bins['received'] or 0,
            'stored_count': bins['stored'] or 0,
            'dispatched_count': bins['dispatched'] or 0,
        },
        'exits': {
            'total_count': exits['total'] or 0,
            'pending_count': exits['pending'] or 0,
            'in_progress_count': exits['in_progress'] or 0,
            'completed_count': exits['completed'] or 0,
        },
        'positions': {
            'total_count': positions_total,
            'occupied_count': positions_occupied,
            'free_count': positions_free,
            'assigned_count': positions_assigned,
            'total_percentage': round(total_percentage, 2),
            'occupied_percentage': round(occupied_percentage, 2),
            'assigned_percentage': round(assigned_percentage, 2),
            'free_percentage': round(free_percentage, 2),
        },
    }


def get_monthly_series(workspace, limit: int = 12) -> list:
    months: dict[str, dict] = defaultdict(
        lambda: {'entries': 0, 'exits': 0, 'bins': 0})

    for row in (
        _entries_qs(workspace)
        .annotate(period=TruncMonth('created_at'))
        .values('period')
        .annotate(count=Count('id'))
        .order_by('period')
    ):
        if row['period']:
            key = row['period'].strftime('%Y-%m')
            months[key]['entries'] = row['count']

    for row in (
        _exits_qs(workspace)
        .annotate(period=TruncMonth('created_at'))
        .values('period')
        .annotate(count=Count('id'))
        .order_by('period')
    ):
        if row['period']:
            key = row['period'].strftime('%Y-%m')
            months[key]['exits'] = row['count']

    for row in (
        _bins_qs(workspace)
        .annotate(period=TruncMonth('created_at'))
        .values('period')
        .annotate(count=Count('id'))
        .order_by('period')
    ):
        if row['period']:
            key = row['period'].strftime('%Y-%m')
            months[key]['bins'] = row['count']

    sorted_keys = sorted(months.keys())[-limit:]
    return [
        {'month': key, **months[key]}
        for key in sorted_keys
    ]


def get_daily_last7(workspace) -> list:
    since = timezone.now().date() - timedelta(days=6)
    days: dict[str, dict] = defaultdict(
        lambda: {'entries': 0, 'exits': 0, 'bins': 0})

    for row in (
        _entries_qs(workspace)
        .filter(created_at__date__gte=since)
        .annotate(period=TruncDate('created_at'))
        .values('period')
        .annotate(count=Count('id'))
    ):
        if row['period']:
            days[row['period'].isoformat()]['entries'] = row['count']

    for row in (
        _exits_qs(workspace)
        .filter(created_at__date__gte=since)
        .annotate(period=TruncDate('created_at'))
        .values('period')
        .annotate(count=Count('id'))
    ):
        if row['period']:
            days[row['period'].isoformat()]['exits'] = row['count']

    for row in (
        _bins_qs(workspace)
        .filter(created_at__date__gte=since)
        .annotate(period=TruncDate('created_at'))
        .values('period')
        .annotate(count=Count('id'))
    ):
        if row['period']:
            days[row['period'].isoformat()]['bins'] = row['count']

    result = []
    for offset in range(7):
        day = since + timedelta(days=offset)
        key = day.isoformat()
        row = days.get(key, {'entries': 0, 'exits': 0, 'bins': 0})
        result.append({
            'day': DAYS[day.isoweekday() - 1],
            'entries': row['entries'],
            'exits': row['exits'],
            'bins': row['bins'],
        })
    return result


def build_client_stats(workspace) -> list:
    """Estadísticas por cliente del owner (consulta en vivo)."""
    clients = Client.objects.filter(workspace=workspace, is_active=True)
    data = []

    for client in clients.order_by('name'):
        bins_qs = Bin.objects.filter(
            client=client,
            is_active=True,
        )
        bins_in_warehouse = bins_qs.filter(
            status__in=[choices.BinStatus.ALMACENADO, choices.BinStatus.EN_DESPACHO],
        ).count()
        positions_used = bins_qs.filter(
            position_id__isnull=False,
            status__in=[choices.BinStatus.ALMACENADO, choices.BinStatus.EN_DESPACHO],
            position__cava__is_virtual=False,
        ).values('position_id').distinct().count()

        entries_qs = Entry.objects.filter(client=client)
        exits_qs = Exit.objects.filter(client=client)

        data.append({
            'client_id': client.id,
            'client_name': client.name,
            'client_slug': client.slug,
            'positions_used': positions_used,
            'bins_in_warehouse': bins_in_warehouse,
            'entries_total': entries_qs.count(),
            'entries_pending': entries_qs.filter(
                status=choices.OrderStatus.PENDIENTE).count(),
            'entries_in_progress': entries_qs.filter(
                status=choices.OrderStatus.EN_PROGRESO).count(),
            'entries_completed': entries_qs.filter(
                status=choices.OrderStatus.COMPLETADA).count(),
            'exits_total': exits_qs.count(),
            'exits_pending': exits_qs.filter(
                status=choices.OrderStatus.PENDIENTE).count(),
            'exits_in_progress': exits_qs.filter(
                status=choices.OrderStatus.EN_PROGRESO).count(),
            'exits_completed': exits_qs.filter(
                status=choices.OrderStatus.COMPLETADA).count(),
        })

    data.sort(
        key=lambda row: (-row['bins_in_warehouse'], -row['positions_used'], row['client_name']),
    )
    return data


def _cava_position_stats(workspace, cava_id: int) -> dict:
    qs = _positions_qs(workspace).filter(cava_id=cava_id)
    agg = qs.aggregate(
        total=Count('id'),
        libre=Count('id', filter=Q(status=choices.PositionStatus.LIBRE)),
        ocupada=Count('id', filter=Q(status=choices.PositionStatus.OCUPADO)),
        asignada=Count('id', filter=Q(status=choices.PositionStatus.ASIGNADO)),
        picking=Count('id', filter=Q(is_picking=True)),
    )
    return {
        'total': agg['total'] or 0,
        'libre': agg['libre'] or 0,
        'ocupada': agg['ocupada'] or 0,
        'asignada': agg['asignada'] or 0,
        'picking': agg['picking'] or 0,
    }


def _hall_stats_for_cava(workspace, cava_id: int) -> list:
    halls = []
    qs = _positions_qs(workspace).filter(cava_id=cava_id, hall__isnull=False)
    for row in qs.values('hall').annotate(
        total=Count('id'),
        libre=Count('id', filter=Q(status=choices.PositionStatus.LIBRE)),
        ocupada=Count('id', filter=Q(status=choices.PositionStatus.OCUPADO)),
        asignada=Count('id', filter=Q(status=choices.PositionStatus.ASIGNADO)),
    ).order_by('hall'):
        hall_total = row['total'] or 0
        hall_libre = row['libre'] or 0
        hall_ocupada = row['ocupada'] or 0
        hall_asignada = row['asignada'] or 0
        hall_util = 0.0
        if hall_total > 0:
            hall_util = ((hall_ocupada + hall_asignada) / hall_total) * 100
        halls.append({
            'id': row['hall'],
            'name': f"Pasillo {row['hall']}",
            'positions': {
                'total': hall_total,
                'libre': hall_libre,
                'ocupada': hall_ocupada,
                'asignada': hall_asignada,
                'utilization_rate': round(hall_util, 2),
                'occupied_percentage': round((hall_ocupada / hall_total) * 100, 2) if hall_total else 0.0,
                'assigned_percentage': round((hall_asignada / hall_total) * 100, 2) if hall_total else 0.0,
                'free_percentage': round((hall_libre / hall_total) * 100, 2) if hall_total else 0.0,
            },
        })
    return halls


def build_warehouse_structure(workspace) -> dict | None:
    """Estructura del almacén para el owner (primera bodega activa)."""
    storage = Storage.objects.filter(
        workspace=workspace,
        is_active=True,
    ).order_by('id').first()
    if not storage:
        return None

    cavas = Cava.objects.filter(storage=storage, is_active=True).order_by('name')
    cavas_data = []
    global_stats = {
        'total_positions': 0,
        'libre_positions': 0,
        'ocupada_positions': 0,
        'asignada_positions': 0,
        'picking_positions': 0,
        'utilization_rate': 0.0,
    }

    for cava in cavas:
        stats = _cava_position_stats(workspace, cava.id)
        total_positions = stats['total']
        libre_positions = stats['libre']
        ocupada_positions = stats['ocupada']
        asignada_positions = stats['asignada']
        picking_positions = stats['picking']

        utilization_rate = 0.0
        if total_positions > 0:
            utilization_rate = (
                (ocupada_positions + asignada_positions) / total_positions) * 100

        global_stats['total_positions'] += total_positions
        global_stats['libre_positions'] += libre_positions
        global_stats['ocupada_positions'] += ocupada_positions
        global_stats['asignada_positions'] += asignada_positions
        global_stats['picking_positions'] += picking_positions

        cavas_data.append({
            'id': cava.id,
            'name': cava.name,
            'condition': cava.condition,
            'current_temp': cava.current_temp,
            'total_levels': cava.total_levels,
            'positions_has_been_generated': cava.positions_has_been_generated,
            'is_active': cava.is_active,
            'halls': _hall_stats_for_cava(workspace, cava.id),
            'statistics': {
                'total_positions': total_positions,
                'libre_positions': libre_positions,
                'ocupada_positions': ocupada_positions,
                'asignada_positions': asignada_positions,
                'picking_positions': picking_positions,
                'utilization_rate': round(utilization_rate, 2),
            },
        })

    if global_stats['total_positions'] > 0:
        global_stats['utilization_rate'] = round(
            ((global_stats['ocupada_positions'] + global_stats['asignada_positions'])
             / global_stats['total_positions']) * 100,
            2,
        )

    return {
        'storage': {
            'id': storage.id,
            'name': storage.name,
            'is_active': storage.is_active,
            'created_at': storage.created_at,
            'cavas': cavas_data,
        },
        'global_statistics': global_stats,
    }


def _is_sqlite() -> bool:
    return connection.vendor == 'sqlite'


def refresh_dashboard_aggregates() -> None:
    """Refresca MVs / tablas SQLite (una fila por owner)."""
    if _is_sqlite():
        _refresh_sqlite()
    else:
        _refresh_postgres()


def _refresh_postgres() -> None:
    statements = [
        'REFRESH MATERIALIZED VIEW CONCURRENTLY mv_dashboard_monthly',
        'REFRESH MATERIALIZED VIEW CONCURRENTLY mv_dashboard_daily',
        'REFRESH MATERIALIZED VIEW CONCURRENTLY mv_cava_stats',
        'REFRESH MATERIALIZED VIEW CONCURRENTLY mv_cava_hall_stats',
        'REFRESH MATERIALIZED VIEW CONCURRENTLY mv_entries_counters',
        'REFRESH MATERIALIZED VIEW CONCURRENTLY mv_exits_counters',
        'REFRESH MATERIALIZED VIEW CONCURRENTLY mv_bins_counters',
        'REFRESH MATERIALIZED VIEW CONCURRENTLY mv_client_stats',
    ]
    fallbacks = {
        'mv_dashboard_monthly': 'SELECT refresh_mv_dashboard_monthly()',
        'mv_dashboard_daily': 'SELECT refresh_mv_dashboard_daily()',
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
                cursor.execute(f'REFRESH MATERIALIZED VIEW {mv_name}')


def _refresh_sqlite() -> None:
    from apps.workspaces.models import Workspace

    workspace_ids = list(
        Workspace.objects.filter(is_active=True).values_list('id', flat=True)
    )
    if not workspace_ids:
        return

    queries = ['DELETE FROM mv_entries_counters', 'DELETE FROM mv_exits_counters',
               'DELETE FROM mv_bins_counters', 'DELETE FROM mv_dashboard_monthly',
               'DELETE FROM mv_dashboard_daily', 'DELETE FROM mv_cava_stats',
               'DELETE FROM mv_cava_hall_stats', 'DELETE FROM mv_client_stats']

    for ws_id in workspace_ids:
        queries.extend([
            f"""
            INSERT INTO mv_entries_counters (workspace_id, total, pending, in_progress, completed)
            SELECT {ws_id},
                   COUNT(e.id),
                   SUM(CASE WHEN e.status = 'pendiente' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN e.status = 'en_progreso' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN e.status = 'completada' THEN 1 ELSE 0 END)
            FROM order_entry e
            INNER JOIN core_client c ON c.id = e.client_id
            WHERE c.workspace_id = {ws_id}
            """,
            f"""
            INSERT INTO mv_exits_counters (workspace_id, total, pending, in_progress, completed)
            SELECT {ws_id},
                   COUNT(x.id),
                   SUM(CASE WHEN x.status = 'pendiente' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN x.status = 'en_progreso' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN x.status = 'completada' THEN 1 ELSE 0 END)
            FROM order_exit x
            INNER JOIN core_client c ON c.id = x.client_id
            WHERE c.workspace_id = {ws_id}
            """,
            f"""
            INSERT INTO mv_bins_counters (workspace_id, total, received, stored, dispatched)
            SELECT {ws_id},
                   COUNT(b.id),
                   SUM(CASE WHEN b.status = 'en_recibo' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN b.status = 'almacenado' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN b.status = 'despachado' THEN 1 ELSE 0 END)
            FROM inventory_bin b
            INNER JOIN core_client c ON c.id = b.client_id
            WHERE c.workspace_id = {ws_id} AND b.is_active = 1
            """,
            f"""
            INSERT INTO mv_dashboard_monthly (workspace_id, month, entries, exits, bins)
            SELECT {ws_id}, month, SUM(entries), SUM(exits), SUM(bins)
            FROM (
                SELECT strftime('%Y-%m-01', e.created_at) AS month,
                       COUNT(*) AS entries, 0 AS exits, 0 AS bins
                FROM order_entry e
                INNER JOIN core_client c ON c.id = e.client_id
                WHERE c.workspace_id = {ws_id}
                GROUP BY 1
                UNION ALL
                SELECT strftime('%Y-%m-01', x.created_at), 0, COUNT(*), 0
                FROM order_exit x
                INNER JOIN core_client c ON c.id = x.client_id
                WHERE c.workspace_id = {ws_id}
                GROUP BY 1
                UNION ALL
                SELECT strftime('%Y-%m-01', b.created_at), 0, 0, COUNT(*)
                FROM inventory_bin b
                INNER JOIN core_client c ON c.id = b.client_id
                WHERE c.workspace_id = {ws_id}
                GROUP BY 1
            ) t
            GROUP BY month
            """,
            f"""
            INSERT INTO mv_dashboard_daily (workspace_id, day, entries, exits, bins)
            SELECT {ws_id}, day, SUM(entries), SUM(exits), SUM(bins)
            FROM (
                SELECT date(e.created_at) AS day, COUNT(*) AS entries, 0 AS exits, 0 AS bins
                FROM order_entry e
                INNER JOIN core_client c ON c.id = e.client_id
                WHERE c.workspace_id = {ws_id}
                GROUP BY 1
                UNION ALL
                SELECT date(x.created_at), 0, COUNT(*), 0
                FROM order_exit x
                INNER JOIN core_client c ON c.id = x.client_id
                WHERE c.workspace_id = {ws_id}
                GROUP BY 1
                UNION ALL
                SELECT date(b.created_at), 0, 0, COUNT(*)
                FROM inventory_bin b
                INNER JOIN core_client c ON c.id = b.client_id
                WHERE c.workspace_id = {ws_id}
                GROUP BY 1
            ) t
            GROUP BY day
            """,
            f"""
            INSERT INTO mv_cava_stats (workspace_id, cava_id, total, libre, ocupada, asignada)
            SELECT {ws_id}, s.id,
                   SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 AND p.status = 'libre' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 AND p.status = 'ocupado' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 AND p.status = 'asignado' THEN 1 ELSE 0 END)
            FROM storage_cava s
            INNER JOIN storage_storage st ON st.id = s.storage_id
            LEFT JOIN storage_position p ON p.cava_id = s.id
            WHERE st.workspace_id = {ws_id} AND s.is_virtual = 0
            GROUP BY s.id
            """,
            f"""
            INSERT INTO mv_cava_hall_stats (id, workspace_id, cava_id, hall, total, libre, ocupada, asignada)
            SELECT ROW_NUMBER() OVER (ORDER BY s.id, p.hall) + ({ws_id} * 10000),
                   {ws_id}, s.id, p.hall,
                   SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 AND p.status = 'libre' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 AND p.status = 'ocupado' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN p.is_active = 1 AND s.is_virtual = 0 AND p.status = 'asignado' THEN 1 ELSE 0 END)
            FROM storage_cava s
            INNER JOIN storage_storage st ON st.id = s.storage_id
            LEFT JOIN storage_position p ON p.cava_id = s.id
            WHERE st.workspace_id = {ws_id} AND s.is_virtual = 0 AND p.hall IS NOT NULL
            GROUP BY s.id, p.hall
            """,
            f"""
            INSERT INTO mv_client_stats (
                workspace_id, client_id, client_name, client_slug,
                positions_used, bins_in_warehouse,
                entries_total, entries_pending, entries_in_progress, entries_completed,
                exits_total, exits_pending, exits_in_progress, exits_completed
            )
            SELECT {ws_id}, c.id, c.name, c.slug,
                   COALESCE((
                       SELECT COUNT(DISTINCT b.position_id)
                       FROM inventory_bin b
                       INNER JOIN storage_position p ON p.id = b.position_id
                       INNER JOIN storage_cava s ON s.id = p.cava_id
                       WHERE b.client_id = c.id AND b.is_active = 1
                         AND b.position_id IS NOT NULL
                         AND b.status IN ('almacenado', 'en_despacho')
                         AND s.is_virtual = 0
                   ), 0),
                   COALESCE((
                       SELECT COUNT(*)
                       FROM inventory_bin b2
                       WHERE b2.client_id = c.id AND b2.is_active = 1
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
            WHERE c.workspace_id = {ws_id}
            ORDER BY c.name
            """,
        ])

    with connection.cursor() as cursor:
        for sql in queries:
            cursor.execute(sql)
