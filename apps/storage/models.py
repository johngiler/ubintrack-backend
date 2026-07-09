from django.db import models
from django.core.files import File

from apps.common.models import BaseModel
from apps.core import choices

from apps.common.utils.media_layout import wms_position_qr_upload

from . import services


class Storage(BaseModel):
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="storages",
        verbose_name="workspace",
    )
    name = models.CharField(max_length=200, verbose_name="nombre de la bodega")

    class Meta:
        verbose_name = 'bodega'
        verbose_name_plural = 'bodegas'

    def __str__(self):
        return self.name


class Cava(BaseModel):
    storage = models.ForeignKey(
        Storage,
        on_delete=models.CASCADE,
        null=True,
        related_name='cavas',
        verbose_name='bodega'
    )

    name = models.CharField(max_length=50, verbose_name='nombre de la cava')
    condition = models.CharField(
        max_length=15, choices=choices.Conditions.choices, verbose_name='condición')
    current_temp = models.IntegerField(
        default=0, blank=True, null=True, verbose_name='temperatura actual')
    temp_min = models.IntegerField(
        default=0, blank=True, null=True, verbose_name='temperatura mínima')
    temp_max = models.IntegerField(
        default=0, blank=True, null=True, verbose_name='temperatura máxima')
    total_levels = models.IntegerField(
        default=0, editable=False, verbose_name='número total de niveles')
    positions_has_been_generated = models.BooleanField(
        default=False, verbose_name='se han generado las posiciones?')
    is_virtual = models.BooleanField(
        default=False, verbose_name='es virtual?',
        help_text='Si es virtual, sus posiciones no se contarán en el dashboard')

    class Meta:
        verbose_name = 'cava'
        verbose_name_plural = 'cavas'

    def __str__(self):
        return self.name


class Position(BaseModel):
    PLACE_CHOICES = [
        ('A', 'A - Adelante'),
        ('B', 'B - Atrás'),
    ]

    RACK_CHOICES = services.generate_rack_list(17)

    HALL_CHOICES = [
        ('A', 'Pasillo A'),
        ('B', 'Pasillo B'),
        ('C', 'Pasillo C'),
        ('D', 'Pasillo D'),
    ]

    status = models.CharField(
        max_length=15,
        choices=choices.PositionStatus.choices,
        default=choices.PositionStatus.LIBRE,
        verbose_name='estado'
    )

    level = models.IntegerField(default=1, verbose_name='nivel')
    place = models.CharField(
        max_length=1, choices=PLACE_CHOICES, verbose_name='lugar')
    rack = models.IntegerField(choices=RACK_CHOICES, verbose_name='rack')
    hall = models.CharField(
        max_length=1, choices=HALL_CHOICES, verbose_name='pasillo')
    cava = models.ForeignKey(Cava, on_delete=models.CASCADE,
                             null=True, related_name="positions", verbose_name='cava')
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="positions",
        verbose_name="workspace",
    )
    is_picking = models.BooleanField(default=False, verbose_name='es picking?')
    code = models.CharField(max_length=100, editable=False, verbose_name='código')

    qr_code_image = models.ImageField(
        upload_to=wms_position_qr_upload,
        blank=True,
        null=True,
        verbose_name='imagen de código QR'
    )

    zpl_code = models.TextField(
        blank=True, null=True, verbose_name='código ZPL')

    class Meta:
        verbose_name = 'posición'
        verbose_name_plural = 'posiciones'
        constraints = [
            models.UniqueConstraint(
                fields=['cava', 'hall', 'rack', 'level', 'place'],
                name='unique_position_slot_per_cava',
            ),
            models.UniqueConstraint(
                fields=['workspace', 'code'],
                name='unique_position_code_per_workspace',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.cava_id and not self.workspace_id:
            ws_id = (
                Cava.objects.filter(pk=self.cava_id)
                .values_list("storage__workspace_id", flat=True)
                .first()
            )
            if ws_id:
                self.workspace_id = ws_id

        if not self.code:
            cava_code = services.cava_code_prefix(self.cava.name if self.cava else "")
            hall_code = f"P{self.hall}"
            rack_code = f"R{self.rack}"
            level_code = f"N{self.level}"
            place_code = self.place

            self.code = f"{cava_code}-{hall_code}-{rack_code}-{level_code}{place_code}"

        qr_buffer = services.generate_qr_with_label(
            self.code, self.code, self.level)
        filename = f"qrcode-{self.code}.webp"
        self.qr_code_image.save(filename, File(qr_buffer), save=False)

        self.zpl_code = services.generate_position_tag_zpl(self)

        super().save(*args, **kwargs)

    def __str__(self):
        return self.code
