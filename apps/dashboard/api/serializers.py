from rest_framework import serializers


class EntriesStatsSerializer(serializers.Serializer):
    total_count = serializers.IntegerField()
    pending_count = serializers.IntegerField()
    in_progress_count = serializers.IntegerField()
    completed_count = serializers.IntegerField()


class BinsStatsSerializer(serializers.Serializer):
    total_count = serializers.IntegerField()
    received_count = serializers.IntegerField()
    stored_count = serializers.IntegerField()
    dispatched_count = serializers.IntegerField()


class ExitsStatsSerializer(serializers.Serializer):
    total_count = serializers.IntegerField()
    pending_count = serializers.IntegerField()
    in_progress_count = serializers.IntegerField()
    completed_count = serializers.IntegerField()


class PositionsStatsSerializer(serializers.Serializer):
    total_count = serializers.IntegerField()
    occupied_count = serializers.IntegerField()
    assigned_count = serializers.IntegerField()
    free_count = serializers.IntegerField()
    total_percentage = serializers.FloatField()
    occupied_percentage = serializers.FloatField()
    assigned_percentage = serializers.FloatField()
    free_percentage = serializers.FloatField()


class MonthlyDataSerializer(serializers.Serializer):
    month = serializers.CharField()
    entries = serializers.IntegerField()
    exits = serializers.IntegerField()
    bins = serializers.IntegerField()


class WeeklyDataSerializer(serializers.Serializer):
    day = serializers.CharField()
    entries = serializers.IntegerField()
    exits = serializers.IntegerField()


class StatsSerializer(serializers.Serializer):
    entries = EntriesStatsSerializer()
    bins = BinsStatsSerializer()
    exits = ExitsStatsSerializer()
    positions = PositionsStatsSerializer()


class ChartDataSerializer(serializers.Serializer):
    monthly = MonthlyDataSerializer(many=True)
    weekly = WeeklyDataSerializer(many=True)


class DashboardStatsSerializer(serializers.Serializer):
    """Serializer para las estadísticas del dashboard"""
    stats = StatsSerializer()
    charts = ChartDataSerializer()


# Serializers para el mapa visual del almacén

class StorageBasicSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class CavaBasicSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    condition = serializers.CharField()
    current_temp = serializers.IntegerField()


class PositionPlaceSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    position_code = serializers.CharField()
    status = serializers.CharField()
    is_picking = serializers.BooleanField()


class PositionLevelSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    name = serializers.CharField()
    places = PositionPlaceSerializer(many=True)


class PositionRackSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    name = serializers.CharField()
    levels = PositionLevelSerializer(many=True)


class HallStatsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    libre = serializers.IntegerField()
    ocupada = serializers.IntegerField()
    asignada = serializers.IntegerField()


class HallSummarySerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    positions = HallStatsSerializer()


class PositionHallSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    racks = PositionRackSerializer(many=True)


class CavaStatsSerializer(serializers.Serializer):
    total_positions = serializers.IntegerField()
    libre_positions = serializers.IntegerField()
    ocupada_positions = serializers.IntegerField()
    asignada_positions = serializers.IntegerField()
    picking_positions = serializers.IntegerField()
    utilization_rate = serializers.FloatField()


class CavaWithHallsSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    condition = serializers.CharField()
    current_temp = serializers.IntegerField()
    total_levels = serializers.IntegerField()
    positions_has_been_generated = serializers.BooleanField()
    is_active = serializers.BooleanField()
    halls = PositionHallSerializer(many=True)
    statistics = CavaStatsSerializer()


class StorageWithCavasSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    is_active = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    cavas = CavaWithHallsSerializer(many=True)


class WarehouseStructureSerializer(serializers.Serializer):
    storage = StorageWithCavasSerializer()
    global_statistics = CavaStatsSerializer()


class WarehouseStructureResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = WarehouseStructureSerializer()


class PositionDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    status = serializers.CharField()
    level = serializers.IntegerField()
    place = serializers.CharField()
    rack = serializers.IntegerField()
    hall = serializers.CharField()
    cava = CavaBasicSerializer()
    is_picking = serializers.BooleanField()
    qr_code_image = serializers.CharField(allow_null=True)
    zpl_code = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    is_active = serializers.BooleanField()


class PositionListSerializer(serializers.Serializer):
    positions = PositionDetailSerializer(many=True)
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)


class PositionListResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PositionListSerializer()


class UserBasicSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()


class PositionFullDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    status = serializers.CharField()
    level = serializers.IntegerField()
    place = serializers.CharField()
    rack = serializers.IntegerField()
    hall = serializers.CharField()
    cava = CavaBasicSerializer()
    is_picking = serializers.BooleanField()
    qr_code_image = serializers.CharField(allow_null=True)
    zpl_code = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    created_by = UserBasicSerializer(allow_null=True)
    updated_by = UserBasicSerializer(allow_null=True)
    is_active = serializers.BooleanField()


class PositionDetailResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = serializers.DictField()


class PositionSearchSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    status = serializers.CharField()
    level = serializers.IntegerField()
    place = serializers.CharField()
    rack = serializers.IntegerField()
    hall = serializers.CharField()
    cava_name = serializers.CharField()
    is_picking = serializers.BooleanField()


class PositionSearchResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = serializers.DictField()


class GlobalStatsSerializer(serializers.Serializer):
    total_positions = serializers.IntegerField()
    libre_positions = serializers.IntegerField()
    ocupada_positions = serializers.IntegerField()
    asignada_positions = serializers.IntegerField()
    picking_positions = serializers.IntegerField()
    utilization_rate = serializers.FloatField()


class CavaSummaryStatsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    libre = serializers.IntegerField()
    ocupada = serializers.IntegerField()
    asignada = serializers.IntegerField()
    picking = serializers.IntegerField()


class CavaSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    condition = serializers.CharField()
    current_temp = serializers.IntegerField()
    total_levels = serializers.IntegerField()
    positions = CavaSummaryStatsSerializer()
    utilization_rate = serializers.FloatField()
    halls_summary = HallSummarySerializer(many=True)


class WarehouseStatsSerializer(serializers.Serializer):
    timestamp = serializers.DateTimeField()
    global_stats = GlobalStatsSerializer()
    cavas_summary = CavaSummarySerializer(many=True)


class WarehouseStatsResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = WarehouseStatsSerializer()


class ClientStatsSerializer(serializers.Serializer):
    client_id = serializers.IntegerField()
    client_name = serializers.CharField()
    client_slug = serializers.CharField()
    positions_used = serializers.IntegerField()
    bins_in_warehouse = serializers.IntegerField()
    entries_total = serializers.IntegerField()
    entries_pending = serializers.IntegerField()
    entries_in_progress = serializers.IntegerField()
    entries_completed = serializers.IntegerField()
    exits_total = serializers.IntegerField()
    exits_pending = serializers.IntegerField()
    exits_in_progress = serializers.IntegerField()
    exits_completed = serializers.IntegerField()


class ClientStatsResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ClientStatsSerializer(many=True)
