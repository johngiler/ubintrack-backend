from django.utils import timezone
from django.db.models import Sum
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.common.utils.permissions_mixins import StrictDjangoModelPermissions

from apps.storage.models import Storage, Cava
from apps.dashboard.models import DashboardMonthlyAgg, DashboardDailyAgg
from apps.dashboard.models import (
    CavaStatsAgg,
    HallStatsAgg,
    EntriesCountersAgg,
    ExitsCountersAgg,
    BinsCountersAgg,
    ClientStatsAgg,
)
from .serializers import ClientStatsResponseSerializer


class DashboardStatsView(APIView):
    """Vista para obtener las estadísticas del dashboard (datos precalculados)."""
    permission_classes = [IsAuthenticated,]

    DAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

    def _build_stats(self) -> dict:
        # Entradas desde MV
        entries_mv = EntriesCountersAgg.objects.first()
        entries_total = entries_mv.total if entries_mv else 0
        entries_pending = entries_mv.pending if entries_mv else 0
        entries_in_progress = entries_mv.in_progress if entries_mv else 0
        entries_completed = entries_mv.completed if entries_mv else 0

        # Bins desde MV
        bins_mv = BinsCountersAgg.objects.first()
        bins_total = bins_mv.total if bins_mv else 0
        bins_received = bins_mv.received if bins_mv else 0
        bins_stored = bins_mv.stored if bins_mv else 0
        bins_dispatched = bins_mv.dispatched if bins_mv else 0

        # Salidas desde MV
        exits_mv = ExitsCountersAgg.objects.first()
        exits_total = exits_mv.total if exits_mv else 0
        exits_pending = exits_mv.pending if exits_mv else 0
        exits_in_progress = exits_mv.in_progress if exits_mv else 0
        exits_completed = exits_mv.completed if exits_mv else 0

        # Posiciones desde MV por cava
        mv_totals = CavaStatsAgg.objects.aggregate(
            total=Sum('total'), libre=Sum('libre'), ocupada=Sum('ocupada'), asignada=Sum('asignada')
        )
        positions_total = mv_totals.get('total') or 0
        positions_free = mv_totals.get('libre') or 0
        positions_occupied = mv_totals.get('ocupada') or 0
        positions_assigned = mv_totals.get('asignada') or 0

        total_percentage = 0.0
        occupied_percentage = 0.0
        assigned_percentage = 0.0
        free_percentage = 0.0
        if positions_total > 0:
            total_percentage = (
                (positions_occupied + positions_assigned) / positions_total) * 100
            occupied_percentage = (positions_occupied / positions_total) * 100
            assigned_percentage = (positions_assigned / positions_total) * 100
            free_percentage = (positions_free / positions_total) * 100

        return {
            'entries': {
                'total_count': entries_total,
                'pending_count': entries_pending,
                'in_progress_count': entries_in_progress,
                'completed_count': entries_completed,
            },
            'bins': {
                'total_count': bins_total,
                'received_count': bins_received,
                'stored_count': bins_stored,
                'dispatched_count': bins_dispatched,
            },
            'exits': {
                'total_count': exits_total,
                'pending_count': exits_pending,
                'in_progress_count': exits_in_progress,
                'completed_count': exits_completed,
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

    def _get_monthly(self, limit: int = 12) -> list:
        qs = DashboardMonthlyAgg.objects.order_by('month')
        count = qs.count()
        if count > limit:
            qs = qs[count - limit:]
        return [
            {
                'month': row.month.strftime('%Y-%m'),
                'entries': row.entries,
                'exits': row.exits,
                'bins': row.bins,
            }
            for row in qs
        ]

    def _get_daily_last7(self) -> list:
        qs = list(DashboardDailyAgg.objects.order_by('-day')[:7])[::-1]
        return [
            {
                'day': self.DAYS[row.day.isoweekday() - 1],
                'entries': row.entries,
                'exits': row.exits,
                'bins': row.bins,
            }
            for row in qs
        ]

    def get(self, request):
        # Restringir a tipo de usuario Administrador (o superusuario)
        user = request.user
        if getattr(user, 'user_type', None) != 'administrador' and not user.is_superuser:
            return Response({'detail': 'No autorizado'}, status=status.HTTP_403_FORBIDDEN)
        data = {
            'stats': self._build_stats(),
            'charts': {
                'monthly': self._get_monthly(),
                'daily': self._get_daily_last7(),
            },
            'timestamp': timezone.now(),
        }

        return Response(data, status=status.HTTP_200_OK)


# Vistas para el mapa visual del almacén

class WarehouseStructureView(APIView):
    """Vista para obtener la estructura completa del almacén"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if getattr(user, 'user_type', None) != 'administrador' and not user.is_superuser:
            return Response({'detail': 'No autorizado'}, status=status.HTTP_403_FORBIDDEN)
        """Obtiene la estructura jerárquica completa del almacén"""
        try:
            # Obtener el storage principal (asumiendo que hay uno)
            storage = Storage.objects.filter(is_active=True).first()
            if not storage:
                return Response({
                    'success': False,
                    'error': 'No se encontró almacén activo'
                }, status=status.HTTP_404_NOT_FOUND)

            # Obtener todas las cavas del storage (sin prefetch, usaremos MVs)
            cavas = Cava.objects.filter(storage=storage, is_active=True)

            # Construir estructura jerárquica
            cavas_data = []
            global_stats = {
                'total_positions': 0,
                'libre_positions': 0,
                'ocupada_positions': 0,
                'asignada_positions': 0,
                'picking_positions': 0,
                'utilization_rate': 0.0
            }

            for cava in cavas:
                # Estadísticas precalculadas por cava
                try:
                    agg = CavaStatsAgg.objects.get(cava_id=cava.id)
                    total_positions = agg.total or 0
                    libre_positions = agg.libre or 0
                    ocupada_positions = agg.ocupada or 0
                    asignada_positions = agg.asignada or 0
                except CavaStatsAgg.DoesNotExist:
                    total_positions = libre_positions = ocupada_positions = asignada_positions = 0

                picking_positions = 0

                utilization_rate = 0.0
                if total_positions > 0:
                    utilization_rate = (
                        (ocupada_positions + asignada_positions) / total_positions) * 100

                # Actualizar estadísticas globales
                global_stats['total_positions'] += total_positions
                global_stats['libre_positions'] += libre_positions
                global_stats['ocupada_positions'] += ocupada_positions
                global_stats['asignada_positions'] += asignada_positions
                global_stats['picking_positions'] += picking_positions

                # Resumen por pasillo usando MV
                halls_data = []
                for hall_agg in HallStatsAgg.objects.filter(cava_id=cava.id):
                    hall_total = hall_agg.total or 0
                    hall_libre = hall_agg.libre or 0
                    hall_ocupada = hall_agg.ocupada or 0
                    hall_asignada = hall_agg.asignada or 0
                    hall_util = 0.0
                    if hall_total > 0:
                        hall_util = (
                            (hall_ocupada + hall_asignada) / hall_total) * 100
                    halls_data.append({
                        'id': hall_agg.hall,
                        'name': f"Pasillo {hall_agg.hall}",
                        'positions': {
                            'total': hall_total,
                            'libre': hall_libre,
                            'ocupada': hall_ocupada,
                            'asignada': hall_asignada,
                            'utilization_rate': round(hall_util, 2),
                            'occupied_percentage': round((hall_ocupada / hall_total) * 100, 2) if hall_total else 0.0,
                            'assigned_percentage': round((hall_asignada / hall_total) * 100, 2) if hall_total else 0.0,
                            'free_percentage': round((hall_libre / hall_total) * 100, 2) if hall_total else 0.0,
                        }
                    })

                cava_data = {
                    'id': cava.id,
                    'name': cava.name,
                    'condition': cava.condition,
                    'current_temp': cava.current_temp,
                    'total_levels': cava.total_levels,
                    'positions_has_been_generated': cava.positions_has_been_generated,
                    'is_active': cava.is_active,
                    'halls': halls_data,
                    'statistics': {
                        'total_positions': total_positions,
                        'libre_positions': libre_positions,
                        'ocupada_positions': ocupada_positions,
                        'asignada_positions': asignada_positions,
                        'picking_positions': picking_positions,
                        'utilization_rate': round(utilization_rate, 2)
                    }
                }
                cavas_data.append(cava_data)

            # Calcular utilización global
            if global_stats['total_positions'] > 0:
                global_stats['utilization_rate'] = round(
                    ((global_stats['ocupada_positions'] + global_stats['asignada_positions']) /
                     global_stats['total_positions']) * 100, 2
                )

            response_data = {
                'success': True,
                'data': {
                    'storage': {
                        'id': storage.id,
                        'name': storage.name,
                        'is_active': storage.is_active,
                        'created_at': storage.created_at,
                        'cavas': cavas_data
                    },
                    'global_statistics': global_stats
                }
            }

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ClientStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if getattr(user, 'user_type', None) != 'administrador' and not user.is_superuser:
            return Response({'detail': 'No autorizado'}, status=status.HTTP_403_FORBIDDEN)
        data = [
            {
                'client_id': row.client_id,
                'client_name': row.client_name,
                'client_slug': row.client_slug,
                'positions_used': row.positions_used,
                'bins_in_warehouse': row.bins_in_warehouse,
                'entries_total': row.entries_total,
                'entries_pending': row.entries_pending,
                'entries_in_progress': row.entries_in_progress,
                'entries_completed': row.entries_completed,
                'exits_total': row.exits_total,
                'exits_pending': row.exits_pending,
                'exits_in_progress': row.exits_in_progress,
                'exits_completed': row.exits_completed,
            }
            for row in ClientStatsAgg.objects.all().order_by('-bins_in_warehouse', '-positions_used', 'client_name')
        ]
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)
