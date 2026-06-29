"""
Genera las plantillas Excel para importación masiva de clientes y pedidos históricos.

Uso:
  python manage.py export_import_templates
  python manage.py export_import_templates --output-dir /ruta/salida
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand

from apps.common.utils.excel_import_templates import (
    default_import_templates_dir,
    write_import_templates,
)


class Command(BaseCommand):
    help = (
        "Genera plantilla_empresas_clientes.xlsx y plantilla_pedidos_historicos.xlsx "
        "para que un tenant prepare datos legacy antes de importarlos."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            type=str,
            default="",
            help=(
                "Directorio de salida (por defecto: backend/data/import_templates/)."
            ),
        )

    def handle(self, *args, **options):
        raw = (options.get("output_dir") or "").strip()
        output_dir = Path(raw) if raw else None
        clients_path, orders_path = write_import_templates(output_dir)
        target = output_dir or default_import_templates_dir()
        self.stdout.write(self.style.SUCCESS(f"Plantillas generadas en {target}:"))
        self.stdout.write(f"  · {clients_path.name}")
        self.stdout.write(f"  · {orders_path.name}")
