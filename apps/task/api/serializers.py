from rest_framework import serializers
from apps.task import models as task_models
from apps.core import models as core_models
from apps.order import models as order_models
from apps.core.choices import UserType
from apps.workspaces.utils.tenant import get_workspace_for_request
from django.db.models import Q


def _staff_users_for_workspace(workspace):
    qs = core_models.CustomUser.objects.filter(
        ~Q(user_type=UserType.CLIENT),
        is_active=True,
    )
    if workspace is not None:
        qs = qs.filter(workspace=workspace)
    return qs


class UserSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = core_models.CustomUser
        fields = ['id', 'username', 'email', 'full_name']


class EntrySimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = order_models.Entry
        fields = ['id', 'code', 'status']


class ExitSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = order_models.Exit
        fields = ['id', 'code', 'status']


class TaskLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = task_models.TaskLog
        fields = ['id', 'task', 'message', 'created_at', 'updated_at', 'is_active']
        read_only_fields = ['id', 'task', 'message', 'created_at', 'updated_at', 'is_active']


class TaskSerializer(serializers.ModelSerializer):
    assigned_to = UserSimpleSerializer(many=True, read_only=True)
    assigned_to_ids = serializers.PrimaryKeyRelatedField(
        queryset=core_models.CustomUser.objects.none(),
        many=True,
        write_only=True,
        source='assigned_to',
        required=False,
    )
    entry = EntrySimpleSerializer(read_only=True)
    entry_id = serializers.PrimaryKeyRelatedField(
        queryset=order_models.Entry.objects.none(),
        write_only=True,
        source='entry',
        required=False,
        allow_null=True,
    )
    exit = ExitSimpleSerializer(read_only=True)
    exit_id = serializers.PrimaryKeyRelatedField(
        queryset=order_models.Exit.objects.none(),
        write_only=True,
        source='exit',
        required=False,
        allow_null=True,
    )
    task_logs = TaskLogSerializer(many=True, read_only=True)

    class Meta:
        model = task_models.Task
        exclude = ('created_by', 'updated_by')
        read_only_fields = ['code', 'workspace']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        workspace = get_workspace_for_request(request) if request else None
        self.fields['assigned_to_ids'].queryset = _staff_users_for_workspace(workspace)
        if workspace is not None:
            self.fields['entry_id'].queryset = order_models.Entry.objects.filter(
                client__workspace=workspace,
            )
            self.fields['exit_id'].queryset = order_models.Exit.objects.filter(
                client__workspace=workspace,
            )

    def _validate_workspace_relations(self, attrs):
        workspace = self.instance.workspace if self.instance else attrs.get('workspace')
        if workspace is None:
            request = self.context.get('request')
            workspace = get_workspace_for_request(request) if request else None
        if workspace is None:
            return attrs

        entry = attrs.get('entry', getattr(self.instance, 'entry', None))
        if entry and entry.client.workspace_id != workspace.id:
            raise serializers.ValidationError(
                {'entry_id': 'La entrada no pertenece a este owner.'}
            )

        exit_obj = attrs.get('exit', getattr(self.instance, 'exit', None))
        if exit_obj and exit_obj.client.workspace_id != workspace.id:
            raise serializers.ValidationError(
                {'exit_id': 'La salida no pertenece a este owner.'}
            )

        assigned = attrs.get('assigned_to')
        if assigned is not None:
            for user in assigned:
                if user.workspace_id != workspace.id:
                    raise serializers.ValidationError(
                        {'assigned_to_ids': 'Un usuario asignado no pertenece a este owner.'}
                    )
        return attrs

    def validate(self, attrs):
        return self._validate_workspace_relations(attrs)


class TaskSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = task_models.Task
        fields = ['id', 'code', 'status']


class UserTaskTrackingSerializer(serializers.ModelSerializer):
    user = UserSimpleSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=core_models.CustomUser.objects.none(),
        write_only=True,
        source='user',
        required=False,
    )
    task = TaskSimpleSerializer(read_only=True)
    task_id = serializers.PrimaryKeyRelatedField(
        queryset=task_models.Task.objects.none(),
        write_only=True,
        source='task',
        required=False,
        allow_null=True,
    )

    class Meta:
        model = task_models.UserTaskTracking
        exclude = ('created_by', 'updated_by')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        workspace = get_workspace_for_request(request) if request else None
        self.fields['user_id'].queryset = _staff_users_for_workspace(workspace)
        if workspace is not None:
            self.fields['task_id'].queryset = task_models.Task.objects.filter(
                workspace=workspace,
            )

    def validate(self, attrs):
        user = attrs.get('user', getattr(self.instance, 'user', None))
        task = attrs.get('task', getattr(self.instance, 'task', None))
        if self.instance is None and user is None:
            raise serializers.ValidationError({'user_id': 'Este campo es requerido.'})
        if user and task and user.workspace_id != task.workspace_id:
            raise serializers.ValidationError(
                {'task_id': 'La tarea no pertenece al mismo owner que el usuario.'}
            )
        return attrs
