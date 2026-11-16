# Alcance multi: cámaras M2M, productos M2M, pasillos JSON.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0010_bin_label_include_observation"),
        ("storage", "0033_entry_position_movements"),
        ("task", "0003_wms_inventory_tasks"),
    ]

    operations = [
        migrations.RemoveField(model_name="task", name="cava"),
        migrations.RemoveField(model_name="task", name="hall"),
        migrations.RemoveField(model_name="task", name="product"),
        migrations.AddField(
            model_name="task",
            name="halls",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Tokens de pasillo/túnel (P1, T1, …). Vacío = todos.",
                verbose_name="pasillos",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="cavas",
            field=models.ManyToManyField(
                blank=True,
                related_name="tasks",
                to="storage.cava",
                verbose_name="cámaras",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="products",
            field=models.ManyToManyField(
                blank=True,
                related_name="tasks",
                to="inventory.product",
                verbose_name="productos",
            ),
        ),
    ]
