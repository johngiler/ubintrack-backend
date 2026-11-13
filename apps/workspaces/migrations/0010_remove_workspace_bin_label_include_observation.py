from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0009_alter_workspace_printer_ip"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="workspace",
            name="bin_label_include_observation",
        ),
    ]
