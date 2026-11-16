# Guarda la acción real del historial (bulk_print, generate, …) y backfill.

from django.db import migrations, models


def _infer_activity_action(event: str, message: str) -> str:
    text = (message or "").lower()
    if "imprim" in text:
        return "bulk_print"
    if "regener" in text:
        return "bulk_regenerate"
    if "generó" in text or "genero" in text:
        # "generó N posiciones" (no regeneró)
        if "regener" not in text:
            return "generate"
    if "push en hilera" in text or "hilera" in text and "push" in text:
        return "set_hilera_push"
    if event == "created":
        return "create"
    if event == "deleted":
        # masivo: "eliminó N bins/posiciones"
        if " etiquetas" not in text and any(
            token in text for token in (" bins", " bin", " posiciones", " posición")
        ):
            # Si dice "eliminó 5 bins" → bulk_delete; "eliminó el bin X" → delete
            import re

            if re.search(r"elimin[oó]\s+\d+\s+", text):
                return "bulk_delete"
        return "delete"
    if event == "status":
        return "status"
    if event == "updated":
        return "update"
    return ""


def backfill_activity_action(apps, schema_editor):
    Notification = apps.get_model("notifications", "Notification")
    updates = []
    for row in Notification.objects.all().only("id", "event", "message", "activity_action").iterator(
        chunk_size=500
    ):
        action = _infer_activity_action(row.event, row.message)
        if not action or row.activity_action == action:
            continue
        updates.append(Notification(id=row.id, activity_action=action))
        if len(updates) >= 500:
            Notification.objects.bulk_update(updates, ["activity_action"])
            updates = []
    if updates:
        Notification.objects.bulk_update(updates, ["activity_action"])


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0007_backfill_notification_actor"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="activity_action",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Clave de filtro del historial de vista (create, bulk_print, …).",
                max_length=64,
                verbose_name="acción de historial",
            ),
        ),
        migrations.RunPython(backfill_activity_action, migrations.RunPython.noop),
    ]
