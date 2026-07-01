import os
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Sum, F
from django_currentuser.db.models import CurrentUserField
from django.db import transaction

from apps.core.choices import Conditions, OrderStatus, InTypes, BinStatus
from apps.common.utils.media_layout import (
    wms_entry_detail_image_upload,
    wms_entry_pdf_upload,
    wms_exit_detail_image_upload,
    wms_exit_pdf_upload,
)


class BaseModel(models.Model):
    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name='fecha de creación')

    updated_at = models.DateTimeField(
        auto_now=True, verbose_name='fecha de actualización')

    created_by = CurrentUserField(
        related_name='%(app_label)s_%(class)s_creates',
        verbose_name='creado por'
    )

    updated_by = CurrentUserField(
        related_name='%(app_label)s_%(class)s_updates',
        on_update=True,
        verbose_name='actualizado por'
    )

    class Meta:
        abstract = True


class Entry(BaseModel):
    client = models.ForeignKey(
        'core.Client',
        on_delete=models.CASCADE,
        related_name='entries',
        verbose_name='cliente'
    )

    status = models.CharField(
        max_length=15,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDIENTE,
        verbose_name='estado',
        blank=True,
        null=True
    )

    storage = models.ForeignKey(
        'storage.Storage',
        on_delete=models.SET_NULL,
        related_name='entries',
        null=True,
        blank=True,
        verbose_name='almacén'
    )

    condition = models.CharField(
        max_length=15,
        choices=Conditions.choices,
        verbose_name='condición',
        blank=True,
        null=True
    )

    in_type = models.CharField(
        max_length=15,
        choices=InTypes.choices,
        null=True,
        blank=True,
        verbose_name='tipo de entrada'
    )

    code = models.CharField(
        max_length=50,
        unique=True,
        editable=False,
        verbose_name='código'
    )

    entry_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='fecha de entrada'
    )

    total_restibas = models.PositiveBigIntegerField(
        default=0, blank=True, verbose_name='total restibas')

    total_wraps = models.PositiveBigIntegerField(
        default=0, blank=True, verbose_name='total wraps/strech')

    total_added_pallets = models.PositiveBigIntegerField(
        default=0, blank=True, verbose_name='total pallets adicionales')

    class Meta:
        verbose_name = 'orden de entrada'
        verbose_name_plural = 'ordenes de entrada'

    def save(self, *args, **kwargs):
        if not self.code:
            last_entry = Entry.objects.filter(
                client=self.client).order_by('id').last()
            sequence = f'{int(last_entry.code.split(
                '-')[-1]) + 1:08d}' if last_entry else '00000001'
            self.code = f'{self.client.slug.upper()}-ENTRADA-{sequence}'

        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class EntryProduct(BaseModel):
    entry = models.ForeignKey(
        Entry,
        on_delete=models.CASCADE,
        related_name='entry_products',
        verbose_name='entrada'
    )

    product = models.ForeignKey(
        'inventory.Product',
        on_delete=models.CASCADE,
        related_name='entry_products',
        verbose_name='producto'
    )

    quantity = models.PositiveBigIntegerField(
        default=0, verbose_name='cantidad')

    real_quantity = models.PositiveBigIntegerField(
        default=0, verbose_name='cantidad real')

    expiration_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='fecha de expiración'
    )

    batch = models.CharField(max_length=200, blank=True, null=True,
                             verbose_name='lote')

    is_completed = models.BooleanField(
        default=False, verbose_name='completado')

    in_inventory = models.PositiveBigIntegerField(
        default=0, verbose_name='en inventario')

    class Meta:
        verbose_name = 'producto de la orden de entrada'
        verbose_name_plural = 'productos de la orden de entrada'

    def __str__(self):
        name = self.product.name.upper()
        uom = self.product.uom
        exp = self.expiration_date or "Sin fecha"
        batch = self.batch or "Sin lote"
        return f'{name} / EXP: {exp} / LOTE: {batch} / {uom}'

    def update_inventory_status(self):
        """
        Actualiza el estado de inventario del producto en la entrada.

        in_inventory representa la cantidad TOTAL almacenada en bins (sin restar salidas),
        es decir, la suma de:
        - Cantidades actuales en bins activos
        - Cantidades despachadas en salidas procesadas

        Esto permite saber cuánto se ha recibido/almacenado, independientemente de si
        parte de ese inventario ya fue despachado.
        """

        from apps.order.models import ExitProduct

        # Sumar las cantidades actuales en todos los bins activos
        total_in_bins = self.bin_products.filter(is_active=True).aggregate(
            total=Sum('quantity')
        )['total'] or 0

        # Sumar las cantidades que ya fueron despachadas en salidas procesadas
        # Esto nos da la cantidad total que se almacenó originalmente
        total_despachado = ExitProduct.objects.filter(
            bin_product__entry_product=self,
            is_processed=True
        ).aggregate(total=Sum('quantity'))['total'] or 0

        # La cantidad en inventario es la suma de lo que está en bins + lo que se despachó
        # Esto representa la cantidad total que se ha almacenado
        total_almacenado = total_in_bins + total_despachado

        # Actualizar el inventario con la cantidad total almacenada
        self.in_inventory = total_almacenado
        self.save(update_fields=['in_inventory'])

        # Actualizar el estado de completado
        # Un producto está completado cuando la cantidad total almacenada es igual a la cantidad real esperada
        if total_almacenado >= self.real_quantity:
            self.is_completed = True
        else:
            self.is_completed = False

        self.save(update_fields=['is_completed'])

    def save(self, *args, **kwargs):
        if self.pk:
            if self.in_inventory == self.real_quantity:
                self.is_completed = True
            else:
                self.is_completed = False

        super().save(*args, **kwargs)

        # Después de guardar el producto, actualizar el estado de la entrada
        self.update_entry_status()

    def update_entry_status(self):
        """
        Actualiza el estado de la entrada basado en el inventario.
        NOTA: No autocompleta la entrada - eso solo se hace manualmente
        a través del endpoint /api/entries/{id}/complete/
        """
        entry = self.entry

        # Si ya está completada, no hacer nada (solo se completa manualmente)
        if entry.status == OrderStatus.COMPLETADA:
            return

        # Verificar si hay inventario en algún producto
        have_any_inventory = entry.entry_products.filter(
            in_inventory__gt=0).exists()

        # Determinar el nuevo estado (solo entre PENDIENTE y EN_PROGRESO)
        if have_any_inventory:
            new_status = OrderStatus.EN_PROGRESO
        else:
            new_status = OrderStatus.PENDIENTE

        # Solo actualizar si el estado cambió
        if entry.status != new_status:
            entry.status = new_status
            entry.save(update_fields=['status'])
            print(f"DEBUG: Entrada {entry.code} actualizada a {new_status}")


class EntryDetail(BaseModel):
    entry = models.OneToOneField(
        Entry,
        on_delete=models.CASCADE,
        related_name='entry_detail',
        verbose_name='entrada'
    )

    arrival_temperature = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='temperatura de llegada'
    )

    description = models.TextField(
        null=True, blank=True, verbose_name='descripción')

    image = models.ImageField(
        upload_to=wms_entry_detail_image_upload, null=True, blank=True, verbose_name='imagen'
    )

    pdf_document = models.FileField(
        upload_to=wms_entry_pdf_upload,
        null=True,
        blank=True,
        verbose_name='documento PDF'
    )

    class Meta:
        verbose_name = 'detalle de la orden de entrada'
        verbose_name_plural = 'detalle de la orden de entrada'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def __str__(self):
        return self.entry.code


class Exit(BaseModel):
    client = models.ForeignKey(
        'core.Client',
        on_delete=models.CASCADE,
        related_name='exits',
        verbose_name='cliente'
    )

    status = models.CharField(
        max_length=15,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDIENTE,
        verbose_name='estado'
    )

    branch = models.ForeignKey(
        'core.Branch',
        on_delete=models.SET_NULL,
        related_name='exits',
        null=True,
        blank=True,
        verbose_name='sucursal'
    )

    exit_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='fecha de salida'
    )

    out_type = models.CharField(
        max_length=15,
        choices=InTypes.choices,
        null=True,
        blank=True,
        verbose_name='tipo de salida'
    )

    code = models.CharField(max_length=50, unique=True, editable=False,
                            verbose_name='código')

    class Meta:
        verbose_name = 'oden de salida'
        verbose_name_plural = 'ordenes de salida'

    def save(self, *args, **kwargs):
        # Verificar si es una actualización y el estado cambió a COMPLETADA
        is_updating = self.pk is not None
        old_status = None

        if is_updating:
            try:
                old_instance = Exit.objects.get(pk=self.pk)
                old_status = old_instance.status
            except Exit.DoesNotExist:
                pass

        if not self.code:
            try:
                last_entry = Exit.objects.filter(
                    client=self.client).order_by('id').last()
                if last_entry and '-' in last_entry.code:
                    sequence = f'{int(last_entry.code.split("-")[-1]) + 1:08d}'
                else:
                    sequence = '00000001'
            except (ValueError, AttributeError, IndexError):
                sequence = '00000001'

            self.code = f'{self.client.slug.upper()}-SALIDA-{sequence}'

        super().save(*args, **kwargs)

        # Si el estado cambió a COMPLETADA, validar y procesar
        if (is_updating and old_status != OrderStatus.COMPLETADA and
                self.status == OrderStatus.COMPLETADA):
            # Validar que todos los exit_products estén procesados
            all_exit_products = []
            for group in self.exit_product_groups.all():
                all_exit_products.extend(group.exit_products.all())

            if all_exit_products:
                all_processed = all(
                    ep.is_processed for ep in all_exit_products)
                if not all_processed:
                    raise ValidationError(
                        'No se puede completar la orden. Todos los productos deben estar procesados (is_processed=True).'
                    )

            # Si pasa la validación, actualizar bins a DESPACHADO
            self.update_bins_to_dispatched_if_all_processed()

    @transaction.atomic
    def update_bins_to_dispatched_if_all_processed(self):
        """
        Cambia el estado de todos los bins relacionados a DESPACHADO si todos 
        los exit_products de la orden están procesados (is_processed=True).
        Operación atómica para garantizar consistencia.
        """
        from apps.inventory.models import Bin

        # Obtener todos los exit_products de esta orden
        all_exit_products = []
        for group in self.exit_product_groups.all():
            all_exit_products.extend(group.exit_products.all())

        if not all_exit_products:
            return

        # Verificar si todos los exit_products están procesados
        all_processed = all(ep.is_processed for ep in all_exit_products)

        if all_processed:
            # Obtener bins que deben marcarse como DESPACHADO:
            # IMPORTANTE: Un bin solo puede marcarse como DESPACHADO si su cantidad es 0
            # No importa si es "completo" o "picking", si tiene cantidad > 0, NO se marca como DESPACHADO
            bin_ids_to_dispatch = set()
            bin_ids_excluded = set()

            for exit_product in all_exit_products:
                bin_product = exit_product.bin_product
                bin_product.refresh_from_db()  # Obtener cantidad actualizada

                # Solo marcar como DESPACHADO si la cantidad es 0
                if bin_product.quantity == 0:
                    bin_ids_to_dispatch.add(bin_product.bin.id)
                else:
                    bin_ids_excluded.add(bin_product.bin.id)

            # Actualizar el estado de los bins seleccionados a DESPACHADO
            # Usar save() en lugar de update() para que se disparen los signals
            bins_updated = 0
            if bin_ids_to_dispatch:
                # Usar la fecha de salida de la orden, o la fecha actual si no hay
                dispatch_date = self.exit_date if self.exit_date else timezone.now().date()
                # Convertir a datetime con zona horaria de Panamá
                from datetime import datetime
                from zoneinfo import ZoneInfo
                panama_tz = ZoneInfo('America/Panama')
                dispatch_datetime = datetime.combine(
                    dispatch_date, datetime.min.time(), tzinfo=panama_tz
                )

                bins_to_update = Bin.objects.filter(
                    id__in=bin_ids_to_dispatch
                )
                for bin in bins_to_update:
                    bin.status = BinStatus.DESPACHADO
                    bin.dispatched_date = dispatch_datetime
                    bin.save(
                        update_fields=['status', 'dispatched_date', 'updated_at'])
                    bins_updated += 1

            print(
                f"DEBUG: Orden {self.code} completada. {bins_updated} bins actualizados a DESPACHADO, {len(bin_ids_excluded)} bins con cantidad > 0 excluidos")
        else:
            print(
                f"DEBUG: Orden {self.code} completada pero no todos los exit_products están procesados")

    def __str__(self):
        return self.code


class ExitProductGroup(BaseModel):
    """
    Tabla intermedia para agrupar ExitProducts por producto.
    Permite consolidar productos en una orden de salida.
    """
    exit = models.ForeignKey(
        Exit,
        on_delete=models.CASCADE,
        related_name='exit_product_groups',
        verbose_name='orden de salida'
    )

    product = models.ForeignKey(
        'inventory.Product',
        on_delete=models.CASCADE,
        related_name='exit_product_groups',
        verbose_name='producto'
    )

    quantity = models.PositiveBigIntegerField(
        default=0,
        verbose_name='cantidad total'
    )

    bin_or_batch = models.TextField(
        blank=True,
        null=True,
        verbose_name='lotes y/o bins (separados por comas)'
    )

    class Meta:
        verbose_name = 'grupo de productos de salida'
        verbose_name_plural = 'grupos de productos de salida'
        # Un producto solo puede aparecer una vez en una orden de salida
        unique_together = ('exit', 'product')

    def save(self, *args, **kwargs):
        creating = not self.pk
        super().save(*args, **kwargs)

        # Si es una creación nueva, generar los ExitProducts automáticamente
        if creating and self.quantity > 0:
            self.generate_exit_products()

    @transaction.atomic
    def generate_exit_products(self):
        """
        Genera los ExitProducts necesarios para esta agrupación,
        tomando los bins según los criterios especificados o FEFO (First Expire, First Out).

        bin_or_batch puede contener:
        - Múltiples valores separados por comas: "abc123,BIN-000000001,def456,BIN-000000002"
        - Solo lotes: "abc123,def456,ghi789"
        - Solo bin_codes: "BIN-000000001,BIN-000000002,BIN-000000003"
        - Valor único: "abc123" o "BIN-000000001"

        Los bins se procesan en el orden especificado como prioridad.
        Si no hay suficiente cantidad en los criterios especificados, se complementa con FEFO.

        IMPORTANTE: Calcula disponibilidad real restando reservas existentes (ExitProducts no procesados)
        y usa SELECT FOR UPDATE para evitar race conditions.
        """
        from apps.inventory.models import BinProduct
        from django.db.models import Sum

        print(
            f"DEBUG: Generando exit_products para grupo {self.id} - Producto: {self.product.name} - Cantidad: {self.quantity} - Criterios: {self.bin_or_batch or 'No especificado (FEFO)'}")

        # Si ya tiene exit_products, limpiamos para regenerar
        if self.exit_products.exists():
            print(f"DEBUG: El grupo ya tiene exit_products, limpiando...")
            self.exit_products.all().delete()

        # Obtener IDs de bins que ya están asociados como bins COMPLETOS a otras salidas activas
        bins_in_other_exits = ExitProduct.objects.filter(
            # Solo bins completos (el bin entero está reservado)
            is_picking=False,
            exit_product_group__exit__status__in=[
                OrderStatus.PENDIENTE, OrderStatus.EN_PROGRESO]
        ).exclude(
            exit_product_group__exit_id=self.exit.id  # Excluir la salida actual
        ).values_list('bin_product__bin_id', flat=True)

        # Calcular reservas existentes por BinProduct (query separada para evitar problemas con select_for_update)
        # Esto incluye ExitProducts con is_picking=True (reservas parciales) que NO están procesados
        reservations_dict = {}
        reservations_qs = ExitProduct.objects.filter(
            bin_product__entry_product__product=self.product,
            bin_product__entry_product__entry__client=self.exit.client,
            is_processed=False,  # Solo no procesados
            exit_product_group__exit__status__in=[
                OrderStatus.PENDIENTE, OrderStatus.EN_PROGRESO]
        ).exclude(
            exit_product_group__exit_id=self.exit.id  # Excluir la salida actual
        ).values('bin_product_id').annotate(
            total_reserved=Sum('quantity')
        )
        for res in reservations_qs:
            reservations_dict[res['bin_product_id']
                              ] = res['total_reserved']

        # Obtener los bins con este producto (solo bins activos y disponibles)
        # NOTA: La validación de reservas con reservations_dict previene sobre-reservas
        bins_query = BinProduct.objects.filter(
            entry_product__product=self.product,
            entry_product__entry__client=self.exit.client,  # Mismo cliente
            bin__status=BinStatus.ALMACENADO,  # Solo bins almacenados
            bin__is_active=True,  # Solo bins activos (Bin.is_active)
            quantity__gt=0
        ).exclude(
            # Excluir bins ya asociados como completos a otras salidas
            bin_id__in=bins_in_other_exits
        ).select_related('bin__position', 'entry_product')

        # Verificar si se especificó algún bin_code (si es así, no priorizar posiciones picking)
        has_bin_code = False
        if self.bin_or_batch:
            bin_or_batch_list = [item.strip()
                                 for item in self.bin_or_batch.split(',') if item.strip()]
            has_bin_code = any(item.startswith("BIN-")
                               for item in bin_or_batch_list)

        # Si se especifica lotes o bin_codes, filtrar primero por esos criterios
        if self.bin_or_batch:
            # Dividir por comas y limpiar espacios
            bin_or_batch_list = [item.strip()
                                 for item in self.bin_or_batch.split(',') if item.strip()]
            print(
                f"DEBUG: Procesando múltiples criterios: {bin_or_batch_list}")

            # Separar bins y lotes
            bin_codes = [
                item for item in bin_or_batch_list if item.startswith("BIN-")]
            batches = [
                item for item in bin_or_batch_list if not item.startswith("BIN-")]

            print(f"DEBUG: Bin codes: {bin_codes}")
            print(f"DEBUG: Lotes: {batches}")

            # Crear lista ordenada de bins según prioridad especificada
            prioritized_bins = []
            used_bin_ids = set()

            # Procesar cada criterio en el orden especificado
            for criterion in bin_or_batch_list:
                if criterion.startswith("BIN-"):
                    # Es un bin_code
                    specific_bins = bins_query.filter(bin__code=criterion)
                    for bin_product in specific_bins:
                        if bin_product.bin.id not in used_bin_ids:
                            prioritized_bins.append(bin_product)
                            used_bin_ids.add(bin_product.bin.id)
                else:
                    # Es un lote
                    # Si NO se especificó bin_code, priorizar posiciones picking dentro del mismo lote
                    if not has_bin_code:
                        # Primero: bins del lote en posiciones picking
                        picking_batch_bins = bins_query.filter(
                            entry_product__batch=criterion,
                            bin__position__is_picking=True
                        ).order_by(
                            F('entry_product__expiration_date').asc(
                                nulls_last=True),
                            'id'
                        )
                        for bin_product in picking_batch_bins:
                            if bin_product.bin.id not in used_bin_ids:
                                prioritized_bins.append(bin_product)
                                used_bin_ids.add(bin_product.bin.id)

                        # Segundo: otros bins del lote (posiciones normales)
                        other_batch_bins = bins_query.filter(
                            entry_product__batch=criterion
                        ).filter(
                            models.Q(bin__position__isnull=True) | models.Q(
                                bin__position__is_picking=False)
                        ).order_by(
                            F('entry_product__expiration_date').asc(
                                nulls_last=True),
                            'id'
                        )
                        for bin_product in other_batch_bins:
                            if bin_product.bin.id not in used_bin_ids:
                                prioritized_bins.append(bin_product)
                                used_bin_ids.add(bin_product.bin.id)
                    else:
                        # Si se especificó bin_code, usar orden normal sin priorizar posiciones picking
                        specific_bins = bins_query.filter(entry_product__batch=criterion).order_by(
                            F('entry_product__expiration_date').asc(
                                nulls_last=True),
                            'id'
                        )
                        for bin_product in specific_bins:
                            if bin_product.bin.id not in used_bin_ids:
                                prioritized_bins.append(bin_product)
                                used_bin_ids.add(bin_product.bin.id)

            # Calcular cantidad DISPONIBLE REAL en bins priorizados (física - reservas)
            prioritized_quantity = sum(
                max(0, bin_product.quantity -
                    reservations_dict.get(bin_product.id, 0))
                for bin_product in prioritized_bins)
            print(
                f"DEBUG: Cantidad disponible REAL en criterios priorizados: {prioritized_quantity}")

            if prioritized_quantity >= self.quantity:
                # Suficiente en los criterios especificados
                bins_with_product = prioritized_bins
                print(f"DEBUG: Suficiente cantidad en criterios priorizados")
            else:
                # Combinar bins priorizados + otros bins (FEFO)
                # Excluir los bins ya utilizados (por bin_id, NO por BinProduct id)
                remaining_bins_query = bins_query.exclude(
                    bin_id__in=used_bin_ids
                )

                # Si NO se especificó bin_code, priorizar posiciones picking en los bins restantes
                if not has_bin_code:
                    # Primero: bins restantes en posiciones picking
                    picking_remaining = remaining_bins_query.filter(
                        bin__position__is_picking=True
                    ).order_by(
                        F('entry_product__expiration_date').asc(nulls_last=True),
                        'id'
                    )

                    # Segundo: otros bins restantes
                    other_remaining = remaining_bins_query.filter(
                        models.Q(bin__position__isnull=True) | models.Q(
                            bin__position__is_picking=False)
                    ).order_by(
                        F('entry_product__expiration_date').asc(nulls_last=True),
                        'id'
                    )

                    # Combinar: priorizados + picking restantes + otros restantes
                    bins_with_product = prioritized_bins + \
                        list(picking_remaining) + list(other_remaining)
                else:
                    # Si se especificó bin_code, usar FEFO normal sin priorizar posiciones picking
                    other_bins = remaining_bins_query.order_by(
                        F('entry_product__expiration_date').asc(nulls_last=True),
                        'id'
                    )
                bins_with_product = prioritized_bins + list(other_bins)
                other_quantity = other_bins.aggregate(
                    total=Sum('quantity'))['total'] or 0
                print(
                    f"DEBUG: Combinando criterios priorizados + FEFO. Priorizados: {prioritized_quantity}, Total disponible: {prioritized_quantity + other_quantity}")
        else:
            # Sin lote especificado
            # Si NO se especificó bin_code, priorizar bins en posiciones picking (nivel del suelo)
            if not has_bin_code:
                # Primero: bins en posiciones picking (is_picking=True)
                picking_position_bins = bins_query.filter(
                    bin__position__is_picking=True
                ).order_by(
                    F('entry_product__expiration_date').asc(nulls_last=True),
                    'id'
                )

                # Segundo: otros bins (posiciones normales o sin posición)
                other_bins = bins_query.filter(
                    models.Q(bin__position__isnull=True) | models.Q(
                        bin__position__is_picking=False)
                ).order_by(
                    F('entry_product__expiration_date').asc(nulls_last=True),
                    'id'
                )

                # Combinar: primero picking positions, luego otros
                bins_with_product = list(
                    picking_position_bins) + list(other_bins)
                picking_count = picking_position_bins.count()
                print(
                    f"DEBUG: Sin lote especificado, priorizando posiciones picking. Bins picking: {picking_count}, Otros bins: {other_bins.count()}")
            else:
                # Si se especificó bin_code, usar FEFO normal sin priorizar posiciones picking
                bins_with_product = bins_query.order_by(
                    F('entry_product__expiration_date').asc(nulls_last=True),
                    'id'
                )
                print(
                    f"DEBUG: Bin_code especificado, usando FEFO normal sin priorizar posiciones picking")

        print(
            f"DEBUG: Encontrados {len(bins_with_product) if isinstance(bins_with_product, list) else bins_with_product.count()} bins con el producto")

        # Verificar que hay suficiente inventario REAL (física - reservas existentes)
        if isinstance(bins_with_product, list):
            total_available = sum(
                max(0, bin_product.quantity -
                    reservations_dict.get(bin_product.id, 0))
                for bin_product in bins_with_product)
            # Debug: mostrar reservas por bin
            for bp in bins_with_product:
                reserved = reservations_dict.get(bp.id, 0)
                if reserved > 0:
                    print(
                        f"DEBUG: Bin {bp.bin.code} - Física: {bp.quantity}, Reservada: {reserved}, Disponible: {bp.quantity - reserved}")
        else:
            # Para querysets, calcular disponible real
            bins_list = list(bins_with_product)
            total_available = sum(
                max(0, bp.quantity - reservations_dict.get(bp.id, 0))
                for bp in bins_list)
            bins_with_product = bins_list

        print(
            f"DEBUG: Inventario disponible REAL: {total_available}, Solicitado: {self.quantity}")

        if total_available < self.quantity:
            error_msg = f"Cantidad insuficiente. Solicitado: {self.quantity}, Disponible REAL: {total_available} (ya restando reservas existentes)"
            print(f"ERROR: {error_msg}")
            raise ValidationError(error_msg)

        # Crear los ExitProducts
        quantity_remaining = self.quantity
        exit_products_created = 0
        # Tracking de cantidades ya asignadas en esta generación para evitar duplicados
        assigned_in_this_run = {}

        for bin_product in bins_with_product:
            if quantity_remaining <= 0:
                break

            # Calcular disponibilidad REAL de este bin (física - reservas existentes - ya asignado en esta corrida)
            reserved = reservations_dict.get(bin_product.id, 0)
            already_assigned = assigned_in_this_run.get(bin_product.id, 0)
            available_in_bin = max(0, bin_product.quantity - reserved - already_assigned)

            # Si no hay disponibilidad real en este bin, saltar al siguiente
            if available_in_bin <= 0:
                print(
                    f"DEBUG: Bin {bin_product.bin.code} sin disponibilidad real (Física: {bin_product.quantity}, Reservada: {reserved}, Ya asignado: {already_assigned})")
                continue

            # Determinar cuánto extraer de este bin (de la disponibilidad real)
            quantity_to_extract = min(available_in_bin, quantity_remaining)
            # is_picking = True si no extraemos toda la cantidad FÍSICA del bin
            # Considerar lo ya asignado en esta corrida para determinar picking
            total_from_this_bin = already_assigned + quantity_to_extract
            is_picking = total_from_this_bin < bin_product.quantity

            print(
                f"DEBUG: Bin {bin_product.bin.code} - Lote: {bin_product.entry_product.batch or 'Sin lote'} - Física: {bin_product.quantity} - Reservada: {reserved} - Disponible real: {available_in_bin} - A extraer: {quantity_to_extract} - Is picking: {is_picking}")

            # Crear ExitProduct
            exit_product = ExitProduct.objects.create(
                exit_product_group=self,
                bin_product=bin_product,
                quantity=quantity_to_extract,
                is_picking=is_picking
            )
            exit_products_created += 1

            print(
                f"DEBUG: Creado exit_product {exit_product.id} con cantidad {quantity_to_extract}")

            # Actualizar tracking de asignación en esta corrida
            assigned_in_this_run[bin_product.id] = already_assigned + quantity_to_extract

            # Si la orden ya está en COMPLETADA, actualizamos inmediatamente la cantidad en el bin
            if self.exit.status == OrderStatus.COMPLETADA:
                print(
                    f"DEBUG: La orden está COMPLETADA, restando cantidad del bin inmediatamente")
                bin_product.quantity -= quantity_to_extract
                bin_product.save(update_fields=['quantity'])

            # Actualizar cantidad restante
            quantity_remaining -= quantity_to_extract

        print(
            f"DEBUG: Generados {exit_products_created} exit_products. Cantidad restante: {quantity_remaining}")

        # Verificar si se generaron todos los exit_products necesarios
        if quantity_remaining > 0:
            print(
                f"ERROR: No se pudo asignar toda la cantidad. Faltan {quantity_remaining} unidades")

        # Verificar si el grupo tiene los exit_products esperados
        actual_count = self.exit_products.count()
        if actual_count != exit_products_created:
            print(
                f"ERROR: Discrepancia en exit_products creados. Esperados: {exit_products_created}, Actuales: {actual_count}")

        return exit_products_created

    def update_quantity(self):
        """
        Actualiza la cantidad total basada en la suma de los ExitProducts asociados.
        """
        total = self.exit_products.aggregate(
            total=Sum('quantity'))['total'] or 0
        self.quantity = total
        self.save(update_fields=['quantity'])

    def __str__(self):
        return f"{self.product.name} ({self.quantity})"


class ExitProduct(BaseModel):
    exit_product_group = models.ForeignKey(
        ExitProductGroup,
        on_delete=models.CASCADE,
        related_name='exit_products',
        verbose_name='grupo de productos'
    )

    bin_product = models.ForeignKey(
        'inventory.BinProduct',
        on_delete=models.CASCADE,
        related_name='exit_products',
        verbose_name='producto del bin de entrada'
    )

    quantity = models.PositiveBigIntegerField(
        default=0, verbose_name='cantidad')

    is_picking = models.BooleanField(
        default=False, verbose_name='para picking')

    was_qr_validated = models.BooleanField(
        default=False, verbose_name='fue validado con QR?')

    is_processed = models.BooleanField(
        default=False, verbose_name='procesado')

    class Meta:
        verbose_name = 'detalle de producto de salida'
        verbose_name_plural = 'detalles de productos de salida'

    def save(self, *args, **kwargs):
        # Verificar si is_processed cambió a True
        is_processed_changed_to_true = False
        if self.pk:
            try:
                old_instance = ExitProduct.objects.get(pk=self.pk)
                is_processed_changed_to_true = not old_instance.is_processed and self.is_processed
            except ExitProduct.DoesNotExist:
                pass
        else:
            # Es un objeto nuevo
            is_processed_changed_to_true = self.is_processed

        super().save(*args, **kwargs)

        # Si is_processed cambió a True, verificar si necesitamos actualizar el Exit status
        if is_processed_changed_to_true and self.exit_product_group and self.exit_product_group.exit:
            exit_obj = self.exit_product_group.exit
            if exit_obj.status != OrderStatus.EN_PROGRESO:
                exit_obj.status = OrderStatus.EN_PROGRESO
                exit_obj.save(update_fields=['status'])

        # Actualizar la cantidad total en el grupo
        if self.exit_product_group:
            self.exit_product_group.update_quantity()

    def delete(self, *args, **kwargs):
        group = self.exit_product_group
        super().delete(*args, **kwargs)

        # Actualizar la cantidad total en el grupo
        if group:
            group.update_quantity()

    def __str__(self):
        return f"{self.bin_product} ({self.quantity})"


class ExitDetail(BaseModel):
    exit = models.OneToOneField(
        Exit,
        on_delete=models.CASCADE,
        related_name='exit_detail',
        verbose_name='salida'
    )

    temperature = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='temperatura'
    )

    picking_pallets = models.PositiveIntegerField(
        default=0,
        verbose_name='pallets de picking',
        help_text='Número de pallets usados en picking (se suma al total de pallets/bins)'
    )

    description = models.TextField(
        null=True, blank=True, verbose_name='descripción')

    image = models.ImageField(
        upload_to=wms_exit_detail_image_upload, null=True, blank=True, verbose_name='imagen'
    )

    pdf_document = models.FileField(
        upload_to=wms_exit_pdf_upload,
        null=True,
        blank=True,
        verbose_name='documento PDF'
    )

    class Meta:
        verbose_name = 'detalle de la orden de salida'
        verbose_name_plural = 'detalle de la orden de salida'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def __str__(self):
        return self.exit.code


class ExitMovementType(models.TextChoices):
    """Tipos de movimiento para órdenes de salida"""
    CREATED = 'created', 'Orden Creada'
    STATUS_CHANGED = 'status_changed', 'Cambio de Estado'
    PRODUCT_ADDED = 'product_added', 'Producto Agregado'
    PRODUCT_REMOVED = 'product_removed', 'Producto Eliminado'
    BIN_ASSIGNED = 'bin_assigned', 'Bin Asignado'
    BIN_REMOVED = 'bin_removed', 'Bin Eliminado'
    BIN_PROCESSED = 'bin_processed', 'Bin Procesado'
    BIN_REVERSED = 'bin_reversed', 'Bin Reversado'
    BIN_REPLACED = 'bin_replaced', 'Bin Reemplazado'
    BIN_REASSIGNED = 'bin_reassigned', 'Bin Reasignado'
    IMPORT_CREATED = 'import_created', 'Creada por Importación'
    QUANTITY_CHANGED = 'quantity_changed', 'Cantidad Modificada'
    CONFLICT_DETECTED = 'conflict_detected', 'Conflicto Detectado'
    CONFLICT_RESOLVED = 'conflict_resolved', 'Conflicto Resuelto'


class ExitMovement(BaseModel):
    """
    Modelo para trackear todos los movimientos y eventos de una orden de salida.
    Registra: creación, cambios de estado, asignación/remoción de bins, 
    procesamiento, reversiones, reemplazos, etc.
    """
    exit = models.ForeignKey(
        Exit,
        on_delete=models.CASCADE,
        related_name='movements',
        verbose_name='orden de salida'
    )

    movement_type = models.CharField(
        max_length=25,
        choices=ExitMovementType.choices,
        verbose_name='tipo de movimiento'
    )

    # Para cambios de estado
    old_status = models.CharField(
        max_length=15,
        choices=OrderStatus.choices,
        null=True,
        blank=True,
        verbose_name='estado anterior'
    )

    new_status = models.CharField(
        max_length=15,
        choices=OrderStatus.choices,
        null=True,
        blank=True,
        verbose_name='estado nuevo'
    )

    # Información del producto afectado
    product = models.ForeignKey(
        'inventory.Product',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='exit_movements',
        verbose_name='producto'
    )

    product_sku = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name='SKU del producto'
    )

    product_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name='nombre del producto'
    )

    # Información del bin afectado
    bin = models.ForeignKey(
        'inventory.Bin',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='exit_movements',
        verbose_name='bin'
    )

    bin_code = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name='código del bin'
    )

    # Para reemplazos de bin
    old_bin_code = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name='bin anterior'
    )

    new_bin_code = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name='bin nuevo'
    )

    # Cantidades
    quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='cantidad'
    )

    old_quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='cantidad anterior'
    )

    new_quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='cantidad nueva'
    )

    # Si fue picking o completo
    is_picking = models.BooleanField(
        null=True,
        blank=True,
        verbose_name='es picking?'
    )

    # Notas adicionales con detalles del movimiento
    notes = models.TextField(
        blank=True,
        null=True,
        verbose_name='notas adicionales'
    )

    # Metadata adicional en JSON para flexibilidad
    metadata = models.JSONField(
        null=True,
        blank=True,
        verbose_name='metadata adicional'
    )

    class Meta:
        verbose_name = 'movimiento de salida'
        verbose_name_plural = 'movimientos de salidas'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['exit', '-created_at']),
            models.Index(fields=['movement_type']),
        ]

    def __str__(self):
        return f"{self.exit.code} - {self.get_movement_type_display()} - {self.created_at}"

    @classmethod
    def log_movement(cls, exit_obj, movement_type, **kwargs):
        """
        Método de conveniencia para registrar un movimiento.
        """
        return cls.objects.create(
            exit=exit_obj,
            movement_type=movement_type,
            **kwargs
        )
