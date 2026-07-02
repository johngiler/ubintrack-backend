from django.core.management.base import BaseCommand, CommandError

from apps.common.legacy_import.extract import extract_legacy_dump


class Command(BaseCommand):
    help = (
        "Extrae datos operativos del dump PostgreSQL legacy (formato pg_dump custom) "
        "a JSON revisable en data/rbcold/import/parsed/."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dump-path",
            default=None,
            help="Ruta al .sql custom (default: data/rbcold/db/rbcold_db_backup_20260701_161324.sql)",
        )
        parser.add_argument(
            "--parsed-dir",
            default=None,
            help="Directorio de salida JSON (default: data/rbcold/import/parsed)",
        )

    def handle(self, *args, **options):
        try:
            manifest = extract_legacy_dump(
                dump_path=options["dump_path"],
                parsed_dir=options["parsed_dir"],
            )
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS("Extracción legacy completada."))
        for key, meta in manifest.get("tables", {}).items():
            self.stdout.write(f"  - {key}: {meta.get('rows', 0)} filas → {meta.get('file')}")
        self.stdout.write("")
        self.stdout.write("Siguiente paso (revisar JSON y luego):")
        self.stdout.write("  python manage.py import_legacy_rbcold --dry-run")
        self.stdout.write("  python manage.py import_legacy_rbcold --confirm")
