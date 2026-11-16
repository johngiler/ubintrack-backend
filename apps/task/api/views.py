from django.db.models import Count, Prefetch, Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.utils.permissions_mixins import (
    DenyClientUsers,
    StrictDjangoModelPermissions,
    WorkspaceScopeMixin,
)
from apps.core.choices import TaskLineStatus, TaskStatus
from apps.inventory.models import BinProduct
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

        if obj.status == TaskStatus.IN_PROGRESS:
            from apps.task.services.build_lines import build_task_lines

            build_task_lines(obj)

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

        if "status" in serializer.validated_data:
            incoming_status = serializer.validated_data["status"]
            if incoming_status in (TaskStatus.VERIFICATION, TaskStatus.CLOSED):
                from apps.task.services.line_actions import (
                    next_status_after_lines,
                    task_has_pending_lines,
                )

                if task_has_pending_lines(instance):
                    raise ValidationError(
                        {"detail": "No se pudo completar la operación."}
                    )
                # Desde en_proceso: destino según require_approval.
                # Desde verificacion → completada se respeta (no reescribir a verificacion).
                if instance.status == TaskStatus.IN_PROGRESS:
                    serializer.validated_data["status"] = next_status_after_lines(
                        instance
                    )
                elif instance.status == TaskStatus.VERIFICATION:
                    if incoming_status != TaskStatus.CLOSED:
                        serializer.validated_data["status"] = TaskStatus.VERIFICATION
                elif incoming_status == TaskStatus.CLOSED and instance.require_approval:
                    serializer.validated_data["status"] = TaskStatus.VERIFICATION

                if (
                    serializer.validated_data["status"] == TaskStatus.CLOSED
                    and not instance.completed_at
                ):
                    from django.utils import timezone

                    serializer.validated_data.setdefault(
                        "completed_at", timezone.now()
                    )

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

        if obj.status == TaskStatus.IN_PROGRESS:
            from apps.task.services.build_lines import build_task_lines

            # Primera entrada a en_proceso, o alcance cambiado sin progreso → materializar.
            entered_progress = old_status != TaskStatus.IN_PROGRESS
            scope_touched = bool(scope_changes)
            has_progress = obj.lines.exclude(
                line_status=TaskLineStatus.PENDING
            ).exists()
            if entered_progress or not obj.lines.exists():
                build_task_lines(obj)
            elif scope_touched and not has_progress:
                build_task_lines(obj, force=True)

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
            if obj.status == TaskStatus.CLOSED:
                from apps.task.services.report_pdf import update_task_pdf

                update_task_pdf(obj)
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

    @action(detail=True, methods=["post"], url_path="generate_lines")
    def generate_lines(self, request, pk=None):
        from apps.task.services.build_lines import build_task_lines, scoped_bins_count

        task = self.get_object()
        force = bool(request.data.get("force"))
        # Sin líneas: siempre intenta crear. Con solo pendientes: force regenera.
        if not task.lines.exists():
            force = True
        created = build_task_lines(task, force=force)
        return Response(
            {
                "created": created,
                "lines_count": task.lines.count(),
                "scoped_bins": scoped_bins_count(task),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="regenerate_pdf")
    def regenerate_pdf(self, request, pk=None):
        from apps.task.services.report_pdf import update_task_pdf

        task = self.get_object()
        if task.status != TaskStatus.CLOSED:
            raise ValidationError({"detail": "No se pudo completar la operación."})
        pdf_path = update_task_pdf(task)
        if not pdf_path:
            raise ValidationError({"detail": "No se pudo completar la operación."})
        task.refresh_from_db(fields=["pdf_document"])
        pdf_url = None
        if task.pdf_document:
            try:
                pdf_url = request.build_absolute_uri(task.pdf_document.url)
            except ValueError:
                pdf_url = None
        return Response(
            {"pdf_url": pdf_url or pdf_path},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="verify_position")
    def verify_position(self, request, pk=None):
        from apps.task.services.line_actions import verify_position

        task = self.get_object()
        raw = (
            request.data.get("position_code")
            or request.data.get("code")
            or request.data.get("qr")
            or ""
        )
        line_id = request.data.get("line_id") or request.data.get("line")
        lines = verify_position(
            task=task,
            position_code=raw,
            actor=request.user,
            line_id=line_id,
        )
        data = serializers.TaskLineSerializer(
            lines, many=True, context={"request": request}
        ).data
        return Response({"results": data}, status=status.HTTP_200_OK)

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
    ).order_by("-created_at", "-id")
    serializer_class = serializers.TaskLogSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["task", "kind"]
    ordering_fields = ["created_at", "id"]
    ordering = ["-created_at", "-id"]
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
        "task",
        "position",
        "position__cava",
        "bin",
        "bin__client",
    ).prefetch_related(
        Prefetch(
            "bin__bin_products",
            queryset=BinProduct.objects.filter(is_active=True).select_related(
                "entry_product__product"
            ),
        )
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
