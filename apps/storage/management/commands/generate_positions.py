from django.core.management.base import BaseCommand
from apps.storage.models import Cava, Position


class Command(BaseCommand):
    help = 'Genera posiciones automáticamente para todas las cavas'

    def add_arguments(self, parser):
        parser.add_argument(
            '--virtual',
            action='store_true',
            help='Genera posiciones para cavas virtuales (Pasillo A, Rack 1, Lugar A)'
        )
        parser.add_argument(
            '--levels',
            type=int,
            default=5,
            help='Número de niveles a crear (solo para cavas virtuales, default: 5)'
        )

    def handle(self, *args, **kwargs):
        is_virtual = kwargs.get('virtual', False)
        num_levels = kwargs.get('levels', 5)

        cavas = Cava.objects.all()

        if is_virtual:
            # Configuración para cavas virtuales
            self.stdout.write(self.style.SUCCESS(
                f"Modo virtual activado: Generando posiciones con Pasillo A, Rack 1, Lugar A y {num_levels} niveles"))

            halls = ['A']  # Solo Pasillo A
            racks = [1]    # Solo Rack 1
            levels = range(1, num_levels + 1)  # Niveles según parámetro
            places = ['A']  # Solo Lugar A

        else:
            # Configuración para cavas normales
            halls = ['A', 'B', 'C', 'D']  # Pasillos PA a PD
            racks = range(1, 18)          # Racks R1 a R17
            levels = range(1, 6)          # Niveles N1 a N5
            places = ['A', 'B']           # Posiciones A y B

        for cava in cavas:
            if not cava.positions_has_been_generated:
                cv = cava.name.split()
                # Tomar las iniciales de todas las palabras del nombre
                cava_name = ''.join([word[0].upper() for word in cv])
                self.stdout.write(f"Generando posiciones para {cava_name}...")

                for hall in halls:
                    hall_code = f"P{hall}"

                    for rack in racks:
                        rack_code = f"R{rack}"

                        for level in levels:
                            level_code = f"N{level}"

                            for place in places:
                                place_code = place
                                position_code = f"{cava_name}-{rack_code}-{level_code}{place_code}"

                                # Verificar si la posición ya existe
                                if not Position.objects.filter(code=position_code).exists():
                                    # Crear la posición si no existe
                                    Position.objects.create(
                                        code=position_code,
                                        level=level,
                                        place=place,
                                        rack=rack,
                                        hall=hall,
                                        cava=cava,
                                        is_active=True
                                    )

                                    self.stdout.write(
                                        f"Posición {position_code} creada.")
                                else:
                                    self.stdout.write(
                                        f"Posición {position_code} ya existe.")

                cava.positions_has_been_generated = True
                cava.save()
