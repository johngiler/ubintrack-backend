import os
from decimal import Decimal
from datetime import datetime, date, timedelta
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, Count, Q, F
from calendar import monthrange
from apps.inventory.models import Bin
from apps.order.models import Entry, Exit
from apps.core.models import Client
from apps.core.choices import BinStatus, Conditions, InTypes


def add_months(source_date, months):
    """Add months to a date."""
    month = source_date.month - 1 + months
    year = source_date.year + month // 12
    month = month % 12 + 1
    day = min(source_date.day, monthrange(year, month)[1])
    return source_date.replace(year=year, month=month, day=day)


def calculate_storage_days(bin, period_start=None, period_end=None):
    """
    Calculate storage days for a bin within an optional date range.
    From created_at to dispatched_date or current date if still stored.

    If period_start and period_end are provided, only counts days within that range.
    """
    # Asegurar que los datetimes tengan zona horaria
    def make_aware(dt):
        if dt is None:
            return None
        if timezone.is_naive(dt):
            return timezone.make_aware(dt)
        return dt

    period_start = make_aware(period_start)
    period_end = make_aware(period_end)

    # Determine actual start: the later of bin creation or period start
    if period_start:
        start_date = max(bin.created_at, period_start)
    else:
        start_date = bin.created_at

    # Determine actual end
    if bin.dispatched_date:
        if period_end:
            end_date = min(bin.dispatched_date, period_end)
        else:
            end_date = bin.dispatched_date
    else:
        # Still stored
        if period_end:
            end_date = min(timezone.now(), period_end)
        else:
            end_date = timezone.now()

    # Only count if there's overlap
    if start_date > end_date:
        return 0

    delta = end_date - start_date
    return delta.days


def calculate_bin_storage_cost(bin, tariff, period_start=None, period_end=None):
    """
    Calculate storage cost for a single bin based on its condition and storage days.
    Storage is charged PER DAY.

    If period_start and period_end are provided, only counts days within that period.
    """
    days = calculate_storage_days(bin, period_start, period_end)

    if days == 0:
        return Decimal('0.00'), 0

    # Get condition from the bin's entry
    condition = bin.entry.condition

    # Determine daily rate based on condition
    if condition == Conditions.REFRIGERATED:
        daily_rate = tariff.refrigerated_storage
    elif condition == Conditions.FROZEN:
        daily_rate = tariff.frozen_storage
    else:
        daily_rate = Decimal('0.00')

    return days * daily_rate, days


def calculate_client_billing(client_id, start_date=None, end_date=None):
    """
    Calculate billing for a specific client within a date range.
    Implements DYNAMIC and STATIC tariff calculation.

    DYNAMIC TARIFF (rotation >= threshold):
    - Uses actual movements (entries, exits, picking)
    - Storage = average_pallets × daily_rate × days_in_period

    STATIC TARIFF (rotation < threshold):
    - Ignores actual movements
    - Charges ALL average pallets with penalty
    - Formula: avg_pallets × (monthly_storage + penalty_rate × entry + penalty_rate × exit)
    - NO picking charged

    Rotation = (entries + exits) / average_pallets
    """
    try:
        client = Client.objects.select_related('tariff').get(id=client_id)
    except Client.DoesNotExist:
        return None

    tariff = client.tariff
    if not tariff:
        return None

    # Calculate period days
    today = timezone.now().date()

    if start_date and end_date:
        # Normalize to date objects if datetime
        if hasattr(start_date, 'date'):
            period_start_date = start_date.date() if callable(
                getattr(start_date, 'date', None)) else start_date
        else:
            period_start_date = start_date
        if hasattr(end_date, 'date'):
            period_end_date = end_date.date() if callable(
                getattr(end_date, 'date', None)) else end_date
        else:
            period_end_date = end_date

        # Handle datetime vs date
        if isinstance(period_start_date, datetime):
            period_start_date = period_start_date.date()
        if isinstance(period_end_date, datetime):
            period_end_date = period_end_date.date()

        # IMPORTANTE: No calcular días futuros - limitar end_date a hoy
        effective_end_date = min(period_end_date, today)

        # Si la fecha de inicio es futura, no hay nada que calcular
        if period_start_date > today:
            period_days = 0
        else:
            period_days = (effective_end_date - period_start_date).days + 1
    else:
        period_days = 30  # Default
        effective_end_date = today

    # === STEP 1: Gather Raw Data ===
    # NOTA: Usamos effective_end_date para no incluir datos futuros
    # Usar la misma lógica que generate_billing_report_excel para consistencia

    # Storage: bins that were active during the period (hasta hoy, no futuro)
    bins_query = Bin.objects.filter(
        client=client,
        is_active=True
    ).select_related('entry', 'position')

    if start_date and end_date:
        bins_query = bins_query.filter(
            Q(created_at__lte=effective_end_date) &
            (Q(dispatched_date__gte=period_start_date) | Q(dispatched_date__isnull=True))
        )

    # Entries: bins created in the period (hasta hoy)
    # Usar QuerySet (no lista) para poder filtrar por condición
    bins_created_query = Bin.objects.filter(
        client=client,
        is_active=True,
        created_at__gte=period_start_date,
        created_at__lte=effective_end_date
    ).select_related('entry')

    # Exits: bins dispatched in the period (hasta hoy)
    bins_dispatched_query = Bin.objects.filter(
        client=client,
        is_active=True,
        dispatched_date__gte=period_start_date,
        dispatched_date__lte=effective_end_date
    ).select_related('entry')

    # Picking (hasta hoy)
    exits_with_services_query = Exit.objects.filter(
        client=client,
        created_at__gte=period_start_date,
        created_at__lte=effective_end_date
    ).prefetch_related('exit_product_groups__exit_products')

    # === STEP 2: Calculate Counts and Details ===
    # Contar entradas y salidas por condición (igual que en generate_billing_report_excel)
    frozen_in = bins_created_query.filter(entry__condition=Conditions.FROZEN).count()
    refrigerated_in = bins_created_query.filter(entry__condition=Conditions.REFRIGERATED).count()
    dry_in = bins_created_query.filter(entry__condition=Conditions.DRY).count()
    
    frozen_out = bins_dispatched_query.filter(entry__condition=Conditions.FROZEN).count()
    refrigerated_out = bins_dispatched_query.filter(entry__condition=Conditions.REFRIGERATED).count()
    dry_out = bins_dispatched_query.filter(entry__condition=Conditions.DRY).count()

    # Totales de entradas y salidas
    entries_count = frozen_in + refrigerated_in + dry_in
    exits_count = frozen_out + refrigerated_out + dry_out

    bins = list(bins_query)
    bins_created = list(bins_created_query)
    bins_dispatched = list(bins_dispatched_query)
    exits_with_services = list(exits_with_services_query)

    # Calculate picking count
    picking_count = 0
    for exit in exits_with_services:
        for group in exit.exit_product_groups.all():
            picking_products = group.exit_products.filter(is_picking=True)
            picking_count += picking_products.count()

    # Calculate average pallets using existing function
    # NOTA: Usamos effective_end_date para no incluir días futuros
    # Calcular promedios por condición (igual que generate_billing_report_excel)
    if start_date and end_date:
        avg_data = calculate_daily_inventory_average(
            client_id, period_start_date, effective_end_date, 'all')
        avg_refrigerated = calculate_daily_inventory_average(
            client_id, period_start_date, effective_end_date, 'refrigerated')
        avg_frozen = calculate_daily_inventory_average(
            client_id, period_start_date, effective_end_date, 'frozen')
        avg_dry = calculate_daily_inventory_average(
            client_id, period_start_date, effective_end_date, 'dry')
        
        average_pallets = Decimal(
            str(avg_data.get('average_bins', 0))) if avg_data else Decimal('0')
    else:
        avg_data = None
        avg_refrigerated = None
        avg_frozen = None
        avg_dry = None
        average_pallets = Decimal(str(len(bins)))

    # === STEP 3: Calculate Rotation and Determine Tariff Type ===

    total_movements = entries_count + exits_count
    rotation = Decimal('0.00')
    if average_pallets > 0:
        rotation = Decimal(str(total_movements)) / average_pallets

    rotation_threshold = tariff.rotation_threshold or Decimal('0.40')

    # Decisión de tarifa basada SOLO en el porcentaje de rotación:
    # - Rotación >= umbral (40%) → DINÁMICA (movimientos reales)
    # - Rotación < umbral (40%) → ESTÁTICA (promedio con penalización 80%)
    is_dynamic_tariff = rotation >= rotation_threshold

    # === STEP 4: Calculate Costs Based on Tariff Type ===

    # Initialize billing data
    billing_data = {
        'client_id': client.id,
        'client_name': client.name,
        'client_ruc': client.ruc or '',
        'client_dv': client.dv or '',
        'client_address': client.address or '',
        'tariff_name': tariff.name,
        'tariff_type': 'dynamic' if is_dynamic_tariff else 'static',
        'rotation_percentage': float(rotation * 100),
        'rotation_threshold': float(rotation_threshold * 100),
        'average_pallets': float(average_pallets),
        'period_days': period_days,
        'storage': {
            'total_cost': Decimal('0.00'),
            'total_days': 0,
            'bins_count': 0,
            'refrigerated': {'cost': Decimal('0.00'), 'days': 0, 'bins': 0},
            'frozen': {'cost': Decimal('0.00'), 'days': 0, 'bins': 0},
            'dry': {'cost': Decimal('0.00'), 'days': 0, 'bins': 0},
            'bins_detail': []
        },
        'entries': {
            'total_cost': Decimal('0.00'),
            'count': entries_count,
            'pallet': {'cost': Decimal('0.00'), 'count': 0},
            'floor': {'cost': Decimal('0.00'), 'count': 0}
        },
        'exits': {
            'total_cost': Decimal('0.00'),
            'count': exits_count
        },
        'picking': {
            'total_cost': Decimal('0.00'),
            'count': picking_count
        },
        'additional_services': {
            'total_cost': Decimal('0.00'),
            'restiba': {'cost': Decimal('0.00'), 'count': 0},
            'wrap': {'cost': Decimal('0.00'), 'count': 0},
            'added_pallet': {'cost': Decimal('0.00'), 'count': 0},
            'piso': {'cost': Decimal('0.00'), 'count': 0},
            'strech': {'cost': Decimal('0.00'), 'count': 0}
        },
        'total_billing': Decimal('0.00')
    }

    if is_dynamic_tariff:
        # === TARIFA DINÁMICA ===
        # Según documento de especificación:
        # TOTAL DINÁMICO = (Promedio Pallets × Tarifa Mensual)
        #                + (Entradas Reales × Tarifa Entrada)
        #                + (Salidas Reales × Tarifa Salida)
        #                + (Pickings Reales × Tarifa Picking)

        # ALMACENAMIENTO: Promedio Redondeado × Tarifa Mensual (la tarifa ya es mensual, no diaria)
        # Usar los promedios ya calculados arriba y REDONDEAR antes del cálculo
        avg_ref_bins_exact = Decimal(str(avg_refrigerated.get(
            'average_bins', 0))) if avg_refrigerated else Decimal('0')
        avg_frz_bins_exact = Decimal(
            str(avg_frozen.get('average_bins', 0))) if avg_frozen else Decimal('0')
        avg_dry_bins_exact = Decimal(
            str(avg_dry.get('average_bins', 0))) if avg_dry else Decimal('0')

        # Redondear promedios ANTES del cálculo para consistencia con verificación manual
        avg_ref_bins = Decimal(str(round(float(avg_ref_bins_exact))))
        avg_frz_bins = Decimal(str(round(float(avg_frz_bins_exact))))
        avg_dry_bins = Decimal(str(round(float(avg_dry_bins_exact))))

        # Calcular costo de almacenamiento: Promedio Redondeado × Tarifa Mensual
        ref_storage_cost = avg_ref_bins * tariff.refrigerated_storage
        frz_storage_cost = avg_frz_bins * tariff.frozen_storage
        dry_storage_cost = avg_dry_bins * (tariff.dry_storage or Decimal('0'))

        # Usar round() igual que en Facturación para consistencia
        billing_data['storage']['refrigerated']['cost'] = ref_storage_cost
        billing_data['storage']['refrigerated']['bins'] = round(float(avg_ref_bins))
        billing_data['storage']['refrigerated']['days'] = period_days

        billing_data['storage']['frozen']['cost'] = frz_storage_cost
        billing_data['storage']['frozen']['bins'] = round(float(avg_frz_bins))
        billing_data['storage']['frozen']['days'] = period_days

        billing_data['storage']['dry']['cost'] = dry_storage_cost
        billing_data['storage']['dry']['bins'] = round(float(avg_dry_bins))
        billing_data['storage']['dry']['days'] = period_days

        billing_data['storage']['total_cost'] = ref_storage_cost + \
            frz_storage_cost + dry_storage_cost
        billing_data['storage']['bins_count'] = round(float(avg_ref_bins)) + \
            round(float(avg_frz_bins)) + round(float(avg_dry_bins))
        billing_data['storage']['total_days'] = period_days

        # Llenar bins_detail con información de los bins almacenados
        for bin in bins:
            condition = bin.entry.condition if bin.entry else 'Sin condición'
            billing_data['storage']['bins_detail'].append({
                'id': bin.id,
                'code': bin.code,
                'status': bin.status,
                'condition': condition,
                'cost': 0,  # El costo se calcula por promedio, no por bin individual
                'created_at': bin.created_at.isoformat() if bin.created_at else None,
                'dispatched_date': bin.dispatched_date.isoformat() if bin.dispatched_date else None
            })

        # ENTRADAS: Entradas Reales × Tarifa Entrada (por condición)
        processed_entries = set()
        for bin_created in bins_created:
            entry = bin_created.entry
            if entry:
                # Obtener tarifa según condición
                condition = entry.condition or ''
                if entry.in_type == InTypes.PISO:
                    # Seleccionar tarifa por condición (entrada en piso)
                    if condition == 'Congelado':
                        rate = tariff.in_floor_frozen
                    elif condition == 'Refrigerado':
                        rate = tariff.in_floor_refrigerated
                    elif condition == 'Seco':
                        rate = tariff.in_floor_dry
                    else:
                        rate = Decimal('0.00')
                    billing_data['entries']['floor']['cost'] += rate
                    billing_data['entries']['floor']['count'] += 1
                    billing_data['entries']['total_cost'] += rate
                else:
                    # PALLETIZADO o sin tipo definido -> contar como Pallet
                    # Seleccionar tarifa por condición (entrada en pallet)
                    if condition == 'Congelado':
                        rate = tariff.in_pallet_frozen
                    elif condition == 'Refrigerado':
                        rate = tariff.in_pallet_refrigerated
                    elif condition == 'Seco':
                        rate = tariff.in_pallet_dry
                    else:
                        rate = Decimal('0.00')
                    billing_data['entries']['pallet']['cost'] += rate
                    billing_data['entries']['pallet']['count'] += 1
                    billing_data['entries']['total_cost'] += rate

            # Servicios adicionales de ENTRADAS (una vez por entrada)
            if entry and entry.id not in processed_entries:
                processed_entries.add(entry.id)
                # Restiba
                if entry.total_restibas > 0:
                    restiba_cost = tariff.restiba * entry.total_restibas
                    billing_data['additional_services']['restiba']['cost'] += restiba_cost
                    billing_data['additional_services']['restiba']['count'] += entry.total_restibas
                    billing_data['additional_services']['total_cost'] += restiba_cost
                # Wrap/Strech de entradas
                if entry.total_wraps > 0:
                    wrap_cost = tariff.wrap * entry.total_wraps
                    billing_data['additional_services']['wrap']['cost'] += wrap_cost
                    billing_data['additional_services']['wrap']['count'] += entry.total_wraps
                    billing_data['additional_services']['total_cost'] += wrap_cost
                # Pallet adicional
                if hasattr(entry, 'total_added_pallets') and entry.total_added_pallets > 0:
                    added_pallet_cost = tariff.added_pallet * entry.total_added_pallets
                    billing_data['additional_services']['added_pallet']['cost'] += added_pallet_cost
                    billing_data['additional_services']['added_pallet']['count'] += entry.total_added_pallets
                    billing_data['additional_services']['total_cost'] += added_pallet_cost

        # SALIDAS: Salidas Reales × Tarifa Salida (por condición)
        for bin_dispatched in bins_dispatched:
            entry = bin_dispatched.entry
            condition = entry.condition if entry else ''
            # Seleccionar tarifa por condición
            if condition == 'Congelado':
                rate = tariff.out_frozen
            elif condition == 'Refrigerado':
                rate = tariff.out_refrigerated
            elif condition == 'Seco':
                rate = tariff.out_dry
            else:
                rate = Decimal('0.00')
            billing_data['exits']['total_cost'] += rate

        # PICKING: Solo se cobra si has_picking_tariff está activo
        # Suma de promedios por condición × Tarifa Picking
        if tariff.has_picking_tariff:
            # Usar la suma de average_units por condición (igual que Facturación)
            avg_ref_units = Decimal(
                str(avg_refrigerated.get('average_units', 0))) if avg_refrigerated else Decimal('0')
            avg_frz_units = Decimal(
                str(avg_frozen.get('average_units', 0))) if avg_frozen else Decimal('0')
            avg_dry_units = Decimal(
                str(avg_dry.get('average_units', 0))) if avg_dry else Decimal('0')
            avg_boxes = round(avg_ref_units) + round(avg_frz_units) + round(avg_dry_units)
            billing_data['picking']['total_cost'] = tariff.picking * Decimal(str(avg_boxes))
            billing_data['picking']['count'] = int(avg_boxes)
            billing_data['picking']['type'] = 'boxes'
        else:
            # Si no tiene has_picking_tariff, NO se cobra picking
            billing_data['picking']['total_cost'] = Decimal('0.00')
            billing_data['picking']['count'] = 0
            billing_data['picking']['type'] = 'none'

        # PISO: Contar bins de entradas a piso (para referencia)
        piso_count = bins_created_query.filter(entry__in_type=InTypes.PISO).count()
        billing_data['additional_services']['piso']['count'] = piso_count
        # El costo de piso ya está incluido en entradas floor

        # WRAP/STRECH de SALIDAS: Solo si has_picking_tariff está activo
        # Se cobra wrap por cada bin despachado
        if tariff.has_picking_tariff and exits_count > 0:
            salidas_wrap_cost = tariff.wrap * exits_count
            billing_data['additional_services']['strech']['count'] = exits_count
            billing_data['additional_services']['strech']['cost'] = salidas_wrap_cost
            billing_data['additional_services']['total_cost'] += salidas_wrap_cost
        else:
            billing_data['additional_services']['strech']['count'] = 0
            billing_data['additional_services']['strech']['cost'] = Decimal('0.00')

        # Agregar detalles del cálculo dinámico
        billing_data['dynamic_details'] = {
            'calculation_method': 'average_x_monthly_rate',
            'formula': 'Promedio × Tarifa Mensual + Entradas × Tarifa + Salidas × Tarifa + Picking × Tarifa',
            'refrigerated_avg': float(avg_ref_bins),
            'refrigerated_monthly_rate': float(tariff.refrigerated_storage),
            'refrigerated_storage_cost': float(ref_storage_cost),
            'refrigerated_entry_rate': float(tariff.in_pallet_refrigerated),
            'refrigerated_exit_rate': float(tariff.out_refrigerated),
            'frozen_avg': float(avg_frz_bins),
            'frozen_monthly_rate': float(tariff.frozen_storage),
            'frozen_storage_cost': float(frz_storage_cost),
            'frozen_entry_rate': float(tariff.in_pallet_frozen),
            'frozen_exit_rate': float(tariff.out_frozen),
            'dry_avg': float(avg_dry_bins),
            'dry_monthly_rate': float(tariff.dry_storage or 0),
            'dry_storage_cost': float(dry_storage_cost),
            'dry_entry_rate': float(tariff.in_pallet_dry),
            'dry_exit_rate': float(tariff.out_dry),
            'period_days': period_days,
            'picking_rate': float(tariff.picking),
            'entries_count': entries_count,
            'exits_count': exits_count,
            'picking_count': picking_count
        }

    else:
        # === STATIC TARIFF CALCULATION ===
        # Según documento de especificación (rotación < 40%):
        #
        # DIFERENCIA CON DINÁMICA:
        # - Almacenamiento: IGUAL que dinámica (Promedio × Tarifa Mensual)
        # - Entradas: Promedio × 80% × Tarifa (NO movimientos reales)
        # - Salidas: Promedio × 80% × Tarifa (NO movimientos reales)
        # - Picking: NO SE COBRA
        #
        # Ejemplo: 100 pallets promedio, tarifa mensual $32, entrada $15, salida $15
        # Almacenamiento: 100 × $32 = $3,200
        # Entradas: 100 × 0.8 × $15 = $1,200
        # Salidas: 100 × 0.8 × $15 = $1,200
        # TOTAL = $5,600

        penalty_rate = tariff.static_penalty_rate or Decimal('0.80')

        # ALMACENAMIENTO: Igual que dinámica (Promedio Redondeado × Tarifa Mensual)
        # Usar los promedios ya calculados arriba y REDONDEAR antes del cálculo
        avg_ref_bins_exact = Decimal(str(avg_refrigerated.get(
            'average_bins', 0))) if avg_refrigerated else Decimal('0')
        avg_frz_bins_exact = Decimal(
            str(avg_frozen.get('average_bins', 0))) if avg_frozen else Decimal('0')
        avg_dry_bins_exact = Decimal(
            str(avg_dry.get('average_bins', 0))) if avg_dry else Decimal('0')

        # Redondear promedios ANTES del cálculo para consistencia con verificación manual
        avg_ref_bins = Decimal(str(round(float(avg_ref_bins_exact))))
        avg_frz_bins = Decimal(str(round(float(avg_frz_bins_exact))))
        avg_dry_bins = Decimal(str(round(float(avg_dry_bins_exact))))

        # Calcular almacenamiento: Promedio Redondeado × Tarifa Mensual
        ref_storage_cost = avg_ref_bins * tariff.refrigerated_storage
        frz_storage_cost = avg_frz_bins * tariff.frozen_storage
        dry_storage_cost = avg_dry_bins * (tariff.dry_storage or Decimal('0'))

        # Usar round() igual que en Facturación para consistencia
        billing_data['storage']['refrigerated']['cost'] = ref_storage_cost
        billing_data['storage']['refrigerated']['bins'] = round(float(avg_ref_bins))
        billing_data['storage']['refrigerated']['days'] = period_days

        billing_data['storage']['frozen']['cost'] = frz_storage_cost
        billing_data['storage']['frozen']['bins'] = round(float(avg_frz_bins))
        billing_data['storage']['frozen']['days'] = period_days

        billing_data['storage']['dry']['cost'] = dry_storage_cost
        billing_data['storage']['dry']['bins'] = round(float(avg_dry_bins))
        billing_data['storage']['dry']['days'] = period_days

        billing_data['storage']['total_cost'] = ref_storage_cost + \
            frz_storage_cost + dry_storage_cost
        billing_data['storage']['bins_count'] = round(float(avg_ref_bins)) + \
            round(float(avg_frz_bins)) + round(float(avg_dry_bins))
        billing_data['storage']['total_days'] = period_days

        # Llenar bins_detail con información de los bins almacenados
        for bin in bins:
            condition = bin.entry.condition if bin.entry else 'Sin condición'
            billing_data['storage']['bins_detail'].append({
                'id': bin.id,
                'code': bin.code,
                'status': bin.status,
                'condition': condition,
                'cost': 0,  # El costo se calcula por promedio, no por bin individual
                'created_at': bin.created_at.isoformat() if bin.created_at else None,
                'dispatched_date': bin.dispatched_date.isoformat() if bin.dispatched_date else None
            })

        # ENTRADAS: Promedio por condición × 80% × Tarifa por condición
        ref_entry_rate = tariff.in_pallet_refrigerated
        frz_entry_rate = tariff.in_pallet_frozen
        dry_entry_rate = tariff.in_pallet_dry
        
        ref_entry_cost = avg_ref_bins * penalty_rate * ref_entry_rate
        frz_entry_cost = avg_frz_bins * penalty_rate * frz_entry_rate
        dry_entry_cost = avg_dry_bins * penalty_rate * dry_entry_rate
        
        entry_portion = ref_entry_cost + frz_entry_cost + dry_entry_cost

        billing_data['entries']['total_cost'] = entry_portion
        billing_data['entries']['count'] = int(average_pallets)
        billing_data['entries']['pallet']['count'] = int(average_pallets)
        billing_data['entries']['pallet']['cost'] = entry_portion

        # SALIDAS: Promedio por condición × 80% × Tarifa por condición
        ref_exit_rate = tariff.out_refrigerated
        frz_exit_rate = tariff.out_frozen
        dry_exit_rate = tariff.out_dry
        
        ref_exit_cost = avg_ref_bins * penalty_rate * ref_exit_rate
        frz_exit_cost = avg_frz_bins * penalty_rate * frz_exit_rate
        dry_exit_cost = avg_dry_bins * penalty_rate * dry_exit_rate
        
        exit_portion = ref_exit_cost + frz_exit_cost + dry_exit_cost

        billing_data['exits']['total_cost'] = exit_portion
        billing_data['exits']['count'] = int(average_pallets)

        # PICKING: NO se cobra en tarifa estática
        billing_data['picking']['total_cost'] = Decimal('0.00')
        billing_data['picking']['count'] = 0

        # Calcular costo por pallet para referencia
        storage_per_pallet = billing_data['storage']['total_cost'] / \
            average_pallets if average_pallets > 0 else Decimal('0')
        entry_per_pallet = entry_portion / average_pallets if average_pallets > 0 else Decimal('0')
        exit_per_pallet = exit_portion / average_pallets if average_pallets > 0 else Decimal('0')
        per_pallet_cost = storage_per_pallet + entry_per_pallet + exit_per_pallet

        # Detalles del cálculo estático para auditoría
        billing_data['static_details'] = {
            'penalty_rate': float(penalty_rate * 100),
            'storage_calculation': 'Promedio × Tarifa Mensual',
            'refrigerated_avg': float(avg_ref_bins),
            'refrigerated_monthly_rate': float(tariff.refrigerated_storage),
            'refrigerated_storage_cost': float(ref_storage_cost),
            'refrigerated_entry_rate': float(ref_entry_rate),
            'refrigerated_exit_rate': float(ref_exit_rate),
            'frozen_avg': float(avg_frz_bins),
            'frozen_monthly_rate': float(tariff.frozen_storage),
            'frozen_storage_cost': float(frz_storage_cost),
            'frozen_entry_rate': float(frz_entry_rate),
            'frozen_exit_rate': float(frz_exit_rate),
            'dry_avg': float(avg_dry_bins),
            'dry_monthly_rate': float(tariff.dry_storage or 0),
            'dry_storage_cost': float(dry_storage_cost),
            'dry_entry_rate': float(dry_entry_rate),
            'dry_exit_rate': float(dry_exit_rate),
            'period_days': period_days,
            'entry_cost_total': float(entry_portion),
            'exit_cost_total': float(exit_portion),
            'per_pallet_cost': float(per_pallet_cost),
            'actual_entries': entries_count,
            'actual_exits': exits_count,
            'actual_picking': picking_count,
            'note': 'Movimientos reales ignorados. Se cobra entrada/salida sobre promedio con 80%. No se cobra picking.'
        }

    # Calculate total billing
    billing_data['total_billing'] = (
        billing_data['storage']['total_cost'] +
        billing_data['entries']['total_cost'] +
        billing_data['exits']['total_cost'] +
        billing_data['picking']['total_cost'] +
        billing_data['additional_services']['total_cost']
    )

    # Per-pallet cost for reference
    if average_pallets > 0:
        billing_data['cost_per_pallet'] = float(
            billing_data['total_billing'] / average_pallets
        )
    else:
        billing_data['cost_per_pallet'] = 0.0

    return billing_data


def calculate_client_monthly_history(client_id, months=12):
    """
    Calculate monthly billing history for a client.
    Returns billing data for each of the last N months.
    """
    try:
        client = Client.objects.select_related('tariff').get(id=client_id)
    except Client.DoesNotExist:
        return None

    now = timezone.now()
    monthly_history = []

    for i in range(months):
        # Calculate for each month going backwards
        month_date = add_months(now, -i)
        month_start = month_date.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0)
        month_end = add_months(month_start, 1) - timedelta(seconds=1)

        # Calculate billing for this month
        month_billing = calculate_client_billing(
            client_id, month_start, month_end)

        if month_billing:
            monthly_history.append({
                'month': month_start.strftime('%Y-%m'),
                'month_name': month_start.strftime('%B %Y'),
                'total_billing': float(month_billing['total_billing']),
                'storage_cost': float(month_billing['storage']['total_cost']),
                'entries_cost': float(month_billing['entries']['total_cost']),
                'exits_cost': float(month_billing['exits']['total_cost']),
                'picking_cost': float(month_billing['picking']['total_cost']),
                'services_cost': float(month_billing['additional_services']['total_cost']),
            })

    # Reverse to show oldest first
    monthly_history.reverse()

    return {
        'client_id': client.id,
        'client_name': client.name,
        'months': monthly_history
    }


def calculate_general_billing_summary(start_date=None, end_date=None, workspace=None):
    """
    Calculate general billing summary for all clients within a date range.
    Returns aggregated data for dashboard.

    If workspace is provided, only clients of that owner are included.
    """
    from apps.storage.models import Position

    clients = Client.objects.filter(is_active=True).select_related('tariff')
    if workspace is not None:
        clients = clients.filter(workspace=workspace)

    positions_qs = Position.objects.filter(
        cava__is_virtual=False,
        is_active=True,
    )
    if workspace is not None:
        positions_qs = positions_qs.filter(cava__storage__workspace=workspace)
    total_positions = positions_qs.count()

    summary = {
        'total_clients': clients.count(),
        'total_billing': Decimal('0.00'),
        'total_storage_cost': Decimal('0.00'),
        'total_entries_cost': Decimal('0.00'),
        'total_exits_cost': Decimal('0.00'),
        'total_picking_cost': Decimal('0.00'),
        'total_additional_services': Decimal('0.00'),
        'total_bins': 0,
        'total_average_pallets': 0,
        'total_positions': total_positions,
        'total_entries': 0,
        'total_exits': 0,
        'total_picking': 0,
        'clients_summary': []
    }

    for client in clients:
        client_billing = calculate_client_billing(
            client.id, start_date, end_date)

        if client_billing:
            summary['total_billing'] += client_billing['total_billing']
            summary['total_storage_cost'] += client_billing['storage']['total_cost']
            summary['total_entries_cost'] += client_billing['entries']['total_cost']
            summary['total_exits_cost'] += client_billing['exits']['total_cost']
            summary['total_picking_cost'] += client_billing['picking']['total_cost']
            summary['total_additional_services'] += client_billing['additional_services']['total_cost']
            summary['total_bins'] += client_billing['storage']['bins_count']
            summary['total_average_pallets'] += client_billing.get('average_pallets', 0)
            summary['total_entries'] += client_billing['entries']['count']
            summary['total_exits'] += client_billing['exits']['count']
            summary['total_picking'] += client_billing['picking']['count']

            # Add client summary
            summary['clients_summary'].append({
                'client_id': client.id,
                'client_name': client.name,
                'tariff_type': client_billing.get('tariff_type', 'dynamic'),
                'total_billing': float(client_billing['total_billing']),
                'storage_cost': float(client_billing['storage']['total_cost']),
                'bins_count': client_billing['storage']['bins_count'],
                'entries_cost': float(client_billing['entries']['total_cost']),
                'entries_count': client_billing['entries']['count'],
                'exits_cost': float(client_billing['exits']['total_cost']),
                'exits_count': client_billing['exits']['count'],
                'picking_cost': float(client_billing['picking']['total_cost']),
                'picking_count': client_billing['picking']['count'],
                'cost_per_pallet': client_billing.get('cost_per_pallet', 0),
                'average_pallets': client_billing.get('average_pallets', 0)
            })

    # Sort clients by total billing (descending)
    summary['clients_summary'].sort(
        key=lambda x: x['total_billing'], reverse=True)

    return summary


def calculate_daily_inventory_average(client_id, start_date, end_date, storage_type='all'):
    """
    Calcula el promedio diario de inventario almacenado para un cliente.

    Para cada día en el rango:
    - Cuenta los bins que estaban ALMACENADOS ese día
    - Suma las unidades de producto en esos bins

    Un bin estaba almacenado un día específico si:
    - Su status fue ALMACENADO en algún momento antes o durante ese día
    - Y no fue despachado completamente antes de ese día

    Args:
        client_id: ID del cliente
        start_date: Fecha de inicio
        end_date: Fecha de fin
        storage_type: Tipo de almacenamiento ('all', 'refrigerado', 'congelado', 'seco')

    Returns:
        dict: {
            'client_id': int,
            'client_name': str,
            'start_date': str,
            'end_date': str,
            'total_days': int,
            'average_bins': float,
            'average_units': float,
            'daily_data': [
                {
                    'date': str,
                    'bins_count': int,
                    'units_count': int
                },
                ...
            ]
        }
    """
    from apps.inventory.models import BinProduct

    try:
        client = Client.objects.get(id=client_id)
    except Client.DoesNotExist:
        return None

    # Normalizar fechas
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()

    # Generar lista de días
    days = []
    current_day = start_date
    while current_day <= end_date:
        days.append(current_day)
        current_day += timedelta(days=1)

    if not days:
        return {
            'client_id': client.id,
            'client_name': client.name,
            'start_date': str(start_date),
            'end_date': str(end_date),
            'total_days': 0,
            'average_bins': 0,
            'average_units': 0,
            'daily_data': []
        }

    # Obtener todos los bins del cliente que pudieron estar almacenados en el rango
    # Un bin pudo estar almacenado si:
    # - Fue creado antes o durante el periodo (created_at <= end_date)
    # - Y no fue despachado antes del inicio (dispatched_date is null OR dispatched_date >= start_date)
    bins_query = Bin.objects.filter(
        client=client,
        is_active=True,
        status__in=[BinStatus.ALMACENADO, BinStatus.DESPACHADO],
        created_at__date__lte=end_date
    ).filter(
        Q(dispatched_date__isnull=True) | Q(
            dispatched_date__date__gte=start_date)
    ).select_related('entry', 'position__cava').prefetch_related('bin_products')

    bins = list(bins_query)

    # Para cada día, calcular bins por tipo de almacenamiento
    daily_data = []
    total_bins = 0
    total_units = 0
    total_refrigerated = 0
    total_frozen = 0
    total_dry = 0

    # Mapeo de storage_type a condiciones de entry (usar entry.condition, no cava.condition)
    # Esto asegura consistencia con generate_billing_report_excel
    storage_type_map = {
        'refrigerado': Conditions.REFRIGERATED,
        'refrigerated': Conditions.REFRIGERATED,
        'congelado': Conditions.FROZEN,
        'frozen': Conditions.FROZEN,
        'seco': Conditions.DRY,
        'dry': Conditions.DRY
    }

    for day in days:
        day_bins = 0
        day_units = 0
        day_refrigerated = 0
        day_frozen = 0
        day_dry = 0

        for bin in bins:
            # El bin estaba almacenado ese día si:
            # 1. Fue creado (almacenado) en o antes de ese día
            bin_created_date = bin.created_at.date()
            if bin_created_date > day:
                continue

            # 2. No fue despachado antes de ese día
            if bin.dispatched_date:
                dispatched_date = bin.dispatched_date.date()
                if dispatched_date < day:
                    continue

            # Determinar tipo de almacenamiento según entry.condition
            # (igual que en generate_billing_report_excel)
            entry_condition = None
            if bin.entry:
                entry_condition = bin.entry.condition

            # Filtrar por tipo de almacenamiento si se especificó
            if storage_type != 'all':
                expected_condition = storage_type_map.get(storage_type)
                if entry_condition != expected_condition:
                    continue

            # El bin estaba almacenado ese día y cumple el filtro
            day_bins += 1

            # Contar por tipo usando entry.condition
            if entry_condition == Conditions.FROZEN:
                day_frozen += 1
            elif entry_condition == Conditions.DRY:
                day_dry += 1
            elif entry_condition == Conditions.REFRIGERATED:
                day_refrigerated += 1

            # Sumar unidades de los productos del bin
            for bep in bin.bin_products.all():
                day_units += bep.quantity or 0

        daily_data.append({
            'date': str(day),
            'bins_count': day_bins,
            'units_count': day_units,
            'refrigerated_count': day_refrigerated,
            'frozen_count': day_frozen,
            'dry_count': day_dry
        })

        total_bins += day_bins
        total_units += day_units
        total_refrigerated += day_refrigerated
        total_frozen += day_frozen
        total_dry += day_dry

    num_days = len(days)
    # Redondear promedios a enteros (.5 arriba, .4 abajo)
    average_bins = round(total_bins / num_days) if num_days > 0 else 0
    average_units = round(total_units / num_days) if num_days > 0 else 0

    # Calcular promedios por condición (también enteros)
    average_refrigerated = round(total_refrigerated / num_days) if num_days > 0 else 0
    average_frozen = round(total_frozen / num_days) if num_days > 0 else 0
    average_dry = round(total_dry / num_days) if num_days > 0 else 0

    # Calcular valor estimado basado en tarifa del cliente y PROMEDIO REDONDEADO
    tariff = client.tariff
    tariff_name = tariff.name if tariff else 'Sin tarifa'
    refrigerated_rate = float(tariff.refrigerated_storage) if tariff else 0
    frozen_rate = float(tariff.frozen_storage) if tariff else 0
    dry_rate = float(tariff.dry_storage) if tariff and tariff.dry_storage else 0

    # Valor = (Promedio refrigerado × Tarifa) + (Promedio congelado × Tarifa) + (Promedio seco × Tarifa)
    refrigerated_value = round(average_refrigerated * refrigerated_rate, 2)
    frozen_value = round(average_frozen * frozen_rate, 2)
    dry_value = round(average_dry * dry_rate, 2)
    estimated_value = round(refrigerated_value + frozen_value + dry_value, 2)

    return {
        'client_id': client.id,
        'client_name': client.name,
        'start_date': str(start_date),
        'end_date': str(end_date),
        'storage_type': storage_type,
        'total_days': num_days,
        'average_bins': average_bins,
        'average_units': average_units,
        'total_bins_accumulated': total_bins,
        'total_units_accumulated': total_units,
        'tariff_name': tariff_name,
        'refrigerated_rate': refrigerated_rate,
        'frozen_rate': frozen_rate,
        'dry_rate': dry_rate,
        'total_refrigerated': total_refrigerated,
        'total_frozen': total_frozen,
        'total_dry': total_dry,
        'average_refrigerated': average_refrigerated,
        'average_frozen': average_frozen,
        'average_dry': average_dry,
        'refrigerated_value': refrigerated_value,
        'frozen_value': frozen_value,
        'dry_value': dry_value,
        'estimated_value': estimated_value,
        'daily_data': daily_data
    }


def export_inventory_average_excel(result_data):
    """
    Exporta los datos de promedio de inventario a Excel.

    Args:
        result_data: Diccionario con los datos del cálculo de promedios

    Returns:
        BytesIO: Buffer del archivo Excel
    """
    import xlwt
    from io import BytesIO

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Promedios de Inventario')

    # Estilos
    header_style = xlwt.XFStyle()
    header_font = xlwt.Font()
    header_font.bold = True
    header_font.colour_index = xlwt.Style.colour_map['white']
    header_style.font = header_font
    header_pattern = xlwt.Pattern()
    header_pattern.pattern = xlwt.Pattern.SOLID_PATTERN
    header_pattern.pattern_fore_colour = xlwt.Style.colour_map['dark_blue']
    header_style.pattern = header_pattern
    header_alignment = xlwt.Alignment()
    header_alignment.horz = xlwt.Alignment.HORZ_CENTER
    header_style.alignment = header_alignment

    title_style = xlwt.XFStyle()
    title_font = xlwt.Font()
    title_font.bold = True
    title_font.height = 280
    title_style.font = title_font

    info_style = xlwt.XFStyle()
    info_font = xlwt.Font()
    info_font.bold = True
    info_style.font = info_font

    number_style = xlwt.XFStyle()
    number_style.num_format_str = '#,##0'

    money_style = xlwt.XFStyle()
    money_style.num_format_str = '$#,##0.00'

    # Título
    ws.write_merge(
        0, 0, 0, 5, 'REPORTE DE PROMEDIOS DE INVENTARIO', title_style)

    # Información del reporte
    row = 2
    ws.write(row, 0, 'Cliente:', info_style)
    ws.write(row, 1, result_data.get('client_name', ''))
    row += 1
    ws.write(row, 0, 'Período:', info_style)
    ws.write(
        row, 1, f"{result_data.get('start_date', '')} al {result_data.get('end_date', '')}")
    row += 1
    storage_type = result_data.get('storage_type', 'all')
    storage_type_label = 'Todas' if storage_type == 'all' else storage_type.capitalize()
    ws.write(row, 0, 'Tipo:', info_style)
    ws.write(row, 1, storage_type_label)
    row += 1
    ws.write(row, 0, 'Días analizados:', info_style)
    ws.write(row, 1, result_data.get('total_days', 0))
    row += 1
    ws.write(row, 0, 'Promedio diario:', info_style)
    ws.write(row, 1, round(result_data.get('average_bins', 0)), number_style)
    row += 1
    ws.write(row, 0, 'Valor estimado:', info_style)
    ws.write(row, 1, result_data.get('estimated_value', 0), money_style)
    row += 1
    ws.write(row, 0, 'Tarifa:', info_style)
    ws.write(row, 1, result_data.get('tariff_name', ''))

    # Espacio antes de la tabla
    row += 2

    # Headers de la tabla
    headers = ['Fecha', 'Total Pallets']
    total_refrigerated = result_data.get('total_refrigerated', 0)
    total_frozen = result_data.get('total_frozen', 0)
    total_dry = result_data.get('total_dry', 0)

    if total_refrigerated > 0:
        headers.append('Refrigerado')
    if total_frozen > 0:
        headers.append('Congelado')
    if total_dry > 0:
        headers.append('Seco')

    for col, header in enumerate(headers):
        ws.write(row, col, header, header_style)
        ws.col(col).width = 4000

    row += 1

    # Datos diarios
    daily_data = result_data.get('daily_data', [])
    for day in daily_data:
        col = 0
        ws.write(row, col, day.get('date', ''))
        col += 1
        ws.write(row, col, day.get('bins_count', 0), number_style)
        col += 1
        if total_refrigerated > 0:
            ws.write(row, col, day.get('refrigerated_count', 0), number_style)
            col += 1
        if total_frozen > 0:
            ws.write(row, col, day.get('frozen_count', 0), number_style)
            col += 1
        if total_dry > 0:
            ws.write(row, col, day.get('dry_count', 0), number_style)
        row += 1

    # Fila de promedios
    total_days = result_data.get('total_days', 1) or 1
    col = 0
    ws.write(row, col, 'PROMEDIO', info_style)
    col += 1
    ws.write(row, col, round(result_data.get('average_bins', 0)), number_style)
    col += 1
    if total_refrigerated > 0:
        ws.write(row, col, round(total_refrigerated / total_days), number_style)
        col += 1
    if total_frozen > 0:
        ws.write(row, col, round(total_frozen / total_days), number_style)
        col += 1
    if total_dry > 0:
        ws.write(row, col, round(total_dry / total_days), number_style)

    # Guardar en buffer
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return output


def generate_billing_report_excel(client_id, start_date, end_date):
    """
    Genera un reporte de facturación en Excel con 4 pestañas:
    1. Resumen de Facturación - Totales por condición
    2. Movimientos In - Detalle de entradas
    3. Movimientos Out - Detalle de salidas
    4. Promedio de Almacenamiento - Datos diarios

    Args:
        client_id: ID del cliente
        start_date: Fecha de inicio
        end_date: Fecha de fin

    Returns:
        BytesIO: Buffer del archivo Excel
    """
    import xlwt
    from io import BytesIO
    from apps.inventory.models import BinProduct

    try:
        client = Client.objects.get(id=client_id)
    except Client.DoesNotExist:
        return None

    # Normalizar fechas
    if isinstance(start_date, str):
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
    elif isinstance(start_date, datetime):
        start_date = start_date.date()

    if isinstance(end_date, str):
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    elif isinstance(end_date, datetime):
        end_date = end_date.date()

    # Crear libro Excel
    wb = xlwt.Workbook(encoding='utf-8')

    # ===== ESTILOS =====
    # Título principal
    title_style = xlwt.XFStyle()
    title_font = xlwt.Font()
    title_font.bold = True
    title_font.height = 280
    title_style.font = title_font

    # Header azul claro (para encabezados de sección)
    header_blue_style = xlwt.XFStyle()
    header_blue_font = xlwt.Font()
    header_blue_font.bold = True
    header_blue_style.font = header_blue_font
    header_blue_pattern = xlwt.Pattern()
    header_blue_pattern.pattern = xlwt.Pattern.SOLID_PATTERN
    header_blue_pattern.pattern_fore_colour = xlwt.Style.colour_map['light_blue']
    header_blue_style.pattern = header_blue_pattern
    header_blue_alignment = xlwt.Alignment()
    header_blue_alignment.horz = xlwt.Alignment.HORZ_CENTER
    header_blue_style.alignment = header_blue_alignment

    # Header amarillo (para títulos de condición)
    header_yellow_style = xlwt.XFStyle()
    header_yellow_font = xlwt.Font()
    header_yellow_font.bold = True
    header_yellow_style.font = header_yellow_font
    header_yellow_pattern = xlwt.Pattern()
    header_yellow_pattern.pattern = xlwt.Pattern.SOLID_PATTERN
    header_yellow_pattern.pattern_fore_colour = xlwt.Style.colour_map['yellow']
    header_yellow_style.pattern = header_yellow_pattern

    # Header verde (para tabla de datos)
    header_green_style = xlwt.XFStyle()
    header_green_font = xlwt.Font()
    header_green_font.bold = True
    header_green_font.colour_index = xlwt.Style.colour_map['white']
    header_green_style.font = header_green_font
    header_green_pattern = xlwt.Pattern()
    header_green_pattern.pattern = xlwt.Pattern.SOLID_PATTERN
    header_green_pattern.pattern_fore_colour = xlwt.Style.colour_map['sea_green']
    header_green_style.pattern = header_green_pattern
    header_green_alignment = xlwt.Alignment()
    header_green_alignment.horz = xlwt.Alignment.HORZ_CENTER
    header_green_style.alignment = header_green_alignment

    # Estilo normal
    normal_style = xlwt.XFStyle()

    # Estilo numérico
    number_style = xlwt.XFStyle()
    number_style.num_format_str = '#,##0'
    number_alignment = xlwt.Alignment()
    number_alignment.horz = xlwt.Alignment.HORZ_CENTER
    number_style.alignment = number_alignment

    # Estilo bold
    bold_style = xlwt.XFStyle()
    bold_font = xlwt.Font()
    bold_font.bold = True
    bold_style.font = bold_font

    # ===== OBTENER DATOS =====
    # IMPORTANTE: Limitar end_date a hoy para no incluir datos futuros
    # Usar la misma lógica que calculate_client_billing para consistencia
    today = timezone.now().date()
    effective_end_date = min(end_date, today)

    # Obtener bins de entrada del cliente en el período
    # Usar filtros de fecha directamente (igual que calculate_client_billing)
    bins_created = Bin.objects.filter(
        client=client,
        is_active=True,
        created_at__gte=start_date,
        created_at__lte=effective_end_date
    ).select_related('entry', 'entry__storage').prefetch_related('bin_products__entry_product__product')

    # Bins que fueron despachados en el período
    bins_dispatched = Bin.objects.filter(
        client=client,
        is_active=True,
        dispatched_date__gte=start_date,
        dispatched_date__lte=effective_end_date
    ).select_related('entry', 'entry__storage').prefetch_related('bin_products__entry_product__product')

    # Calcular promedios diarios usando la misma función que calculate_client_billing
    # Esto asegura consistencia entre ambas vistas
    avg_all = calculate_daily_inventory_average(client_id, start_date, effective_end_date, 'all')
    avg_refrigerated_data = calculate_daily_inventory_average(client_id, start_date, effective_end_date, 'refrigerated')
    avg_frozen_data = calculate_daily_inventory_average(client_id, start_date, effective_end_date, 'frozen')
    avg_dry_data = calculate_daily_inventory_average(client_id, start_date, effective_end_date, 'dry')

    # Obtener datos del promedio general
    daily_data = avg_all.get('daily_data', []) if avg_all else []
    num_days = avg_all.get('total_days', 1) if avg_all else 1

    # Promedios de bins/pallets
    avg_total = round(avg_all.get('average_bins', 0)) if avg_all else 0
    avg_refrigerated = round(avg_refrigerated_data.get('average_bins', 0)) if avg_refrigerated_data else 0
    avg_frozen = round(avg_frozen_data.get('average_bins', 0)) if avg_frozen_data else 0
    avg_dry = round(avg_dry_data.get('average_bins', 0)) if avg_dry_data else 0

    # Promedios de cajas (PICKING) - usar average_units de cada condición
    avg_boxes = round(avg_all.get('average_units', 0)) if avg_all else 0
    avg_refrigerated_boxes = round(avg_refrigerated_data.get('average_units', 0)) if avg_refrigerated_data else 0
    avg_frozen_boxes = round(avg_frozen_data.get('average_units', 0)) if avg_frozen_data else 0
    avg_dry_boxes = round(avg_dry_data.get('average_units', 0)) if avg_dry_data else 0

    # Obtener tarifa del cliente
    tariff = client.tariff
    has_picking_tariff = tariff.has_picking_tariff if tariff else False

    # Contar entradas y salidas por condición
    frozen_in = bins_created.filter(entry__condition=Conditions.FROZEN).count()
    frozen_out = bins_dispatched.filter(
        entry__condition=Conditions.FROZEN).count()
    refrigerated_in = bins_created.filter(
        entry__condition=Conditions.REFRIGERATED).count()
    refrigerated_out = bins_dispatched.filter(
        entry__condition=Conditions.REFRIGERATED).count()
    dry_in = bins_created.filter(entry__condition=Conditions.DRY).count()
    dry_out = bins_dispatched.filter(entry__condition=Conditions.DRY).count()

    # PISO: Contar bins de entradas a piso (floor entries) por condición
    frozen_piso = bins_created.filter(
        entry__condition=Conditions.FROZEN,
        entry__in_type=InTypes.PISO
    ).count()
    refrigerated_piso = bins_created.filter(
        entry__condition=Conditions.REFRIGERATED,
        entry__in_type=InTypes.PISO
    ).count()
    dry_piso = bins_created.filter(
        entry__condition=Conditions.DRY,
        entry__in_type=InTypes.PISO
    ).count()

    # PALLET: Contar bins de entradas a piso (floor entries) por condición
    frozen_pallet = bins_created.filter(
        entry__condition=Conditions.FROZEN,
        entry__in_type=InTypes.PISO
    ).count()
    refrigerated_pallet = bins_created.filter(
        entry__condition=Conditions.REFRIGERATED,
        entry__in_type=InTypes.PISO
    ).count()
    dry_pallet = bins_created.filter(
        entry__condition=Conditions.DRY,
        entry__in_type=InTypes.PISO
    ).count()

    # PICKING: 0 si no tiene tarifa picking, sino promedio de cajas diarias
    frozen_picking = 0 if not has_picking_tariff else avg_frozen_boxes
    refrigerated_picking = 0 if not has_picking_tariff else avg_refrigerated_boxes
    dry_picking = 0 if not has_picking_tariff else avg_dry_boxes

    # STRECH: Calcular para entradas a piso y salidas con picking
    # Entradas a piso por condición
    frozen_strech_in = bins_created.filter(
        entry__condition=Conditions.FROZEN,
        entry__in_type=InTypes.PISO
    ).count()
    refrigerated_strech_in = bins_created.filter(
        entry__condition=Conditions.REFRIGERATED,
        entry__in_type=InTypes.PISO
    ).count()
    dry_strech_in = bins_created.filter(
        entry__condition=Conditions.DRY,
        entry__in_type=InTypes.PISO
    ).count()

    # Salidas con picking por condición
    # Si has_picking_tariff == True, contar TODOS los bins despachados (OUT)
    # Si has_picking_tariff == False, entonces salidas_picking = 0
    if has_picking_tariff:
        frozen_strech_out = frozen_out
        refrigerated_strech_out = refrigerated_out
        dry_strech_out = dry_out
    else:
        frozen_strech_out = 0
        refrigerated_strech_out = 0
        dry_strech_out = 0

    # STRECH total: (entradas a piso + salidas con picking) / 4
    frozen_strech = (frozen_strech_in + frozen_strech_out + 3) // 4
    refrigerated_strech = (refrigerated_strech_in + refrigerated_strech_out + 3) // 4
    dry_strech = (dry_strech_in + dry_strech_out + 3) // 4

    # ===== PESTAÑA 1: RESUMEN DE FACTURACIÓN =====
    ws1 = wb.add_sheet('Resumen de Facturacion')

    # Formato de fecha para el título (usa el mes de la fecha FIN)
    date_format = f"{start_date.strftime('%d-%m-%y')} AL {end_date.strftime('%d-%m-%y')}"
    month_name = end_date.strftime('%B').upper()

    # Título del período
    row = 0
    ws1.write_merge(row, row, 0, 1, f"{month_name} {date_format}", title_style)
    row += 2

    # Función para escribir sección de condición (sin colores de fondo)
    def write_condition_section(ws, start_row, condition_name, almacenaje, in_count, out_count,
                                picking, piso, strech, restiba, pallet):
        row = start_row
        # Título de condición
        ws.write_merge(row, row, 0, 1, condition_name, bold_style)
        ws.write(row, 2, 'TOTALES', bold_style)
        row += 1

        # ALMACENAJE
        ws.write(row, 0, 'ALMACENAJE', bold_style)
        ws.write(row, 2, almacenaje, number_style)
        row += 1

        # IN
        ws.write(row, 0, 'IN', bold_style)
        ws.write(row, 2, in_count, number_style)
        row += 1

        # OUT
        ws.write(row, 0, 'OUT', bold_style)
        ws.write(row, 2, out_count, number_style)
        row += 1

        # PICKING
        ws.write(row, 0, 'PICKING', bold_style)
        ws.write(row, 2, picking, number_style)
        row += 1

        # PISO
        ws.write(row, 0, 'PISO', bold_style)
        ws.write(row, 2, piso, number_style)
        row += 1

        # STRECH
        ws.write(row, 0, 'STRECH', bold_style)
        ws.write(row, 2, strech, number_style)
        row += 1

        # RE ESTIBA / ESTIBA
        ws.write(row, 0, 'RE ESTIBA / ESTIBA', bold_style)
        ws.write(row, 2, restiba, number_style)
        row += 1

        # PALLET
        ws.write(row, 0, 'PALLET', bold_style)
        ws.write(row, 2, pallet, number_style)
        row += 1

        return row + 1

    # Sección CONGELADO
    row = write_condition_section(
        ws1, row,
        f'{client.name.upper()} CONGELADO',
        avg_frozen,  # Almacenaje = promedio bins
        frozen_in,   # IN
        frozen_out,  # OUT
        frozen_picking,  # PICKING (0 si no tiene tarifa picking, sino promedio cajas)
        frozen_piso,  # PISO (bins de entradas a piso)
        frozen_strech,  # STRECH (entradas a piso + salidas picking) / 4
        0,  # RE ESTIBA/ESTIBA
        frozen_pallet  # PALLET (bins de entradas a piso)
    )

    row += 1

    # Sección REFRIGERADO
    row = write_condition_section(
        ws1, row,
        f'{client.name.upper()} REFRIGERADO',
        avg_refrigerated,  # Almacenaje
        refrigerated_in,  # IN
        refrigerated_out,  # OUT
        refrigerated_picking,  # PICKING
        refrigerated_piso,  # PISO
        refrigerated_strech,  # STRECH
        0,  # RE ESTIBA/ESTIBA
        refrigerated_pallet  # PALLET
    )

    row += 1

    # Sección SECO
    row = write_condition_section(
        ws1, row,
        f'{client.name.upper()} SECO',
        avg_dry,  # Almacenaje
        dry_in,  # IN
        dry_out,  # OUT
        dry_picking,  # PICKING
        dry_piso,  # PISO
        dry_strech,  # STRECH
        0,  # RE ESTIBA/ESTIBA
        dry_pallet  # PALLET
    )

    # Ajustar anchos de columna
    ws1.col(0).width = 6000
    ws1.col(1).width = 3000
    ws1.col(2).width = 3000

    # ===== PESTAÑA 2: MOVIMIENTOS IN =====
    ws2 = wb.add_sheet('Movimientos In')

    # Headers
    headers_in = ['Fecha de Creación', 'Código', 'Cliente', 'Entrada', 'Posición',
                  'SKU', 'Condición', 'Fecha de Vencimiento', 'Producto',
                  'Cantidad Total', 'Cantidad Reservada', 'Cantidad Disponible',
                  'UOM', 'Lote', 'Estado', 'Fecha de Despachado']

    for col, header in enumerate(headers_in):
        ws2.write(0, col, header, header_green_style)
        ws2.col(col).width = 4500

    # Datos
    row = 1
    for bin in bins_created:
        entry = bin.entry
        for bep in bin.bin_products.all():
            product = bep.entry_product.product if bep.entry_product else None
            ws2.write(row, 0, bin.created_at.strftime(
                '%d/%m/%Y') if bin.created_at else '')
            ws2.write(row, 1, bin.code or '')
            ws2.write(row, 2, client.name)
            ws2.write(row, 3, entry.code if entry else '')
            ws2.write(
                row, 4, bin.position.code if bin.position else '')
            ws2.write(row, 5, product.client_sku if product else '')

            # Condición
            condition_label = ''
            if entry and entry.condition:
                if entry.condition == Conditions.FROZEN:
                    condition_label = 'Congelado'
                elif entry.condition == Conditions.REFRIGERATED:
                    condition_label = 'Refrigerado'
                elif entry.condition == Conditions.DRY:
                    condition_label = 'Seco'
            ws2.write(row, 6, condition_label)

            ws2.write(row, 7, bep.expiration_date.strftime(
                '%d/%m/%Y') if bep.expiration_date else '')
            ws2.write(row, 8, product.name if product else '')
            ws2.write(row, 9, bep.quantity, number_style)
            ws2.write(row, 10, 0, number_style)  # Cantidad reservada
            ws2.write(row, 11, 0, number_style)  # Cantidad disponible
            ws2.write(row, 12, product.uom if product else '')
            ws2.write(row, 13, bep.batch or '')

            # Estado
            status_label = bin.status
            if bin.status == BinStatus.ALMACENADO:
                status_label = 'Almacenado'
            elif bin.status == BinStatus.DESPACHADO:
                status_label = 'Despachado'
            ws2.write(row, 14, status_label)

            ws2.write(row, 15, bin.dispatched_date.strftime(
                '%d/%m/%Y %H:%M') if bin.dispatched_date else 'N/A')
            row += 1

    # ===== PESTAÑA 3: MOVIMIENTOS OUT =====
    ws3 = wb.add_sheet('Movimientos Out')

    # Obtener salidas completadas en el período
    exits = Exit.objects.filter(
        client=client,
        created_at__gte=start_date,
        created_at__lte=effective_end_date
    ).select_related('branch').prefetch_related(
        'exit_product_groups__product',
        'exit_product_groups__exit_products__bin_product__bin__entry'
    )

    headers_out = ['Fecha de Creación', 'Código', 'Cliente', 'Sucursal',
                   'Fecha de Salida', 'Estado', 'Producto', 'Condición',
                   'Cantidad', 'UOM', 'Pallets de Picking']

    for col, header in enumerate(headers_out):
        ws3.write(0, col, header, header_green_style)
        ws3.col(col).width = 4500

    row = 1
    for exit_order in exits:
        for group in exit_order.exit_product_groups.all():
            product = group.product
            picking_count = group.exit_products.filter(is_picking=True).count()

            # Obtener condición del primer exit_product
            condition_label = ''
            first_ep = group.exit_products.first()
            if first_ep and first_ep.bin_product and first_ep.bin_product.bin:
                entry = first_ep.bin_product.bin.entry
                if entry and entry.condition:
                    if entry.condition == Conditions.FROZEN:
                        condition_label = 'Congelado'
                    elif entry.condition == Conditions.REFRIGERATED:
                        condition_label = 'Refrigerado'
                    elif entry.condition == Conditions.DRY:
                        condition_label = 'Seco'

            ws3.write(row, 0, exit_order.created_at.strftime(
                '%d/%m/%Y %H:%M') if exit_order.created_at else '')
            ws3.write(row, 1, exit_order.code or '')
            ws3.write(row, 2, client.name)
            ws3.write(
                row, 3, exit_order.branch.name if exit_order.branch else 'N/A')
            ws3.write(row, 4, exit_order.completed_at.strftime(
                '%d/%m/%Y') if hasattr(exit_order, 'completed_at') and exit_order.completed_at else '')
            ws3.write(row, 5, 'Completada' if exit_order.status ==
                      'completado' else exit_order.status)
            ws3.write(row, 6, product.name if product else '')
            ws3.write(row, 7, condition_label)
            ws3.write(row, 8, group.quantity, number_style)
            ws3.write(row, 9, product.uom if product else '')
            ws3.write(row, 10, picking_count, number_style)
            row += 1

    # ===== PESTAÑA 4: PROMEDIO DE ALMACENAMIENTO =====
    ws4 = wb.add_sheet('Promedio de Almacenamiento')

    # Título
    ws4.write_merge(
        0, 0, 0, 5, 'REPORTE DE PROMEDIOS DE INVENTARIO', title_style)

    # Información del reporte
    row = 2
    ws4.write(row, 0, 'Cliente:', bold_style)
    ws4.write(row, 1, client.name)
    row += 1
    ws4.write(row, 0, 'Período:', bold_style)
    ws4.write(row, 1, f"{start_date} al {end_date}")
    row += 1
    ws4.write(row, 0, 'Tipo:', bold_style)
    ws4.write(row, 1, 'Todas')
    row += 1
    ws4.write(row, 0, 'Días analizados:', bold_style)
    ws4.write(row, 1, num_days)
    row += 1
    ws4.write(row, 0, 'Promedio diario:', bold_style)
    ws4.write(row, 1, avg_total)
    row += 1
    ws4.write(row, 0, 'Valor estimado:', bold_style)
    ws4.write(row, 1, '')  # Se puede calcular si se necesita
    row += 1
    ws4.write(row, 0, 'Tarifa:', bold_style)
    ws4.write(row, 1, client.tariff.name if client.tariff else 'Sin tarifa')
    row += 2

    # Headers de la tabla
    headers_avg = ['Fecha', 'Total Pallets', 'Refrigerado', 'Congelado', 'Seco']
    for col, header in enumerate(headers_avg):
        ws4.write(row, col, header, header_green_style)
        ws4.col(col).width = 4000
    row += 1

    # Datos diarios
    for day_data in daily_data:
        ws4.write(row, 0, day_data['date'])
        ws4.write(row, 1, day_data['bins_count'], number_style)
        ws4.write(row, 2, day_data['refrigerated_count'], number_style)
        ws4.write(row, 3, day_data['frozen_count'], number_style)
        ws4.write(row, 4, day_data['dry_count'], number_style)
        row += 1

    # Fila de promedio
    ws4.write(row, 0, 'PROMEDIO', bold_style)
    ws4.write(row, 1, avg_total, number_style)
    ws4.write(row, 2, avg_refrigerated, number_style)
    ws4.write(row, 3, avg_frozen, number_style)
    ws4.write(row, 4, avg_dry, number_style)

    # Guardar en buffer
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return output


def generate_invoice_line_items(billing_data):
    """
    Genera las líneas de detalle del comprobante a partir de los datos de billing.
    """
    line_items = []
    item_no = 1
    
    # Almacenamiento
    if billing_data.get('storage', {}).get('total_cost', 0) > 0:
        storage = billing_data['storage']
        tariff_type = billing_data.get('tariff_type', 'dynamic')
        description = f"ALMACENAMIENTO PRODUCTO {'ESTATICO' if tariff_type == 'static' else 'DINAMICO'}"
        
        # Calcular cantidad (promedio de bins)
        quantity = storage.get('bins_count', 0)
        unit_price = float(storage.get('total_cost', 0)) / quantity if quantity > 0 else 0
        
        line_items.append({
            'no': item_no,
            'description': description,
            'quantity': quantity,
            'unit': 'und',
            'unit_price': round(unit_price),  # Redondear a entero
            'discount': 0,
            'amount': float(storage.get('total_cost', 0)),
            'tax': 0,
            'total': float(storage.get('total_cost', 0))
        })
        item_no += 1
    
    # Entradas
    entries_cost = float(billing_data.get('entries', {}).get('total_cost', 0))
    if entries_cost > 0:
        entries_count = billing_data.get('entries', {}).get('count', 0)
        unit_price = entries_cost / entries_count if entries_count > 0 else 0
        
        line_items.append({
            'no': item_no,
            'description': 'ENTRADAS DE MERCANCIA',
            'quantity': entries_count,
            'unit': 'und',
            'unit_price': round(unit_price),  # Redondear a entero
            'discount': 0,
            'amount': entries_cost,
            'tax': 0,
            'total': entries_cost
        })
        item_no += 1
    
    # Salidas
    exits_cost = float(billing_data.get('exits', {}).get('total_cost', 0))
    if exits_cost > 0:
        exits_count = billing_data.get('exits', {}).get('count', 0)
        unit_price = exits_cost / exits_count if exits_count > 0 else 0
        
        line_items.append({
            'no': item_no,
            'description': 'SALIDAS DE MERCANCIA',
            'quantity': exits_count,
            'unit': 'und',
            'unit_price': round(unit_price),  # Redondear a entero
            'discount': 0,
            'amount': exits_cost,
            'tax': 0,
            'total': exits_cost
        })
        item_no += 1
    
    # Picking
    picking_cost = float(billing_data.get('picking', {}).get('total_cost', 0))
    if picking_cost > 0:
        picking_count = billing_data.get('picking', {}).get('count', 0)
        unit_price = picking_cost / picking_count if picking_count > 0 else 0
        
        line_items.append({
            'no': item_no,
            'description': 'SERVICIO DE PICKING',
            'quantity': picking_count,
            'unit': 'und',
            'unit_price': round(unit_price),  # Redondear a entero
            'discount': 0,
            'amount': picking_cost,
            'tax': 0,
            'total': picking_cost
        })
        item_no += 1
    
    # Servicios adicionales
    additional = billing_data.get('additional_services', {})
    
    # WRAP/STRECH unificado (entradas + salidas)
    wrap_cost = float(additional.get('wrap', {}).get('cost', 0))
    wrap_count = additional.get('wrap', {}).get('count', 0)
    strech_cost = float(additional.get('strech', {}).get('cost', 0))
    strech_count = additional.get('strech', {}).get('count', 0)
    
    total_wrap_cost = wrap_cost + strech_cost
    total_wrap_count = wrap_count + strech_count
    
    if total_wrap_cost > 0:
        unit_price = total_wrap_cost / total_wrap_count if total_wrap_count > 0 else 0
        
        line_items.append({
            'no': item_no,
            'description': 'SERVICIO DE WRAP/STRECH',
            'quantity': total_wrap_count,
            'unit': 'und',
            'unit_price': round(unit_price),
            'discount': 0,
            'amount': total_wrap_cost,
            'tax': 0,
            'total': total_wrap_cost
        })
        item_no += 1
    
    # RESTIBA
    if float(additional.get('restiba', {}).get('cost', 0)) > 0:
        restiba_cost = float(additional['restiba']['cost'])
        restiba_count = additional['restiba'].get('count', 0)
        unit_price = restiba_cost / restiba_count if restiba_count > 0 else 0
        
        line_items.append({
            'no': item_no,
            'description': 'SERVICIO DE RESTIBA',
            'quantity': restiba_count,
            'unit': 'und',
            'unit_price': round(unit_price),
            'discount': 0,
            'amount': restiba_cost,
            'tax': 0,
            'total': restiba_cost
        })
        item_no += 1
    
    # PALLET ADICIONAL
    if float(additional.get('added_pallet', {}).get('cost', 0)) > 0:
        added_pallet_cost = float(additional['added_pallet']['cost'])
        added_pallet_count = additional['added_pallet'].get('count', 0)
        unit_price = added_pallet_cost / added_pallet_count if added_pallet_count > 0 else 0
        
        line_items.append({
            'no': item_no,
            'description': 'PALLET ADICIONAL',
            'quantity': added_pallet_count,
            'unit': 'und',
            'unit_price': round(unit_price),
            'discount': 0,
            'amount': added_pallet_cost,
            'tax': 0,
            'total': added_pallet_cost
        })
        item_no += 1
    
    return line_items


def serialize_billing_data(billing_data):
    """
    Serializa los datos de billing para guardar en JSON (convierte Decimals a float).
    """
    def convert_decimal(obj):
        if isinstance(obj, Decimal):
            return float(obj)
        elif isinstance(obj, dict):
            return {k: convert_decimal(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_decimal(item) for item in obj]
        return obj
    
    return convert_decimal(billing_data)


def _workspace_logo_image(workspace, fallback_style, size=1.1):
    """Compat: delega en helper compartido."""
    from apps.common.utils.pdf_branding import workspace_logo_flowable
    return workspace_logo_flowable(workspace, fallback_style, size=size)


def generate_invoice_pdf(invoice, billing_data):
    """
    Genera el PDF del comprobante de factura electrónica.
    Diseño basado en el formato DGI de Panamá.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
    from reportlab.pdfgen import canvas
    from io import BytesIO
    
    # Ancho disponible para contenido (letter = 8.5", márgenes = 0.5" cada lado = 7.5")
    CONTENT_WIDTH = 7.5 * inch
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.5*inch,
        leftMargin=0.5*inch,
        topMargin=0.3*inch,
        bottomMargin=0.7*inch  # Más espacio para el pie de página
    )
    
    # Función para el pie de página
    def add_footer(canvas_obj, doc):
        canvas_obj.saveState()
        # Línea separadora
        canvas_obj.setStrokeColor(colors.black)
        canvas_obj.setLineWidth(0.5)
        canvas_obj.line(0.5*inch, 0.55*inch, 8*inch, 0.55*inch)
        
        # Página X de Y (izquierda)
        canvas_obj.setFont('Helvetica', 7)
        canvas_obj.drawString(0.5*inch, 0.4*inch, f"Página {doc.page} de 1")
        
        # Texto central
        canvas_obj.setFont('Helvetica', 6)
        center_text = "Documento validado por Soluciones de Punto de Venta, S.A. con R.U.C. 1837393-1-711438, es Proveedor Autorizado Calificado,"
        canvas_obj.drawCentredString(4.25*inch, 0.35*inch, center_text)
        canvas_obj.drawCentredString(4.25*inch, 0.22*inch, "Resolución No. 201-9720 de 12/10/2021.")
        
        # Número de documento (derecha)
        canvas_obj.setFont('Helvetica', 7)
        canvas_obj.drawRightString(8*inch, 0.4*inch, f"0000-001-{invoice.number}")
        
        canvas_obj.restoreState()
    
    styles = getSampleStyleSheet()
    
    # Estilos personalizados
    dgi_title_style = ParagraphStyle(
        'DGITitle',
        parent=styles['Normal'],
        fontSize=14,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold',
        textColor=colors.Color(0, 0.4, 0.6),  # Azul oscuro
        spaceAfter=2
    )
    
    dgi_subtitle_style = ParagraphStyle(
        'DGISubtitle',
        parent=styles['Normal'],
        fontSize=11,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold',
        textColor=colors.Color(0, 0.4, 0.6),
        spaceAfter=2
    )
    
    dgi_type_style = ParagraphStyle(
        'DGIType',
        parent=styles['Normal'],
        fontSize=10,
        alignment=TA_CENTER,
        textColor=colors.Color(0, 0.4, 0.6),
    )
    
    normal_style = ParagraphStyle(
        'NormalCustom',
        parent=styles['Normal'],
        fontSize=8,
        leading=10
    )
    
    small_style = ParagraphStyle(
        'Small',
        parent=styles['Normal'],
        fontSize=7,
        leading=9
    )
    
    bold_style = ParagraphStyle(
        'BoldCustom',
        parent=styles['Normal'],
        fontSize=8,
        fontName='Helvetica-Bold',
        leading=10
    )
    
    section_title_style = ParagraphStyle(
        'SectionTitle',
        parent=styles['Normal'],
        fontSize=8,
        fontName='Helvetica-Bold',
        leading=10
    )
    
    elements = []

    workspace = getattr(getattr(invoice, 'client', None), 'workspace', None)

    # === ENCABEZADO ===
    logo_img = _workspace_logo_image(workspace, bold_style)
    
    # Título DGI (centro)
    dgi_content = [
        Paragraph('DGI', dgi_title_style),
        Paragraph('Comprobante auxiliar de factura electrónica', dgi_subtitle_style),
        Paragraph('Factura de Operación Interna', dgi_type_style),
    ]
    
    # Generar código QR con información completa del comprobante
    import qrcode
    qr = qrcode.QRCode(
        version=4,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    # Datos del QR: URL de verificación con parámetros del comprobante
    emisor_name = (workspace.legal_name or workspace.name) if workspace else 'N/A'
    emisor_ruc = workspace.rif if workspace and workspace.rif else 'N/A'
    support_email = workspace.support_email if workspace and workspace.support_email else 'N/A'
    emisor_phone = workspace.phone if workspace and workspace.phone else ''

    qr_data = f"""https://dgi-fep.mef.gob.pa/Consultas/FacturasPorCUFE
Número: {invoice.number}
Emisor RUC: {emisor_ruc}
Receptor RUC: {invoice.client_ruc}
Fecha: {invoice.created_at.strftime('%d-%m-%Y')}
Total: {invoice.total}"""
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_img_pil = qr.make_image(fill_color="black", back_color="white")
    
    # Convertir PIL Image a BytesIO para ReportLab
    qr_buffer = BytesIO()
    qr_img_pil.save(qr_buffer, format='PNG')
    qr_buffer.seek(0)
    qr_img = Image(qr_buffer, width=1.3*inch, height=1.3*inch)
    
    header_data = [[logo_img, dgi_content, qr_img]]
    header_table = Table(header_data, colWidths=[1.2*inch, 5.1*inch, 1.2*inch])  # Total: CONTENT_WIDTH
    header_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
        ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 0.15*inch))
    
    # === DATOS DEL EMISOR (sin recuadro, texto simple) ===
    phone_line = f'<br/><b>Teléfono:</b> {emisor_phone}' if emisor_phone else ''
    emisor_text = f"""<b>Emisor:</b>  {emisor_name}<br/>
<b>RUC:</b> {emisor_ruc}{phone_line}"""
    elements.append(Paragraph(emisor_text, normal_style))
    elements.append(Spacer(1, 0.15*inch))
    
    # === DATOS DEL RECEPTOR (sin recuadro, texto simple) ===
    receptor_text = f"""<b>Tipo de Receptor:</b>  Contribuyente<br/>
<b>Cliente:</b>  {invoice.client.name}<br/>
<b>RUC/Cédula:</b> {invoice.client_ruc}    <b>DV:</b> {invoice.client_dv}<br/>
<b>Dirección:</b>  {invoice.client_address if invoice.client_address else 'N/A'}"""
    elements.append(Paragraph(receptor_text, normal_style))
    elements.append(Spacer(1, 0.15*inch))
    
    # === DATOS DEL COMPROBANTE (en recuadro) ===
    fecha_emision = invoice.created_at.strftime('%d-%m-%Y')
    
    # Columna izquierda: Número, Fecha, Punto
    left_col = [
        Paragraph(f'<b>Número:</b> {invoice.number}', normal_style),
        Paragraph(f'<b>Fecha de Emisión:</b> {fecha_emision}', normal_style),
        Paragraph(f'<b>Punto de Facturación:</b> {invoice.billing_point}', normal_style),
    ]
    
    # Columna derecha: Info CUFE (placeholder para futuro)
    right_col = [
        Paragraph('<font size="6">Consulte por la clave de acceso en: https://dgi-fep.mef.gob.pa/Consultas/FacturasPorCUFE</font>', small_style),
        Paragraph('<font size="6"><b>CUFE:</b> (Pendiente de configuración)</font>', small_style),
        Paragraph('<font size="6"><b>Protocolo de autorización:</b> (Pendiente)</font>', small_style),
    ]
    
    comprobante_data = [
        [left_col, right_col]
    ]
    comprobante_table = Table(comprobante_data, colWidths=[2.5*inch, 5*inch])  # Total: CONTENT_WIDTH
    comprobante_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(comprobante_table)
    elements.append(Spacer(1, 0.15*inch))
    
    # === TABLA DE ITEMS ===
    items_header = [
        Paragraph('<b>No.</b>', small_style),
        Paragraph('<b>Descripción</b>', small_style),
        Paragraph('<b>Cantidad</b>', small_style),
        Paragraph('<b>Unidad</b>', small_style),
        Paragraph('<b>Valor<br/>Unitario</b>', small_style),
        Paragraph('<b>Descuento<br/>Unitario</b>', small_style),
        Paragraph('<b>Monto</b>', small_style),
        Paragraph('<b>ITBMS</b>', small_style),
        Paragraph('<b>Valor Item</b>', small_style),
    ]
    items_data = [items_header]
    
    for item in invoice.line_items:
        items_data.append([
            str(item['no']),
            Paragraph(item['description'], small_style),
            f"{int(item['quantity'])}",  # Cantidad sin decimales
            item['unit'],
            f"{int(item['unit_price'])}",  # Valor unitario sin decimales
            f"{int(item['discount'])}",  # Descuento sin decimales
            f"{item['amount']:,.2f}",
            f"{int(item['tax'])}",  # Impuesto sin decimales
            f"{item['total']:,.2f}"
        ])
    
    # Anchos de columna que suman CONTENT_WIDTH (7.5")
    col_widths = [0.35*inch, 2.65*inch, 0.6*inch, 0.45*inch, 0.65*inch, 0.65*inch, 0.75*inch, 0.55*inch, 0.85*inch]
    
    # Agregar fila de "Valor Total" directamente a la tabla de items
    total = float(invoice.total)
    items_data.append(['', '', '', '', '', '', '', Paragraph('<b>Valor Total</b>', small_style), f'{total:,.2f}'])
    
    items_table = Table(items_data, colWidths=col_widths)
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.85, 0.85, 0.85)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -2), 0.5, colors.black),  # Grid hasta la penúltima fila
        ('BOX', (-2, -1), (-1, -1), 0.5, colors.black),  # Recuadro solo para Valor Total
        ('INNERGRID', (-2, -1), (-1, -1), 0.5, colors.black),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('FONTNAME', (-2, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 0.15*inch))
    
    # === SECCIÓN INFERIOR: ITBMS + TOTALES ===
    # Desglose ITBMS (izquierda)
    itbms_data = [
        [Paragraph('<b>Desglose ITBMS</b>', small_style), '', ''],
        [Paragraph('<b>Monto Base</b>', small_style), Paragraph('<b>%</b>', small_style), Paragraph('<b>Impuesto</b>', small_style)],
        [f'{total:,.2f}', 'Exento', '0.00'],
        ['0.00', '7%', '0.00'],
        ['0.00', '10%', '0.00'],
        ['0.00', '15%', '0.00'],
        ['', Paragraph('<b>Total</b>', small_style), '0.00'],
    ]
    itbms_table = Table(itbms_data, colWidths=[1*inch, 0.6*inch, 0.7*inch])
    itbms_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('ALIGN', (0, 2), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'CENTER'),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('GRID', (0, 1), (-1, -1), 0.5, colors.black),
        ('SPAN', (0, 0), (-1, 0)),
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    
    # Totales (derecha)
    totals_data = [
        [Paragraph('<b>Total Neto</b>', small_style), f'{total:,.2f}'],
        ['Monto Exento ITBMS', f'{total:,.2f}'],
        ['Monto Gravado ITBMS', '0.00'],
        ['ITBMS', ''],
        [Paragraph('<b>Total Impuesto</b>', small_style), '0.00'],
        [Paragraph('<b>Total USD</b>', small_style), f'{total:,.2f}'],
        ['', ''],
        [Paragraph('<b>Forma de Pago</b>', small_style), ''],
        ['Crédito', f'{total:,.2f}'],
        [Paragraph('<b>Total a pagar</b>', small_style), f'{total:,.2f}'],
    ]
    totals_table = Table(totals_data, colWidths=[1.4*inch, 0.9*inch])
    totals_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))
    
    # Combinar ITBMS y Totales en una fila (Total: CONTENT_WIDTH = 7.5")
    combined_data = [[itbms_table, '', totals_table]]
    combined_table = Table(combined_data, colWidths=[2.3*inch, 2.5*inch, 2.7*inch])
    combined_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(combined_table)
    elements.append(Spacer(1, 0.15*inch))
    
    # === INFORMACIÓN ADICIONAL ===
    info_header = Paragraph('<b>INFORMACIÓN ADICIONAL</b>', section_title_style)
    
    # Contenido estructurado en tabla
    info_rows = [
        [Paragraph('<b>Referencia:</b>', small_style), Paragraph(invoice.number, small_style)],
        [Paragraph('<b>Vendedor:</b>', small_style), Paragraph(invoice.vendor if invoice.vendor else 'N/A', small_style)],
        [Paragraph('<b>Comentario:</b>', small_style), Paragraph(invoice.comment if invoice.comment else 'N/A', small_style)],
        ['', ''],
        [Paragraph('<b>Datos de Pago:</b>', small_style), ''],
        [Paragraph('<b>Banco:</b>', small_style), Paragraph('BANCO GENERAL · Cuenta corriente', small_style)],
        [Paragraph('<b>Cuenta:</b>', small_style), Paragraph('03-01-01-123185-9', small_style)],
        [Paragraph('<b>Correo:</b>', small_style), Paragraph(support_email, small_style)],
        ['', ''],
        [Paragraph('PUEDE CONSULTAR LA VALIDEZ DEL DOCUMENTO ESCANEANDO EL CODIGO QR', small_style), ''],
    ]
    
    info_content_table = Table(info_rows, colWidths=[1.2*inch, 6.3*inch])
    info_content_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('SPAN', (0, 9), (-1, 9)),  # Span para el texto del QR
    ]))
    
    info_data = [
        [info_header],
        [info_content_table],
    ]
    info_table = Table(info_data, colWidths=[CONTENT_WIDTH])  # Mismo ancho que otras secciones
    info_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, colors.black),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(info_table)
    
    # Construir el documento con pie de página
    doc.build(elements, onFirstPage=add_footer, onLaterPages=add_footer)
    buffer.seek(0)
    
    return buffer
