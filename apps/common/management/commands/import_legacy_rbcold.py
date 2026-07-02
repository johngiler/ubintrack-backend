from django.core.management.base import BaseCommand, CommandError

from apps.common.legacy_import.constants import DEFAULT_WORKSPACE_SLUG
from apps.common.legacy_import.import_rbcold import LegacyRbcoldImporter


class Command(BaseCommand):
    help = (
        "Importa datos operativos legacy de rbcold desde JSON parseado al workspace multi-tenant. "
        "Requiere seed_platform_data, ensure_default_workspace --slug rbcold y los JSON de "
        "extract_legacy_rbcold (generados en dev; no hace falta pg_restore en prod)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--workspace-slug",
            default=DEFAULT_WORKSPACE_SLUG,
            help="Slug del owner destino (default: rbcold)",
        )
        parser.add_argument(
            "--parsed-dir",
            default=None,
            help="Directorio con JSON parseado (default: data/rbcold/import/parsed)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula la importación dentro de una transacción con rollback.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Requerido para escribir datos (sin esto solo permite --dry-run).",
        )
        parser.add_argument(
            "--clear-workspace",
            action="store_true",
            help="Borra datos operativos existentes del workspace antes de importar.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        confirm = options["confirm"]
        clear_workspace = options["clear_workspace"]

        if not dry_run and not confirm:
            raise CommandError(
                "Importación real bloqueada por seguridad. "
                "Usa --dry-run para simular o --confirm para ejecutar."
            )

        if clear_workspace and not confirm and not dry_run:
            raise CommandError("--clear-workspace requiere --confirm o --dry-run.")

        importer = LegacyRbcoldImporter(
            workspace_slug=options["workspace_slug"],
            parsed_dir=options["parsed_dir"],
            dry_run=dry_run,
            clear_workspace=clear_workspace,
        )

        if dry_run:
            preview = importer.preview()
            self.stdout.write(self.style.WARNING("Modo dry-run (rollback al final)."))
            if preview.get("tables"):
                self.stdout.write("Filas en JSON:")
                for key, meta in preview["tables"].items():
                    rows = meta["rows"] if isinstance(meta, dict) else meta
                    self.stdout.write(f"  - {key}: {rows}")

        try:
            result = importer.import_all()
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc

        mode = "simulación" if result["dry_run"] else "importación"
        self.stdout.write(self.style.SUCCESS(f"Legacy rbcold — {mode} completada."))
        self.stdout.write(f"Workspace: {result['workspace']}")
        for key, count in sorted(result["stats"].items()):
            self.stdout.write(f"  - {key}: {count}")

        if result["warnings"]:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("Advertencias:"))
            for warning in result["warnings"]:
                self.stdout.write(f"  • {warning}")

        if result.get("id_maps_file"):
            self.stdout.write("")
            self.stdout.write(f"Mapa de IDs legacy → nuevo: {result['id_maps_file']}")
