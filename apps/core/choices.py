from django.db import models


class UoM(models.TextChoices):
    BOX = 'Cajas', 'Cajas'
    TANQUE = 'Tanque', 'Tanque'
    UNIDAD = 'Unidad', 'Unidad'
    SACO = 'Saco', 'Saco'
    CANASTA = 'Canasta', 'Canasta'


class Conditions(models.TextChoices):
    FROZEN = 'Congelado', 'Congelado'
    REFRIGERATED = 'Refrigerado', 'Refrigerado'
    DRY = 'Seco', 'Seco'


class InTypes(models.TextChoices):
    PALLETIZADO = 'Palletizado', 'Palletizado'
    PISO = 'Piso', 'Piso'


class RotationTypes(models.TextChoices):
    FIFO = 'FIFO', 'FIFO'
    FEFO = 'FEFO', 'FEFO'


class BinStatus(models.TextChoices):
    EN_RECIBO = 'en_recibo', 'En recibo'
    ALMACENADO = 'almacenado', 'Almacenado'
    EN_DESPACHO = 'en_despacho', 'En despacho'
    DESPACHADO = 'despachado', 'Despachado'


class OrderStatus(models.TextChoices):
    PENDIENTE = 'pendiente', 'Pendiente'
    EN_PROGRESO = 'en_progreso', 'En Progreso'
    COMPLETADA = 'completada', 'Completada'
    CANCELADA = 'cancelada', 'Cancelada'


class PositionStatus(models.TextChoices):
    LIBRE = 'libre', 'Libre'
    ASIGNADO = 'asignado', 'Asignado'
    OCUPADO = 'ocupado', 'Ocupado'


class TaskStatus(models.TextChoices):
    PENDING = 'pendiente', 'Pendiente'
    ASSIGNED = 'asignada', 'Asignada'
    IN_PROGRESS = 'en_proceso', 'En Proceso'
    VERIFICATION = 'verificacion', 'Verificación'
    CLOSED = 'completada', 'Completada'
    CANCELLED = 'cancelada', 'Cancelada'


class TaskType(models.TextChoices):
    AUDIT = 'auditoria', 'Auditoría de Inventario'
    EXPIRATION_CONTROL = 'vencimientos', 'Control de Vencimientos'
    ERROR_CORRECTION = 'correccion', 'Corrección de Errores'
    ENTRY_STORAGE = 'entrada_almacenamiento', 'Tarea de Entrada y Almacenamiento'
    EXIT_DISPATCH = 'salida_despacho', 'Tarea de Salida y Despacho'


class TaskPriority(models.TextChoices):
    LOW = 'low', 'Low'
    MEDIUM = 'medium', 'Medium'
    HIGH = 'high', 'High'


class UserType(models.TextChoices):
    ADMINISTRATOR = 'administrador', 'Administrador'
    EMPLOYEE = 'empleado', 'Empleado'
    CLIENT = 'cliente', 'Cliente'


class ProfileUserType(models.TextChoices):
    EMPLOYEE = UserType.EMPLOYEE
    CLIENT = UserType.CLIENT
