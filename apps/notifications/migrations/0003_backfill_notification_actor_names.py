from django.db import migrations
import re


_EMAIL_RE = re.compile(r"^[^\s]+@[^\s]+$")
_LEADING_EMAIL_RE = re.compile(r"^([^\s]+@[^\s]+)\s+")


def _display_name(user) -> str:
    name = (getattr(user, "full_name", None) or "").strip()
    if name and "@" not in name:
        return name
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    combined = f"{first} {last}".strip()
    if combined:
        return combined
    username = (getattr(user, "username", None) or "").strip()
    if username and "@" not in username:
        return username
    email = (getattr(user, "email", None) or username or "").strip()
    if "@" in email:
        local = email.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
        return local.title() if local else "Usuario"
    return email or "Usuario"


def backfill_actor_names(apps, schema_editor):
    Notification = apps.get_model("notifications", "Notification")
    User = apps.get_model("core", "CustomUser")

    users_by_login = {}
    for user in User.objects.all().only("id", "username", "email", "full_name", "first_name", "last_name"):
        if user.username:
            users_by_login[user.username.lower()] = user
        if user.email:
            users_by_login[user.email.lower()] = user

    for notification in Notification.objects.all().iterator():
        changed = False
        actor_raw = (notification.actor_display or "").strip()
        name = actor_raw

        if actor_raw and _EMAIL_RE.match(actor_raw):
            user = users_by_login.get(actor_raw.lower())
            name = _display_name(user) if user else actor_raw.split("@", 1)[0].title()
            if name != notification.actor_display:
                notification.actor_display = name
                changed = True

        message = notification.message or ""
        match = _LEADING_EMAIL_RE.match(message)
        if match and name:
            new_message = f"{name} {message[match.end():]}"
            if new_message != notification.message:
                notification.message = new_message
                changed = True

        if changed:
            notification.save(update_fields=["actor_display", "message"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0002_notification_position_status"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_actor_names, noop_reverse),
    ]
