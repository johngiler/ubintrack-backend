from django.db import models
from django.db.models import Q
from django_currentuser.db.models import CurrentUserField

from apps.core.models import CustomUser
from apps.order.models import Entry, Exit
from apps.core.choices import TaskStatus, TaskType, TaskPriority


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


class Task(BaseModel):
    code = models.CharField(max_length=15, unique=True,
                            editable=False, verbose_name='código de tarea')
    task_type = models.CharField(
        max_length=30, choices=TaskType.choices, verbose_name='tipo de tarea')
    status = models.CharField(
        max_length=20, choices=TaskStatus.choices, default=TaskStatus.PENDING, verbose_name='estado')
    priority = models.CharField(
        max_length=10, choices=TaskPriority.choices, default=TaskPriority.MEDIUM, verbose_name='prioridad')
    started_at = models.DateTimeField(
        null=True, blank=True, verbose_name='fecha de inicio')
    completed_at = models.DateTimeField(
        null=True, blank=True, verbose_name='fecha de fin')

    assigned_to = models.ManyToManyField(
        CustomUser,
        blank=True,
        related_name="tasks",
        limit_choices_to=~Q(user_type='cliente'),
        verbose_name='asignado a'
    )

    entry = models.ForeignKey(
        Entry,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name='orden de entrada'
    )

    exit = models.ForeignKey(
        Exit,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name='orden de salida'
    )

    observations = models.TextField(null=True, blank=True, verbose_name='observaciones')

    class Meta:
        verbose_name = 'tarea'
        verbose_name_plural = 'tareas'

    def save(self, *args, **kwargs):
        if not self.code:
            last_task = Task.objects.order_by('id').last()
            if last_task and last_task.code:
                last_number = int(last_task.code.split('-')[-1])
                new_number = last_number + 1
            else:
                new_number = 1
            self.code = f"TASK-{new_number:010d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class TaskLog(BaseModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE,
                             related_name="task_logs", verbose_name='tarea')
    message = models.TextField(editable=False, verbose_name='mensaje')

    class Meta:
        verbose_name = 'log de tarea'
        verbose_name_plural = 'logs de tarea'

    def __str__(self):
        return self.task.code


class UserTaskTracking(BaseModel):
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="task_trackings",
        limit_choices_to=~Q(user_type='cliente'),
        verbose_name='usuario'
    )

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="user_trackings",
        blank=True,
        null=True,
        verbose_name='tarea'
    )

    timer_start = models.DateTimeField(verbose_name='inicio del temporizador')
    timer_stop = models.DateTimeField(
        null=True, blank=True, verbose_name='parada del temporizador')
    duration = models.DurationField(null=True, blank=True, verbose_name='duración')
    time_since_last_task = models.DurationField(
        null=True, blank=True, verbose_name='tiempo transcurrido desde la última tarea')

    class Meta:
        verbose_name = 'seguimiento de tarea'
        verbose_name_plural = 'seguimiento de tareas'

    def save(self, *args, **kwargs):
        if self.timer_start and self.timer_stop:
            self.duration = self.timer_stop - self.timer_start

        if self.user and self.timer_start:
            previous_tracking = (
                UserTaskTracking.objects.filter(
                    user=self.user, timer_stop__isnull=False)
                .exclude(pk=self.pk)
                .order_by("-timer_stop")
                .first()
            )
            if previous_tracking:
                self.time_since_last_task = self.timer_start - previous_tracking.timer_stop

        super().save(*args, **kwargs)

    def __str__(self):
        return self.user.username
