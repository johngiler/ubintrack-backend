from rest_framework import serializers
from apps.task import models as task_models
from apps.core import models as core_models
from apps.order import models as order_models
from django.db.models import Q


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
        exclude = ('created_by', 'updated_by')


class TaskSerializer(serializers.ModelSerializer):
    assigned_to = UserSimpleSerializer(many=True, read_only=True)
    assigned_to_ids = serializers.PrimaryKeyRelatedField(
        queryset=core_models.CustomUser.objects.filter(~Q(user_type='cliente')),
        many=True,
        write_only=True,
        source='assigned_to',
        required=False
    )
    entry = EntrySimpleSerializer(read_only=True)
    entry_id = serializers.PrimaryKeyRelatedField(
        queryset=order_models.Entry.objects.all(),
        write_only=True,
        source='entry',
        required=False,
        allow_null=True
    )
    exit = ExitSimpleSerializer(read_only=True)
    exit_id = serializers.PrimaryKeyRelatedField(
        queryset=order_models.Exit.objects.all(),
        write_only=True,
        source='exit',
        required=False,
        allow_null=True
    )
    task_logs = TaskLogSerializer(many=True, read_only=True)

    class Meta:
        model = task_models.Task
        exclude = ('created_by', 'updated_by')
        read_only_fields = ['code']


class TaskSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = task_models.Task
        fields = ['id', 'code', 'status']


class UserTaskTrackingSerializer(serializers.ModelSerializer):
    user = UserSimpleSerializer(read_only=True)
    task = TaskSimpleSerializer(read_only=True)

    class Meta:
        model = task_models.UserTaskTracking
        exclude = ('created_by', 'updated_by')
