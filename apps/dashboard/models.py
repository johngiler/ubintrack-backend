from django.db import models


class DashboardMonthlyAgg(models.Model):
    """Materialized view: agregados mensuales por owner."""

    workspace_id = models.IntegerField()
    month = models.DateField()
    entries = models.IntegerField()
    exits = models.IntegerField()
    bins = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'mv_dashboard_monthly'


class DashboardDailyAgg(models.Model):
    """Materialized view: agregados diarios por owner."""

    workspace_id = models.IntegerField()
    day = models.DateField()
    entries = models.IntegerField()
    exits = models.IntegerField()
    bins = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'mv_dashboard_daily'


class CavaStatsAgg(models.Model):
    """Materialized view: resumen por cava y owner."""

    workspace_id = models.IntegerField()
    cava_id = models.IntegerField()
    total = models.IntegerField()
    libre = models.IntegerField()
    ocupada = models.IntegerField()
    asignada = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'mv_cava_stats'


class HallStatsAgg(models.Model):
    """Materialized view: resumen por cava, pasillo y owner."""

    id = models.BigIntegerField(primary_key=True)
    workspace_id = models.IntegerField()
    cava_id = models.IntegerField()
    hall = models.CharField(max_length=1)
    total = models.IntegerField()
    libre = models.IntegerField()
    ocupada = models.IntegerField()
    asignada = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'mv_cava_hall_stats'


class EntriesCountersAgg(models.Model):
    workspace_id = models.IntegerField(primary_key=True)
    total = models.IntegerField()
    pending = models.IntegerField()
    in_progress = models.IntegerField()
    completed = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'mv_entries_counters'


class ExitsCountersAgg(models.Model):
    workspace_id = models.IntegerField(primary_key=True)
    total = models.IntegerField()
    pending = models.IntegerField()
    in_progress = models.IntegerField()
    completed = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'mv_exits_counters'


class BinsCountersAgg(models.Model):
    workspace_id = models.IntegerField(primary_key=True)
    total = models.IntegerField()
    received = models.IntegerField()
    stored = models.IntegerField()
    dispatched = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'mv_bins_counters'


class ClientStatsAgg(models.Model):
    workspace_id = models.IntegerField()
    client_id = models.IntegerField()
    client_name = models.CharField(max_length=255)
    client_slug = models.CharField(max_length=255)
    positions_used = models.IntegerField()
    bins_in_warehouse = models.IntegerField()
    entries_total = models.IntegerField()
    entries_pending = models.IntegerField()
    entries_in_progress = models.IntegerField()
    entries_completed = models.IntegerField()
    exits_total = models.IntegerField()
    exits_pending = models.IntegerField()
    exits_in_progress = models.IntegerField()
    exits_completed = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'mv_client_stats'
