# One-timer: rellena Notification.actor desde actor_display (notificaciones antiguas).

from django.db import migrations


def _norm(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _display_keys(user) -> set[str]:
    keys = set()
    for raw in (
        getattr(user, "full_name", None),
        f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}".strip(),
        getattr(user, "username", None),
        getattr(user, "email", None),
    ):
        text = (raw or "").strip()
        if not text:
            continue
        keys.add(_norm(text))
        if "@" in text:
            local = (
                text.split("@", 1)[0]
                .replace(".", " ")
                .replace("_", " ")
                .replace("-", " ")
                .strip()
            )
            if local:
                keys.add(_norm(local))
                keys.add(_norm(local.title()))
    return {k for k in keys if k}


def backfill_notification_actors(apps, schema_editor):
    Notification = apps.get_model("notifications", "Notification")
    User = apps.get_model("core", "CustomUser")

    users = list(
        User.objects.all().only(
            "id",
            "workspace_id",
            "full_name",
            "first_name",
            "last_name",
            "username",
            "email",
        )
    )

    # workspace_id -> display_key -> [user_id, ...] (solo matches únicos se usan)
    by_ws: dict[int | None, dict[str, list[int]]] = {}
    for user in users:
        bucket = by_ws.setdefault(user.workspace_id, {})
        for key in _display_keys(user):
            bucket.setdefault(key, []).append(user.id)

    qs = (
        Notification.objects.filter(actor_id__isnull=True)
        .exclude(actor_display="")
        .exclude(actor_display__iexact="Sistema")
        .only("id", "workspace_id", "actor_display")
        .iterator(chunk_size=500)
    )

    updates = []
    for row in qs:
        key = _norm(row.actor_display)
        if not key:
            continue

        candidates = by_ws.get(row.workspace_id, {}).get(key) or []
        if len(candidates) != 1:
            # Fallback global solo si el nombre es único en todo el sistema.
            global_ids = []
            for bucket in by_ws.values():
                global_ids.extend(bucket.get(key) or [])
            # Dedup preservando orden
            seen = set()
            unique = []
            for uid in global_ids:
                if uid in seen:
                    continue
                seen.add(uid)
                unique.append(uid)
            if len(unique) != 1:
                continue
            actor_id = unique[0]
        else:
            actor_id = candidates[0]

        updates.append(
            Notification(id=row.id, actor_id=actor_id)
        )
        if len(updates) >= 500:
            Notification.objects.bulk_update(updates, ["actor_id"])
            updates = []

    if updates:
        Notification.objects.bulk_update(updates, ["actor_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0006_notification_actor"),
        ("core", "0006_alter_custompermission_module"),
    ]

    operations = [
        migrations.RunPython(backfill_notification_actors, migrations.RunPython.noop),
    ]
