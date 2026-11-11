"""Parsea / importa el inventario inicial CDF del owner coldcave.

Uso típico:
  # Solo regenerar JSON desde el Excel
  python manage.py import_coldcave_initial_bins --parse-only \\
      --from-xlsx "/path/Inventario Inicial CDF.xlsx"

  # Dry-run contra el JSON
  python manage.py import_coldcave_initial_bins --dry-run

  # Carga real (también la hace la migración one-timer)
  python manage.py import_coldcave_initial_bins
"""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.inventory.utils.import_initial_bins import (
    DEFAULT_JSON,
    import_initial_bins,
    load_rows_from_json,
    parse_xlsx_to_rows,
    write_json,
)


class Command(BaseCommand):
    help = (
        "Importa inventario inicial de bins para coldcave desde "
        "data/imports/coldcave/inventario_inicial_cdf.json "
        "(opcionalmente regenera el JSON desde Excel)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--data",
            type=str,
            default=str(DEFAULT_JSON),
            help="Ruta al JSON de importación",
        )
        parser.add_argument(
            "--from-xlsx",
            type=str,
            default=None,
            help="Excel origen para regenerar el JSON antes de importar",
        )
        parser.add_argument(
            "--parse-only",
            action="store_true",
            help="Solo escribe el JSON; no toca la base",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Valida y reporta sin crear registros",
        )
        parser.add_argument(
            "--report",
            type=str,
            default=str(
                Path(settings.BASE_DIR)
                / "data"
                / "imports"
                / "coldcave"
                / "inventario_inicial_cdf_report.json"
            ),
            help="Ruta del reporte JSON de salida",
        )

    def handle(self, *args, **options):
        data_path = Path(options["data"])
        xlsx = options.get("from_xlsx")
        parse_only = options["parse_only"]
        dry_run = options["dry_run"]
        report_path = Path(options["report"])

        if xlsx:
            xlsx_path = Path(xlsx)
            if not xlsx_path.exists():
                raise CommandError(f"Excel no encontrado: {xlsx_path}")
            payload = parse_xlsx_to_rows(xlsx_path)
            write_json(payload, data_path)
            self.stdout.write(
                self.style.SUCCESS(
                    f"JSON escrito: {data_path} ({len(payload.get('rows') or [])} filas)"
                )
            )
            if parse_only:
                return

        if parse_only and not xlsx:
            raise CommandError("--parse-only requiere --from-xlsx")

        if not data_path.exists():
            raise CommandError(
                f"JSON no encontrado: {data_path}. "
                "Genéralo con --from-xlsx / --parse-only."
            )

        data = load_rows_from_json(data_path)
        report = import_initial_bins(data, dry_run=dry_run)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        created = report.get("created_bins") or report.get("would_create_bins") or 0
        self.stdout.write(
            self.style.SUCCESS(
                f"{'Dry-run' if dry_run else 'Import'} OK — bins={created} "
                f"reporte={report_path}"
            )
        )
        if report.get("errors"):
            self.stdout.write(
                self.style.WARNING(f"Errores/avisos: {len(report['errors'])}")
            )
            for err in report["errors"][:30]:
                self.stdout.write(f"  - {err}")
        if report.get("aborted") and not dry_run:
            raise CommandError("Importación abortada; revisa el reporte.")
