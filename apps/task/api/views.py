from django.db.models import Count, Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, mixins, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.common.utils.permissions_mixins import (
    DenyClientUsers,
    StrictDjangoModelPermissions,
    WorkspaceScopeMixin,
)
from apps.core.choices import TaskLineStatus
from apps.task import models as task_models
from apps.workspaces.utils.tenant import get_workspace_for_request

from . import serializers


class TaskViewSet(WorkspaceScopeMixin, viewsets.ModelViewSet):
    queryset = task_models.Task.objects.all().order_by("-created_at")
    serializer_class = serializers.TaskSerializer
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = [
        "task_type",
        "status",
        "priority",
        "assigned_to",
        "client",
        "cavas",
        "products",
    ]
    search_fields = ["code", "title", "observations"]
    ordering_fields = ["created_at", "started_at", "completed_at", "priority"]
    ordering = ["-created_at"]
    permission_classes = [
        IsAuthenticated,
        DenyClientUsers,
        StrictDjangoModelPermissions,
    ]
    workspace_field = "workspace"

    def get_serializer_class(self):
        if self.action == "list":
            return serializers.TaskListSerializer
        return serializers.TaskSerializer

    def get_queryset(self):
        qs = (
            self.scope_to_workspace(super().get_queryset())
            .select_related("client")
            .prefetch_related("assigned_to", "cavas", "products")
            .annotate(
                lines_count=Count("lines", distinct=True),
                lines_done=Count(
                    "lines",
                    filter=~Q(lines__line_status=TaskLineStatus.PENDING),
                    distinct=True,
                ),
            )
        )
        if self.action == "retrieve":
            qs = qs.prefetch_related("task_logs")
        return qs

    def perform_create(self, serializer):
        from apps.audit.services.crud_hooks import SECTION, log_crud
        from apps.notifications.services import notify_task_created
        from apps.task.services.task_log import (
            assignees_label,
            log_task_created,
            task_log_signals_muted,
        )

        ws = get_workspace_for_request(self.request)
        if ws is None:
            raise ValidationError({"detail": "Owner no resuelto."})
        actor = self.request.user
        with task_log_signals_muted():
            obj = serializer.save(workspace=ws)
        log_task_created(task=obj, actor=actor)

        assignees = list(obj.assigned_to.all())
        create_verb = "creó la tarea"
        if assignees:
            create_verb = (
                f"creó la tarea (asignada a {assignees_label(assignees)})"
            )
        log_crud(
            workspace=ws,
            section=SECTION.TASK,
            action="create",
            verb=create_verb,
            label=obj.code,
            actor=actor,
            resource_id=obj.pk,
            resource_code=obj.code,
        )
        notify_task_created(task=obj, actor=actor)

    def perform_update(self, serializer):
        from apps.audit.services.crud_hooks import SECTION, log_crud
        from apps.core.models import CustomUser
        from apps.notifications.services import notify_task_status, notify_task_updated
        from apps.task.services.task_log import (
            assignees_label,
            describe_update_changes,
            log_task_meta_change,
            log_task_reassign,
            log_task_scope_change,
            log_task_status_change,
            task_log_signals_muted,
        )

        instance = serializer.instance
        actor = self.request.user
        old_status = instance.status
        old_status_label = instance.get_status_display()
        old_assignee_ids = set(instance.assigned_to.values_list("id", flat=True))
        old_cava_ids = set(instance.cavas.values_list("id", flat=True))
        old_product_ids = set(instance.products.values_list("id", flat=True))
        # Snapshot escalar antes del save (halls/client/etc.).
        old_snapshot = task_models.Task.objects.get(pk=instance.pk)

        with task_log_signals_muted():
            obj = serializer.save()

        new_assignee_ids = set(obj.assigned_to.values_list("id", flat=True))
        added_ids = new_assignee_ids - old_assignee_ids
        removed_ids = old_assignee_ids - new_assignee_ids
        added_users = list(CustomUser.objects.filter(id__in=added_ids))
        removed_users = list(CustomUser.objects.filter(id__in=removed_ids))

        scope_changes, meta_changes, status_changed = describe_update_changes(
            old=old_snapshot, new=obj
        )
        if set(obj.cavas.values_list("id", flat=True)) != old_cava_ids:
            scope_changes.append("Cámaras actualizadas")
        if set(obj.products.values_list("id", flat=True)) != old_product_ids:
            scope_changes.append("Productos actualizados")

        if status_changed:
            log_task_status_change(task=obj, old_status=old_status, actor=actor)
            # Si también hubo reasignación en el mismo save, no duplicar: ya va en status.
            if added_users or removed_users:
                if obj.status != "asignada" or not added_users:
                    log_task_reassign(
                        task=obj,
                        added=added_users,
                        removed=removed_users,
                        actor=actor,
                    )
            verb = f"pasó {old_status_label} → {obj.get_status_display()}"
            if obj.status == "asignada" and obj.assigned_to.exists():
                verb = (
                    f"{verb} · Asignados: "
                    f"{assignees_label(obj.assigned_to.all())}"
                )
            log_crud(
                workspace=obj.workspace,
                section=SECTION.TASK,
                action="status",
                verb=verb,
                label=obj.code,
                actor=actor,
                resource_id=obj.pk,
                resource_code=obj.code,
            )
            notify_task_status(task=obj, new_status=obj.status, actor=actor)
        elif added_users or removed_users:
            log_task_reassign(
                task=obj,
                added=added_users,
                removed=removed_users,
                actor=actor,
            )
            bits = []
            if added_users:
                bits.append(f"asignó a {assignees_label(added_users)}")
            if removed_users:
                bits.append(f"desasignó a {assignees_label(removed_users)}")
            log_crud(
                workspace=obj.workspace,
                section=SECTION.TASK,
                action="reassign",
                verb=" y ".join(bits) if bits else "reasignó la tarea",
                label=obj.code,
                actor=actor,
                resource_id=obj.pk,
                resource_code=obj.code,
            )
            notify_task_updated(task=obj, actor=actor)
        else:
            log_task_scope_change(task=obj, changes=scope_changes, actor=actor)
            log_task_meta_change(task=obj, changes=meta_changes, actor=actor)
            if scope_changes or meta_changes:
                log_crud(
                    workspace=obj.workspace,
                    section=SECTION.TASK,
                    action="update",
                    verb="actualizó la tarea",
                    label=obj.code,
                    actor=actor,
                    resource_id=obj.pk,
                    resource_code=obj.code,
                )
                notify_task_updated(task=obj, actor=actor)

        # Alcance en paralelo a un cambio de estado (mismo PATCH).
        if status_changed or added_users or removed_users:
            log_task_scope_change(task=obj, changes=scope_changes, actor=actor)
            log_task_meta_change(task=obj, changes=meta_changes, actor=actor)

    def perform_destroy(self, instance):
        from apps.audit.services.crud_hooks import SECTION, log_crud
        from apps.notifications.services import notify_task_deleted

        ws = instance.workspace
        code = instance.code
        pk = instance.pk
        instance.delete()
        log_crud(
            workspace=ws,
            section=SECTION.TASK,
            action="delete",
            verb="eliminó la tarea",
            label=code,
            actor=self.request.user,
            resource_id=pk,
            resource_code=code,
        )
        notify_task_deleted(
            workspace=ws, code=code, resource_id=pk, actor=self.request.user
        )


class TaskLogViewSet(
    WorkspaceScopeMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = task_models.TaskLog.objects.select_related(
        "task", "created_by"
    ).order_by("created_at", "id")
    serializer_class = serializers.TaskLogSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["task", "kind"]
    ordering_fields = ["created_at", "id"]
    ordering = ["created_at", "id"]
    permission_classes = [
        IsAuthenticated,
        DenyClientUsers,
        StrictDjangoModelPermissions,
    ]
    workspace_field = "task__workspace"

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())


class TaskLineViewSet(
    WorkspaceScopeMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = task_models.TaskLine.objects.select_related(
        "task", "position", "bin"
    ).order_by("sort_order", "id")
    serializer_class = serializers.TaskLineSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["task", "line_status", "position"]
    ordering_fields = ["sort_order", "created_at"]
    ordering = ["sort_order"]
    permission_classes = [
        IsAuthenticated,
        DenyClientUsers,
        StrictDjangoModelPermissions,
    ]
    workspace_field = "task__workspace"

    def get_queryset(self):
        return self.scope_to_workspace(super().get_queryset())
