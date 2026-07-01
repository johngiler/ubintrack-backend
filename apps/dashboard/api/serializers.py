"""Serializers de referencia para respuestas del dashboard (documentación)."""

from rest_framework import serializers


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
