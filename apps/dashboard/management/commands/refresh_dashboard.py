from django.core.management.base import BaseCommand

from apps.dashboard.refresh import refresh_dashboard_aggregates


class Command(BaseCommand):
    help = "Refresca agregados del dashboard (materialized views o tablas SQLite)"

    def handle(self, *args, **options):
        self.stdout.write("Refreshing dashboard aggregates...")
        refresh_dashboard_aggregates()
        self.stdout.write(self.style.SUCCESS("Dashboard aggregates refreshed."))
