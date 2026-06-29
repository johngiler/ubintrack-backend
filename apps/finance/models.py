from django.db import models
from decimal import Decimal


class Tariff(models.Model):
    """
    Modelo de tarifas con soporte para cálculo dinámico y estático.
    Todas las tarifas son POR CONDICIÓN (Congelado, Refrigerado, Seco).

    ═══════════════════════════════════════════════════════════════
    TARIFA DINÁMICA (rotación >= 40%)
    ═══════════════════════════════════════════════════════════════
    Se aplica cuando: (Entradas + Salidas) / Promedio Pallets >= 40%

    TOTAL = (Promedio × Tarifa Mensual por condición)  ← Almacenamiento
          + (Entradas Reales × Tarifa Entrada por condición) ← Entradas
          + (Salidas Reales × Tarifa Salida por condición)   ← Salidas
          + (Pickings × Tarifa Picking) ← Picking (si has_picking_tariff)

    Campos usados por condición:
    - refrigerated_storage, frozen_storage, dry_storage ($/mes)
    - in_pallet_[frozen|refrigerated|dry], in_floor_[frozen|refrigerated|dry] ($/entrada)
    - out_[frozen|refrigerated|dry] ($/salida)
    - picking ($/caja)

    ═══════════════════════════════════════════════════════════════
    TARIFA ESTÁTICA (rotación < 40%)
    ═══════════════════════════════════════════════════════════════
    Se aplica cuando: (Entradas + Salidas) / Promedio Pallets < 40%

    TOTAL = (Promedio × Tarifa Mensual por condición)
          + (Promedio × 80% × Tarifa Entrada por condición)
          + (Promedio × 80% × Tarifa Salida por condición)

    - NO se cobra picking
    - Movimientos reales se IGNORAN
    - Se cobra sobre TODOS los pallets promedio con penalización 80%

    Campos usados:
    - static_penalty_rate (0.80 = 80%)
    - Tarifas por condición para entrada/salida
    ═══════════════════════════════════════════════════════════════
    """
    name = models.CharField(max_length=255)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="tariffs",
        verbose_name="workspace",
    )

    # === TARIFAS POR CONDICIÓN - CONGELADO ===
    in_pallet_frozen = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Entrada Pallet Congelado',
        help_text='Costo por pallet congelado que ingresa')
    in_floor_frozen = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Entrada Piso Congelado',
        help_text='Costo por entrada en piso congelado')
    out_frozen = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Salida Congelado',
        help_text='Costo por pallet congelado que sale')

    # === TARIFAS POR CONDICIÓN - REFRIGERADO ===
    in_pallet_refrigerated = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Entrada Pallet Refrigerado',
        help_text='Costo por pallet refrigerado que ingresa')
    in_floor_refrigerated = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Entrada Piso Refrigerado',
        help_text='Costo por entrada en piso refrigerado')
    out_refrigerated = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Salida Refrigerado',
        help_text='Costo por pallet refrigerado que sale')

    # === TARIFAS POR CONDICIÓN - SECO ===
    in_pallet_dry = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Entrada Pallet Seco',
        help_text='Costo por pallet seco que ingresa')
    in_floor_dry = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Entrada Piso Seco',
        help_text='Costo por entrada en piso seco')
    out_dry = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Salida Seco',
        help_text='Costo por pallet seco que sale')

    # Almacenamiento diario por condición
    refrigerated_storage = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Almacenamiento Refrigerado',
        help_text='Tarifa diaria por pallet refrigerado')
    frozen_storage = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Almacenamiento Congelado',
        help_text='Tarifa diaria por pallet congelado')
    dry_storage = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Almacenamiento Seco',
        help_text='Tarifa diaria por pallet seco')

    # Picking y servicios
    picking = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Picking',
        help_text='Costo por movimiento/caja de picking')
    has_picking_tariff = models.BooleanField(
        default=False,
        verbose_name='Tarifa Picking Activa',
        help_text='Si está activo, se multiplica el total de cajas de cada bin por el valor de picking')
    crossdocking = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Crossdocking')
    restiba = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Restiba')
    added_pallet = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Pallet adicional')
    wrap = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Wrap')

    # === CONFIGURACIÓN TARIFA DINÁMICA/ESTÁTICA ===
    rotation_threshold = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.40'),
        verbose_name='Umbral de rotación',
        help_text='Porcentaje mínimo de rotación para aplicar tarifa dinámica (0.40 = 40%)')

    static_penalty_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.80'),
        verbose_name='Tasa de penalización estática',
        help_text='Porcentaje de entrada/salida que se cobra en tarifa estática (0.80 = 80%)')

    # Tarifa mensual fija de almacenamiento (usada en cálculo estático)
    monthly_storage_rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name='Tarifa mensual de almacenamiento',
        help_text='Tarifa fija mensual por pallet (usado en cálculo estático)')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "tarifa"
        verbose_name_plural = "tarifas"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                name="unique_tariff_name_per_workspace",
            )
        ]

    def __str__(self):
        return self.name

    def get_average_daily_storage_rate(self):
        """Retorna el promedio de las tarifas diarias de almacenamiento."""
        rates = [self.refrigerated_storage,
                 self.frozen_storage, self.dry_storage]
        valid_rates = [r for r in rates if r > 0]
        if valid_rates:
            return sum(valid_rates) / len(valid_rates)
        return Decimal('0.00')


class Invoice(models.Model):
    """
    Modelo para almacenar comprobantes de facturación generados.
    El número de comprobante es secuencial: RBCOLD0000001, RBCOLD0000002, etc.
    """
    # Número de comprobante (secuencial)
    number = models.CharField(
        max_length=20, 
        unique=True,
        verbose_name='Número de comprobante',
        help_text='Número secuencial del comprobante (ej: RBCOLD0000001)')
    
    # Relación con cliente
    client = models.ForeignKey(
        'core.Client', 
        on_delete=models.PROTECT,
        related_name='invoices',
        verbose_name='Cliente')
    
    # Período de facturación
    billing_start_date = models.DateField(verbose_name='Fecha inicio período')
    billing_end_date = models.DateField(verbose_name='Fecha fin período')
    
    # Datos fiscales del cliente (al momento de generar)
    client_ruc = models.CharField(
        max_length=50, 
        verbose_name='RUC/Cédula',
        help_text='Número de identificación fiscal del cliente')
    client_dv = models.CharField(
        max_length=10, 
        verbose_name='DV',
        help_text='Dígito verificador')
    client_address = models.TextField(
        blank=True, 
        verbose_name='Dirección',
        help_text='Dirección fiscal del cliente')
    
    # Punto de facturación
    billing_point = models.CharField(
        max_length=20, 
        default='0000 001',
        verbose_name='Punto de facturación')
    
    # Datos del comprobante
    vendor = models.CharField(
        max_length=100, 
        blank=True,
        verbose_name='Vendedor')
    comment = models.TextField(
        blank=True,
        verbose_name='Comentario',
        help_text='Comentario adicional para el comprobante')
    
    # Totales
    subtotal = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        verbose_name='Subtotal')
    tax_exempt = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        verbose_name='Monto exento ITBMS')
    tax_amount = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        verbose_name='Total impuesto')
    total = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        verbose_name='Total')
    
    # Líneas de detalle (JSON para flexibilidad)
    line_items = models.JSONField(
        default=list,
        verbose_name='Líneas de detalle',
        help_text='Lista de items del comprobante')
    
    # Datos del billing completo (para referencia)
    billing_data = models.JSONField(
        default=dict,
        verbose_name='Datos de facturación',
        help_text='Datos completos del cálculo de billing')
    
    # Auditoría
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'core.CustomUser', 
        on_delete=models.SET_NULL, 
        null=True,
        related_name='invoices_created',
        verbose_name='Creado por')
    
    class Meta:
        verbose_name = 'comprobante'
        verbose_name_plural = 'comprobantes'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.number} - {self.client.name}"
    
    @classmethod
    def get_next_number(cls):
        """
        Genera el siguiente número de comprobante secuencial.
        Formato: RBCOLD0000001
        """
        prefix = 'RBCOLD'
        last_invoice = cls.objects.filter(
            number__startswith=prefix
        ).order_by('-number').first()
        
        if last_invoice:
            # Extraer el número del último comprobante
            last_num = int(last_invoice.number.replace(prefix, ''))
            next_num = last_num + 1
        else:
            next_num = 1
        
        return f"{prefix}{next_num:07d}"
