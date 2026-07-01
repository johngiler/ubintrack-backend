import os

from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django_currentuser.db.models import CurrentUserField
from django.utils import timezone

from apps.core import choices

from apps.common.utils.media_layout import wms_bin_tag_upload

from . import services


class BaseModel(models.Model):
    is_active = models.BooleanField(default=True, verbose_name='activo')
    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name='fecha de creación')
    updated_at = models.DateTimeField(
        auto_now=True, verbose_name='fecha de actualización')
    created_by = CurrentUserField(
        related_name='%(app_label)s_%(class)s_creates', verbose_name='creado por')

    updated_by = CurrentUserField(
        related_name='%(app_label)s_%(class)s_updates',
        on_update=True,
        verbose_name='actualizado por'
    )

    class Meta:
        abstract = True


class Category(models.Model):
    name = models.CharField(max_length=200, verbose_name='nombre de categoría')
    category_code = models.CharField(
        max_length=50, unique=True, verbose_name='código')
    coexistence = models.ManyToManyField(
        'self', blank=True, symmetrical=True, verbose_name='coexistencia')

    class Meta:
        verbose_name = 'categoría'
        verbose_name_plural = 'categorías'

    def __str__(self):
        return self.name


class Product(BaseModel):
    client = models.ForeignKey(
        'core.Client',
        on_delete=models.CASCADE,
        related_name='products',
        verbose_name='cliente'
    )

    name = models.CharField(max_length=200, verbose_name='nombre de producto')
    code = models.CharField(max_length=50, unique=True,
                            editable=False, verbose_name='código')
    client_sku = models.CharField(
        max_length=100, null=True, blank=True, verbose_name='sku del cliente')
    rotation_type = models.CharField(
        max_length=4,
        choices=choices.RotationTypes.choices, verbose_name='tipo de rotación')
    uom = models.CharField(max_length=10, choices=choices.UoM.choices,
                           verbose_name='presentación')
    kg_uom = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name='kg por presentación')
    units_in_uom = models.PositiveBigIntegerField(
        default=0, verbose_name='unidades dentro de presentación')
    qty_uom_pallet = models.PositiveBigIntegerField(
        default=0, verbose_name='cantidad de uom en pallet')

    condition = models.CharField(
        max_length=15,
        choices=choices.Conditions.choices,
        null=True,
        blank=True,
        verbose_name='condición'
    )

    storage_temperature = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='temperatura de almacenamiento'
    )

    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        related_name='products',
        null=True,
        blank=True, verbose_name='categoría'
    )

    supplier = models.CharField(max_length=100, blank=True,
                                null=True, verbose_name='proveedor')
    comment = models.TextField(blank=True, verbose_name='comentario')
    is_picking = models.BooleanField(default=False, verbose_name='es picking?')

    class Meta:
        unique_together = ('name', 'client')
        verbose_name = 'producto'
        verbose_name_plural = 'productos'

    def save(self, *args, **kwargs):
        if not self.code:
            last_product = Product.objects.filter(
                client=self.client).order_by('id').last()
            sequence = f"{int(last_product.code.split(
                '-')[-1]) + 1:06d}" if last_product else "000001"
            self.code = f"{self.client.slug.upper()}-PRODUCTO-{sequence}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Bin(BaseModel):
    code = models.CharField(max_length=50, unique=True,
                            editable=False, verbose_name='código de bin')

    client = models.ForeignKey(
        'core.Client',
        on_delete=models.CASCADE,
        editable=False,
        null=True,
        verbose_name='cliente'
    )

    tag_image = models.ImageField(
        upload_to=wms_bin_tag_upload, blank=True, null=True, verbose_name='imagen de tag')

    zpl_code = models.TextField(
        blank=True, null=True, verbose_name='código ZPL')

    entry = models.ForeignKey(
        'order.Entry',
        on_delete=models.CASCADE,
        related_name='bins',
        verbose_name='orden de entrada'
    )

    status = models.CharField(
        max_length=15,
        choices=choices.BinStatus.choices,
        default=choices.BinStatus.EN_RECIBO,
        verbose_name='estado'
    )

    position = models.ForeignKey(
        'storage.Position',
        on_delete=models.SET_NULL,
        related_name='bins',
        null=True,
        blank=True, verbose_name='posición'
    )

    dispatched_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='fecha de despacho'
    )

    was_qr_validated = models.BooleanField(
        default=False,
        verbose_name='fue validado con QR?'
    )

    is_processed = models.BooleanField(
        default=False, verbose_name='procesado')

    class Meta:
        verbose_name = 'bin'
        verbose_name_plural = 'bins'

    def save(self, *args, **kwargs):
        is_new = self._state.adding

        old_status = None
        if not is_new:
            try:
                old_instance = Bin.objects.get(pk=self.pk)
                old_status = old_instance.status
            except Bin.DoesNotExist:
                pass

        if not self.client and self.entry:
            self.client = self.entry.client

        if not self.code:
            self.code = self.generate_global_bin_code()

        if self.status == choices.BinStatus.DESPACHADO and not self.dispatched_date:
            self.dispatched_date = timezone.now()

        if (old_status != self.status and
            self.status in [choices.BinStatus.EN_DESPACHO, choices.BinStatus.DESPACHADO] and
                self.position):
            from apps.storage.models import Position
            Position.objects.filter(pk=self.position.pk).update(
                status=choices.PositionStatus.LIBRE,
                updated_at=timezone.now()
            )

        super().save(*args, **kwargs)

        if not is_new and self.position:
            try:
                if self.bin_products.exists():
                    tag_path = services.generate_bin_tag(self)
                    self.tag_image.name = os.path.relpath(
                        tag_path, settings.MEDIA_ROOT)
                    self.zpl_code = services.generate_bin_tag_zpl(self)
                    models.Model.save(self, update_fields=[
                                      'tag_image', 'zpl_code'])
            except Exception as e:
                print(f"Error generando tag o ZPL: {str(e)}")

    @staticmethod
    def generate_global_bin_code():
        last_bin = Bin.objects.order_by('id').last()
        last_number = int(last_bin.code.split('-')[-1]) if last_bin and last_bin.code else 0
        return f"BIN-{last_number + 1:010d}"

    def generate_tag(self):
        if self.position and self.bin_products.exists():
            tag_path = services.generate_bin_tag(self)
            self.tag_image.name = os.path.relpath(
                tag_path, settings.MEDIA_ROOT)
            self.zpl_code = services.generate_bin_tag_zpl(self)
            self.save(update_fields=['tag_image', 'zpl_code'])
            return True
        return False

    def __str__(self):
        return self.code


class BinProduct(BaseModel):
    bin = models.ForeignKey(
        Bin,
        on_delete=models.CASCADE,
        related_name='bin_products',
        verbose_name='bin'
    )

    entry_product = models.ForeignKey(
        'order.EntryProduct',
        on_delete=models.CASCADE,
        related_name='bin_products',
        null=True,
        blank=True, verbose_name='producto de orden de entrada'
    )
    batch = models.CharField(max_length=50, blank=True,
                             null=True, verbose_name='lote')
    expiration_date = models.DateField(
        blank=True, null=True, verbose_name='fecha de expiración')
    quantity = models.PositiveIntegerField(
        blank=True, null=True, default=0, verbose_name='cantidad')

    class Meta:
        verbose_name = 'producto de bin'
        verbose_name_plural = 'productos de bin'

    def clean(self):
        if self.entry_product:
            if self.pk:
                total_in_bins = BinProduct.objects.filter(
                    entry_product=self.entry_product,
                    is_active=True
                ).exclude(pk=self.pk).aggregate(total=Sum('quantity'))['total'] or 0
            else:
                total_in_bins = BinProduct.objects.filter(
                    entry_product=self.entry_product,
                    is_active=True
                ).aggregate(total=Sum('quantity'))['total'] or 0

            total_in_bins_after_save = total_in_bins + self.quantity

            from apps.order.models import ExitProduct
            total_despachado = ExitProduct.objects.filter(
                bin_product__entry_product=self.entry_product,
                is_processed=True
            ).aggregate(total=Sum('quantity'))['total'] or 0

            total_almacenado_after_save = total_in_bins_after_save + total_despachado

            if total_almacenado_after_save > self.entry_product.real_quantity:
                entry = self.entry_product.entry
                raise ValidationError({
                    'quantity': (
                        f"La cantidad total almacenada ({total_almacenado_after_save}) excede la cantidad "
                        f"disponible ({self.entry_product.real_quantity}) en el producto de entrada. "
                        f"(En bins: {total_in_bins_after_save}, Despachado: {total_despachado})"
                    ),
                    'entry_id': entry.id,
                    'entry_code': entry.code,
                    'inconsistency_error': True
                })

    def save(self, *args, **kwargs):
        self.clean()

        if self.pk:
            try:
                previous = BinProduct.objects.get(pk=self.pk)
                quantity_changed = previous.quantity != self.quantity
            except BinProduct.DoesNotExist:
                quantity_changed = True
        else:
            quantity_changed = True

        super().save(*args, **kwargs)

        if quantity_changed and self.bin:
            self.bin.generate_tag()

    def __str__(self):
        name = self.entry_product.product.name.upper()
        uom = self.entry_product.product.uom
        exp = self.entry_product.expiration_date or "Sin fecha"
        batch = self.entry_product.batch or "Sin lote"
        return f"{name} / EXP: {exp} / LOTE: {batch} / {uom}"


class BinMovementType(models.TextChoices):
    CREATED = 'created', 'Creado'
    STATUS_CHANGED = 'status_changed', 'Cambio de Estado'
    EXIT_PROCESSED = 'exit_processed', 'Salida Procesada'
    EXIT_REVERSED = 'exit_reversed', 'Salida Reversada'


class BinMovement(BaseModel):
    """Trackea creación, cambios de estado y procesamiento de salidas de un bin."""

    bin = models.ForeignKey(
        Bin,
        on_delete=models.CASCADE,
        related_name='movements',
        verbose_name='bin'
    )

    movement_type = models.CharField(
        max_length=20,
        choices=BinMovementType.choices,
        verbose_name='tipo de movimiento'
    )

    old_status = models.CharField(
        max_length=15,
        choices=choices.BinStatus.choices,
        null=True,
        blank=True,
        verbose_name='estado anterior'
    )

    new_status = models.CharField(
        max_length=15,
        choices=choices.BinStatus.choices,
        null=True,
        blank=True,
        verbose_name='estado nuevo'
    )

    exit = models.ForeignKey(
        'order.Exit',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bin_movements',
        verbose_name='orden de salida'
    )

    exit_code = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name='código de salida'
    )

    is_picking = models.BooleanField(
        null=True,
        blank=True,
        verbose_name='es picking?'
    )

    quantity_subtracted = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='cantidad restada'
    )

    quantity_remaining = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='cantidad restante'
    )

    initial_quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='cantidad inicial'
    )

    position = models.ForeignKey(
        'storage.Position',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bin_movements',
        verbose_name='posición'
    )

    notes = models.TextField(
        blank=True,
        null=True,
        verbose_name='notas adicionales'
    )

    class Meta:
        verbose_name = 'movimiento de bin'
        verbose_name_plural = 'movimientos de bins'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.bin.code} - {self.get_movement_type_display()} - {self.created_at}"
