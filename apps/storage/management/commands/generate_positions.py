from django.core.management.base import BaseCommand, CommandError

from apps.core.choices import PositionStatus
from apps.storage.models import Cava, Position
from apps.workspaces.models import Workspace


class Command(BaseCommand):
    help = (
        "Genera posiciones en masa para una cava existente a partir de "
        "pasillos, racks, niveles y profundidades (A/B)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--cava",
            type=str,
            required=True,
            help='Nombre exacto de la cava (debe existir en la BD), p. ej. "Cava 1"',
        )
        parser.add_argument(
            "--halls",
            type=str,
            required=True,
            help="Pasillos separados por coma, p. ej. A,B,C,D",
        )
        parser.add_argument(
            "--racks",
            type=int,
            required=True,
            help="Cantidad de racks por pasillo (1..N)",
        )
        parser.add_argument(
            "--levels",
            type=int,
            required=True,
            help="Cantidad de niveles por rack (1..N)",
        )
        parser.add_argument(
            "--places",
            type=str,
            default="A,B",
            help="Profundidades separadas por coma (A=adelante, B=atrás). Default: A,B",
        )
        parser.add_argument(
            "--workspace-slug",
            type=str,
            default=None,
            help="Slug del workspace (opcional, útil si hay cavas homónimas)",
        )
        parser.add_argument(
            "--status",
            type=str,
            default=PositionStatus.LIBRE,
            choices=[c.value for c in PositionStatus],
            help="Estado inicial de las posiciones creadas",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula la generación sin escribir en la BD",
        )

    def handle(self, *args, **options):
        cava_name = options["cava"].strip()
        halls = self._parse_letters(options["halls"], "pasillos")
        places = self._parse_letters(options["places"], "profundidades")
        racks = options["racks"]
        levels = options["levels"]
        status = options["status"]
        dry_run = options["dry_run"]

        if racks < 1:
            raise CommandError("--racks debe ser >= 1")
        if levels < 1:
            raise CommandError("--levels debe ser >= 1")

        valid_places = {choice[0] for choice in Position.PLACE_CHOICES}
        invalid_places = set(places) - valid_places
        if invalid_places:
            raise CommandError(
                f"Profundidades inválidas: {', '.join(sorted(invalid_places))}. "
                f"Valores permitidos: {', '.join(sorted(valid_places))}"
            )

        valid_halls = {choice[0] for choice in Position.HALL_CHOICES}
        invalid_halls = set(halls) - valid_halls
        if invalid_halls:
            raise CommandError(
                f"Pasillos inválidos: {', '.join(sorted(invalid_halls))}. "
                f"Valores permitidos: {', '.join(sorted(valid_halls))}"
            )

        cava = self._resolve_cava(cava_name, options.get("workspace_slug"))
        total_expected = len(halls) * racks * levels * len(places)

        self.stdout.write(
            f'Cava: "{cava.name}" (id={cava.pk}, bodega={cava.storage.name})'
        )
        self.stdout.write(
            f"Configuración: pasillos={','.join(halls)}, racks=1..{racks}, "
            f"niveles=1..{levels}, profundidades={','.join(places)}"
        )
        self.stdout.write(f"Posiciones a evaluar: {total_expected}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Modo dry-run: no se escribirá en la BD"))

        created_count = 0
        skipped_count = 0
        error_count = 0

        for hall in halls:
            for rack in range(1, racks + 1):
                for level in range(1, levels + 1):
                    for place in places:
                        exists = Position.objects.filter(
                            cava=cava,
                            hall=hall,
                            rack=rack,
                            level=level,
                            place=place,
                        ).exists()

                        if exists:
                            skipped_count += 1
                            continue

                        if dry_run:
                            created_count += 1
                            continue

                        try:
                            position = Position(
                                cava=cava,
                                hall=hall,
                                rack=rack,
                                level=level,
                                place=place,
                                status=status,
                            )
                            position.save()
                            created_count += 1
                        except Exception as exc:
                            error_count += 1
                            self.stdout.write(
                                self.style.ERROR(
                                    f"Error P{hall}-R{rack}-N{level}{place}: {exc}"
                                )
                            )

        if not dry_run and created_count > 0:
            cava.positions_has_been_generated = True
            cava.save(update_fields=["positions_has_been_generated", "updated_at"])

        mode = "simuladas" if dry_run else "creadas"
        self.stdout.write(
            self.style.SUCCESS(
                f"Proceso completado. {mode.capitalize()}: {created_count}, "
                f"Omitidas (ya existían): {skipped_count}, Errores: {error_count}"
            )
        )

    def _parse_letters(self, raw: str, label: str) -> list[str]:
        values = [part.strip().upper() for part in raw.split(",") if part.strip()]
        if not values:
            raise CommandError(f"Debe indicar al menos un valor para {label}")
        if len(values) != len(set(values)):
            raise CommandError(f"Hay valores duplicados en {label}: {raw}")
        return values

    def _resolve_cava(self, name: str, workspace_slug: str | None) -> Cava:
        qs = Cava.objects.filter(name=name, is_active=True).select_related("storage")

        if workspace_slug:
            ws = Workspace.objects.filter(slug=workspace_slug.strip().lower()).first()
            if not ws:
                raise CommandError(f'No existe workspace con slug "{workspace_slug}"')
            qs = qs.filter(storage__workspace=ws)

        count = qs.count()
        if count == 0:
            hint = (
                f' (workspace="{workspace_slug}")' if workspace_slug else ""
            )
            raise CommandError(
                f'No se encontró cava activa con nombre "{name}"{hint}. '
                "Créala primero en la BD."
            )
        if count > 1:
            raise CommandError(
                f'Hay {count} cavas activas con nombre "{name}". '
                "Usa --workspace-slug para desambiguar."
            )

        return qs.first()
