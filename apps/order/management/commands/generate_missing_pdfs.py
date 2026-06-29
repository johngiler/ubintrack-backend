from django.core.management.base import BaseCommand
from django.db.models import Q
from apps.order.models import Exit, ExitDetail, Entry, EntryDetail
from apps.order.services import update_exit_pdf, update_entry_pdf


class Command(BaseCommand):
    help = 'Genera PDFs faltantes para órdenes de salida y/o entrada completadas'

    def add_arguments(self, parser):
        parser.add_argument(
            '--exits',
            action='store_true',
            help='Generar PDFs solo para órdenes de salida',
        )
        parser.add_argument(
            '--entries',
            action='store_true',
            help='Generar PDFs solo para órdenes de entrada',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo mostrar qué PDFs se generarían sin crearlos',
        )

    def handle(self, *args, **options):
        exits_only = options['exits']
        entries_only = options['entries']
        dry_run = options['dry_run']

        # Si no se especifica ninguno, generar ambos
        if not exits_only and not entries_only:
            exits_only = True
            entries_only = True

        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write(self.style.WARNING('GENERACIÓN DE PDFs FALTANTES'))
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write('')

        total_generated = 0
        total_errors = 0

        # Procesar salidas
        if exits_only:
            self.stdout.write(self.style.SUCCESS('📄 PROCESANDO ÓRDENES DE SALIDA'))
            self.stdout.write('-' * 70)

            exits_sin_pdf = Exit.objects.filter(
                Q(status='completada') | Q(exit_date__isnull=False)
            ).filter(
                Q(exit_detail__pdf_document__isnull=True) |
                Q(exit_detail__pdf_document='')
            )

            count = exits_sin_pdf.count()
            self.stdout.write(f'Salidas que necesitan PDF: {count}')

            if count > 0:
                for exit in exits_sin_pdf:
                    self.stdout.write(f'\n  • {exit.code} (ID: {exit.id})')
                    self.stdout.write(f'    Estado: {exit.status} | Fecha salida: {exit.exit_date or "Sin fecha"}')

                    if dry_run:
                        self.stdout.write(self.style.WARNING('    [DRY RUN] Se generaría el PDF'))
                        continue

                    try:
                        # Asegurar que existe ExitDetail
                        try:
                            detail = exit.exit_detail
                        except ExitDetail.DoesNotExist:
                            detail = ExitDetail.objects.create(exit=exit)
                            self.stdout.write(self.style.SUCCESS('    ✓ ExitDetail creado'))

                        # Generar PDF
                        pdf_path = update_exit_pdf(exit)
                        exit.refresh_from_db()
                        self.stdout.write(
                            self.style.SUCCESS(f'    ✓ PDF generado: {exit.exit_detail.pdf_document}')
                        )
                        total_generated += 1
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f'    ✗ ERROR: {e}'))
                        total_errors += 1
                        if self.verbosity >= 2:
                            import traceback
                            self.stdout.write(traceback.format_exc())
            else:
                self.stdout.write(self.style.SUCCESS('  ✓ No hay salidas pendientes de generar PDF'))

            self.stdout.write('')

        # Procesar entradas
        if entries_only:
            self.stdout.write(self.style.SUCCESS('📄 PROCESANDO ÓRDENES DE ENTRADA'))
            self.stdout.write('-' * 70)

            entries_sin_pdf = Entry.objects.filter(
                Q(status='completada') | Q(entry_date__isnull=False)
            ).filter(
                Q(entry_detail__pdf_document__isnull=True) |
                Q(entry_detail__pdf_document='')
            )

            count = entries_sin_pdf.count()
            self.stdout.write(f'Entradas que necesitan PDF: {count}')

            if count > 0:
                for entry in entries_sin_pdf:
                    self.stdout.write(f'\n  • {entry.code} (ID: {entry.id})')
                    self.stdout.write(f'    Estado: {entry.status} | Fecha entrada: {entry.entry_date or "Sin fecha"}')

                    if dry_run:
                        self.stdout.write(self.style.WARNING('    [DRY RUN] Se generaría el PDF'))
                        continue

                    try:
                        # Asegurar que existe EntryDetail
                        try:
                            detail = entry.entry_detail
                        except EntryDetail.DoesNotExist:
                            detail = EntryDetail.objects.create(entry=entry)
                            self.stdout.write(self.style.SUCCESS('    ✓ EntryDetail creado'))

                        # Generar PDF
                        pdf_path = update_entry_pdf(entry)
                        entry.refresh_from_db()
                        self.stdout.write(
                            self.style.SUCCESS(f'    ✓ PDF generado: {entry.entry_detail.pdf_document}')
                        )
                        total_generated += 1
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f'    ✗ ERROR: {e}'))
                        total_errors += 1
                        if self.verbosity >= 2:
                            import traceback
                            self.stdout.write(traceback.format_exc())
            else:
                self.stdout.write(self.style.SUCCESS('  ✓ No hay entradas pendientes de generar PDF'))

            self.stdout.write('')

        # Resumen final
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write(self.style.WARNING('RESUMEN'))
        self.stdout.write(self.style.WARNING('=' * 70))

        if dry_run:
            self.stdout.write(self.style.WARNING('Modo DRY RUN: No se generaron PDFs'))
        else:
            self.stdout.write(self.style.SUCCESS(f'✓ PDFs generados: {total_generated}'))
            if total_errors > 0:
                self.stdout.write(self.style.ERROR(f'✗ Errores: {total_errors}'))

        self.stdout.write('')

