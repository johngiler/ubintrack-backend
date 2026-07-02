"""
One-timer post import legacy: asigna workspace a registros que quedaron sin owner.

Principalmente usuarios cliente con workspace_id NULL (import legacy los dejó
desvinculados aunque client.workspace sí apuntaba al owner).
"""

from django.db import migrations
from django.db.models import OuterRef, Q, Subquery


DEFAULT_WORKSPACE_SLUG = "rbcold"

USER_TYPE_ADMINISTRATOR = "administrador"
USER_TYPE_EMPLOYEE = "empleado"


def _platform_only_users_qs(CustomUser):
    return CustomUser.objects.filter(
        Q(is_superuser=True, client__isnull=True, user_type__isnull=True)
        | Q(
            is_staff=True,
            is_superuser=False,
            workspace__isnull=True,
            client__isnull=True,
            user_type__isnull=True,
        )
    )


def backfill_legacy_workspace_links(apps, schema_editor):
    Workspace = apps.get_model("workspaces", "Workspace")
    CustomUser = apps.get_model("core", "CustomUser")
    Client = apps.get_model("core", "Client")
    Profile = apps.get_model("core", "Profile")
    Tariff = apps.get_model("finance", "Tariff")
    Storage = apps.get_model("storage", "Storage")
    Task = apps.get_model("task", "Task")
    Entry = apps.get_model("order", "Entry")
    Exit = apps.get_model("order", "Exit")

    workspace = Workspace.objects.filter(slug=DEFAULT_WORKSPACE_SLUG, is_active=True).first()
    if workspace is None:
        workspace = Workspace.objects.filter(is_active=True).order_by("id").first()
    if workspace is None:
        return

    Tariff.objects.filter(workspace__isnull=True).update(workspace=workspace)
    Storage.objects.filter(workspace__isnull=True).update(workspace=workspace)
    Profile.objects.filter(workspace__isnull=True).update(workspace=workspace)
    Client.objects.filter(workspace__isnull=True).update(workspace=workspace)

    entry_ws = Entry.objects.filter(pk=OuterRef("entry_id")).values("client__workspace_id")[:1]
    Task.objects.filter(
        workspace__isnull=True,
        entry__isnull=False,
        entry__client__workspace__isnull=False,
    ).update(workspace_id=Subquery(entry_ws))

    exit_ws = Exit.objects.filter(pk=OuterRef("exit_id")).values("client__workspace_id")[:1]
    Task.objects.filter(
        workspace__isnull=True,
        exit__isnull=False,
        exit__client__workspace__isnull=False,
    ).update(workspace_id=Subquery(exit_ws))

    Task.objects.filter(workspace__isnull=True).update(workspace=workspace)

    platform_ids = list(_platform_only_users_qs(CustomUser).values_list("pk", flat=True))

    client_ws = Client.objects.filter(pk=OuterRef("client_id")).values("workspace_id")[:1]
    CustomUser.objects.filter(
        workspace__isnull=True,
        client__isnull=False,
        client__workspace__isnull=False,
    ).exclude(pk__in=platform_ids).update(workspace_id=Subquery(client_ws))

    profile_ws = Profile.objects.filter(pk=OuterRef("profile_id")).values("workspace_id")[:1]
    CustomUser.objects.filter(
        workspace__isnull=True,
        profile__isnull=False,
        profile__workspace__isnull=False,
    ).exclude(pk__in=platform_ids).update(workspace_id=Subquery(profile_ws))

    CustomUser.objects.filter(
        workspace__isnull=True,
        user_type__in=(USER_TYPE_ADMINISTRATOR, USER_TYPE_EMPLOYEE),
    ).exclude(pk__in=platform_ids).update(workspace=workspace)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0003_workspace_created_by_workspace_updated_by"),
        ("core", "0004_alter_branch_created_at_alter_branch_is_active_and_more"),
        ("finance", "0001_initial"),
        ("storage", "0002_alter_cava_created_at_alter_cava_is_active_and_more"),
        ("task", "0002_alter_task_created_at_alter_task_is_active_and_more"),
        ("order", "0002_entry_is_active_entrydetail_is_active_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_legacy_workspace_links, noop_reverse),
    ]
