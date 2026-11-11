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


class PositionNomenclature(BaseModel):
    """Cómo arma el código de posición este owner. Los tokens los pone cada cava."""

    workspace = models.OneToOneField(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="position_nomenclature",
        verbose_name="workspace",
    )
    separator = models.CharField(max_length=4, default="-", verbose_name="separador")
    cava_prefix = models.CharField(
        max_length=8,
        blank=True,
        default="",
        verbose_name="prefijo de cava",
        help_text="Obsoleto: el token de la cava ya viene completo desde el nombre (A1, PC).",
    )
    aisle_prefix = models.CharField(
        max_length=8,
        blank=True,
        default="",
        verbose_name="prefijo de pasillo",
        help_text="Obsoleto: el token del pasillo ya viene completo desde el nombre (P1, T1).",
    )
    rack_prefix = models.CharField(max_length=8, blank=True, default="R", verbose_name="prefijo de rack")
    level_prefix = models.CharField(max_length=8, blank=True, default="N", verbose_name="prefijo de nivel")

    class Meta:
        verbose_name = "nomenclatura de posiciones"
        verbose_name_plural = "nomenclaturas de posiciones"

    def __str__(self):
        return f"Nomenclatura {self.workspace_id}"


class Cava(BaseModel):
    storage = models.ForeignKey(
        Storage,
        on_delete=models.CASCADE,
        null=True,
        related_name='cavas',
        verbose_name='bodega'
    )

    name = models.CharField(max_length=50, verbose_name='nombre de la cava')
    code = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="código",
        help_text="Token completo de la cava (iniciales del nombre). Ejemplo: Anden 1 → A1, Paola Cabrona → PC.",
    )
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
    plan_layout = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="plano gráfico",
        help_text="Metadatos solo visuales: puertas, eje de profundidad. No afecta nomenclatura ni generate.",
    )

    class Meta:
        verbose_name = 'cava'
        verbose_name_plural = 'cavas'

    def __str__(self):
        return self.name


class CavaBlock(BaseModel):
    """Tramo del plano de la cava.

    - rack: celdas nivel × profundidad.
    - section: pasillo / túnel / división (nombre → token de nomenclatura).
    - floor: posiciones de piso (sobre-almacenamiento); hall = tokens de secciones vecinas.
    """

    class Kind(models.TextChoices):
        RACK = "rack", "Rack"
        SECTION = "section", "Sección"
        FLOOR = "floor", "Piso"

    class SectionType(models.TextChoices):
        PASILLO = "pasillo", "Pasillo"
        TUNEL = "tunel", "Túnel"
        DIVISION = "division", "División"

    class Orientation(models.TextChoices):
        HORIZONTAL = "horizontal", "Horizontal"
        VERTICAL = "vertical", "Vertical"

    class LabelSide(models.TextChoices):
        ABOVE = "above", "Arriba"
        BELOW = "below", "Abajo"

    cava = models.ForeignKey(
        Cava,
        on_delete=models.CASCADE,
        related_name="blocks",
        verbose_name="cava",
    )
    sort_order = models.PositiveIntegerField(default=0, verbose_name="orden")
    kind = models.CharField(max_length=8, choices=Kind.choices, verbose_name="tipo")
    section_type = models.CharField(
        max_length=12,
        choices=SectionType.choices,
        blank=True,
        default="",
        verbose_name="tipo de sección",
        help_text="Solo section: pasillo, tunel o division.",
    )
    name = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="nombre",
        help_text="Nombre visible de la sección (ej. Pasillo 1, Tunel 1, Linea 1). El code se deriva de aquí.",
    )
    code = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="código",
        help_text="Token en el código de posición. En sección: derivado del nombre (P1). En rack: inicio de serie.",
    )
    rack_count = models.PositiveIntegerField(
        default=1,
        verbose_name="cantidad",
        help_text="En rack: cantidad de racks. En floor: posiciones de piso. En section: 0.",
    )
    levels = models.PositiveIntegerField(null=True, blank=True, verbose_name="niveles")
    depth = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="profundidad",
        help_text="En rack: profundidades A..N. En floor: cuántas posiciones caben por grupo (dimensión).",
    )
    overlap = models.BooleanField(
        default=False,
        verbose_name="solapar",
        help_text="Solo floor: dibujar las posiciones sobre la sección (pasillo/túnel) vecina.",
    )
    always_visible = models.BooleanField(
        default=False,
        verbose_name="siempre visible",
        help_text="Solo floor: mostrar posiciones aunque el plano tenga ocultas las de piso.",
    )
    stretch_full = models.BooleanField(
        default=True,
        verbose_name="100% ancho",
        help_text="Solo section: ocupar todo el eje de la hilera relacionada.",
    )
    orientation = models.CharField(
        max_length=12,
        choices=Orientation.choices,
        default=Orientation.HORIZONTAL,
        verbose_name="orientación",
        help_text="Horizontal pone este grupo en una fila. Vertical apila los racks.",
    )
    label_side = models.CharField(
        max_length=8,
        choices=LabelSide.choices,
        default=LabelSide.BELOW,
        verbose_name="lado del nombre",
        help_text="Dónde se escribe el nombre del rack en el plano.",
    )
    aisle_code = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="pasillo",
        help_text="Token de sección ligada al rack. Vacío = heredar el contiguo; '-' = sin sección.",
    )

    class Meta:
        verbose_name = "bloque de cava"
        verbose_name_plural = "bloques de cava"
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["cava", "sort_order"],
                name="unique_cava_block_order",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.code}".strip()

    def rack_tokens(self) -> list[str]:
        if self.kind != self.Kind.RACK:
            return []
        count = self.rack_count or 1
        raw = (self.code or "").strip()
        if raw.isdigit():
            start = int(raw)
            return [str(start + i) for i in range(count)]
        if count == 1 and raw:
            return [raw]
        return []

    def resolved_depth_codes(self) -> list[str]:
        total = self.depth or 1
        alphabet = [chr(ord("A") + i) for i in range(26)]
        return [alphabet[i] if i < len(alphabet) else str(i + 1) for i in range(total)]


class CavaGenerationJob(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        RUNNING = "running", "En proceso"
        SUCCEEDED = "succeeded", "Listo"
        FAILED = "failed", "Fallido"

    cava = models.ForeignKey(
        Cava,
        on_delete=models.CASCADE,
        related_name="generation_jobs",
        verbose_name="cava",
    )
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="cava_generation_jobs",
        verbose_name="workspace",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="estado",
    )
    expected_count = models.PositiveIntegerField(default=0, verbose_name="posiciones previstas")
    created_count = models.PositiveIntegerField(default=0, verbose_name="posiciones creadas")
    skipped_count = models.PositiveIntegerField(default=0, verbose_name="posiciones ya existentes")

    class Meta:
        verbose_name = "generación de cava"
        verbose_name_plural = "generaciones de cava"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.cava_id} {self.status}"


class Position(BaseModel):
    status = models.CharField(
        max_length=15,
        choices=choices.PositionStatus.choices,
        default=choices.PositionStatus.LIBRE,
        verbose_name='estado'
    )

    level = models.IntegerField(default=1, verbose_name='nivel')
    place = models.CharField(max_length=32, verbose_name='profundidad')
    rack = models.CharField(max_length=32, verbose_name='rack')
    hall = models.CharField(max_length=32, blank=True, default="", verbose_name='pasillo')
    cava = models.ForeignKey(Cava, on_delete=models.CASCADE,
                             null=True, related_name="positions", verbose_name='cava')
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="positions",
        verbose_name="workspace",
    )
    is_picking = models.BooleanField(default=False, verbose_name='es picking?')
    is_push = models.BooleanField(
        default=False,
        verbose_name='es push?',
        help_text='Marca el conducto (cava+pasillo+rack+nivel) como almacenamiento push-back.',
    )
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
            models.CheckConstraint(
                check=(
                    models.Q(is_active=True) | models.Q(is_push=False)
                )
                & (models.Q(is_active=True) | models.Q(is_picking=False))
                & (models.Q(is_push=False) | models.Q(is_picking=False)),
                name="position_flags_active_push_picking",
            ),
        ]

    # Campos de slot que alimentan `code` (y por tanto QR/ZPL).
    _LABEL_SOURCE_FIELDS = frozenset(
        {"cava", "cava_id", "hall", "rack", "level", "place"}
    )
    _LABEL_OUTPUT_FIELDS = frozenset({"code", "qr_code_image", "zpl_code"})

    def clean(self):
        from apps.storage.utils.position_flags import validate_position_flags

        super().clean()
        validate_position_flags(
            is_active=bool(self.is_active),
            is_picking=bool(self.is_picking),
            is_push=bool(self.is_push),
        )

    def build_code(self) -> str:
        nomenclature = None
        if self.workspace_id:
            nomenclature = PositionNomenclature.objects.filter(
                workspace_id=self.workspace_id
            ).first()
        return services.build_position_code(
            cava_name=self.cava.name if self.cava_id and self.cava else "",
            cava_code=self.cava.code if self.cava_id and self.cava else "",
            hall=self.hall,
            rack=self.rack,
            level=self.level,
            place=self.place,
            nomenclature=nomenclature,
        )

    def refresh_label(self, *, persist=False):
        """Regenera imagen QR + ZPL a partir de code/level."""
        qr_buffer = services.generate_qr_with_label(
            self.code, self.code, self.level
        )
        filename = f"qrcode-{self.code}.webp"
        # Reescribe el archivo (upload_to fija la ruta por código).
        if self.qr_code_image:
            self.qr_code_image.delete(save=False)
        self.qr_code_image.save(filename, File(qr_buffer), save=False)
        self.zpl_code = services.generate_position_tag_zpl(self)
        if persist:
            models.Model.save(
                self,
                update_fields=["qr_code_image", "zpl_code", "updated_at"],
            )
        return self

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        is_new = self._state.adding
        previous_code = None

        if self.cava_id and not self.workspace_id:
            ws_id = (
                Cava.objects.filter(pk=self.cava_id)
                .values_list("storage__workspace_id", flat=True)
                .first()
            )
            if ws_id:
                self.workspace_id = ws_id

        self.hall = str(self.hall or "").strip()
        self.rack = str(self.rack or "").strip()
        self.place = str(self.place or "").strip()

        need_rebuild_code = is_new or not self.code
        need_refresh_label = is_new or not self.qr_code_image or not self.zpl_code

        if update_fields is not None:
            uf = set(update_fields)
            # Persistencia pura de etiqueta ya generada (regenerate manual/bulk).
            label_persist_only = uf <= {
                "qr_code_image",
                "zpl_code",
                "updated_at",
            }
            if label_persist_only:
                super().save(*args, **kwargs)
                return

            if uf & self._LABEL_SOURCE_FIELDS:
                if not is_new and self.pk:
                    previous_code = (
                        Position.objects.filter(pk=self.pk)
                        .values_list("code", flat=True)
                        .first()
                    )
                need_rebuild_code = True
                need_refresh_label = True
            elif uf & {"code", "level"}:
                if not is_new and self.pk and "code" in uf:
                    previous_code = (
                        Position.objects.filter(pk=self.pk)
                        .values_list("code", flat=True)
                        .first()
                    )
                need_refresh_label = True
            elif not (uf & self._LABEL_OUTPUT_FIELDS):
                # Update de flags/status/etc.: no tocar QR/ZPL.
                need_rebuild_code = False
                need_refresh_label = False
        elif not is_new:
            old = (
                Position.objects.filter(pk=self.pk)
                .values("cava_id", "hall", "rack", "level", "place", "code")
                .first()
            )
            if old:
                previous_code = old["code"]
                slot_changed = (
                    old["cava_id"] != self.cava_id
                    or old["hall"] != self.hall
                    or old["rack"] != self.rack
                    or old["level"] != self.level
                    or old["place"] != self.place
                )
                if slot_changed:
                    need_rebuild_code = True
                    need_refresh_label = True
                elif old["code"] != self.code or old["level"] != self.level:
                    need_refresh_label = True
                else:
                    need_rebuild_code = False
                    # Mantener need_refresh_label si faltaba imagen/zpl.
            else:
                need_rebuild_code = True
                need_refresh_label = True

        if need_rebuild_code:
            self.code = self.build_code()
            need_refresh_label = True

        if need_refresh_label:
            self.refresh_label(persist=False)
            if update_fields is not None:
                kwargs["update_fields"] = list(
                    set(update_fields)
                    | {"code", "qr_code_image", "zpl_code", "updated_at"}
                )

        super().save(*args, **kwargs)

        # Si cambió el código, refrescar tags de bins que lo muestran.
        if (
            need_refresh_label
            and previous_code
            and previous_code != self.code
            and self.pk
        ):
            from apps.inventory.models import Bin

            for bin_obj in Bin.objects.filter(position_id=self.pk).iterator():
                bin_obj.generate_tag()

    def __str__(self):
        return self.code
