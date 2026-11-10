# aisle → section; floor kind; section_type; stretch_full; limpia overflow viejo.

from django.db import migrations, models


def forwards(apps, schema_editor):
    CavaBlock = apps.get_model("storage", "CavaBlock")
    Position = apps.get_model("storage", "Position")

    # Posiciones de sobre-almacenamiento antiguas (sin rack).
    Position.objects.filter(rack="").delete()

    for block in CavaBlock.objects.filter(kind="aisle"):
        block.kind = "section"
        block.section_type = "pasillo"
        block.rack_count = 0
        block.stretch_full = True
        block.save(
            update_fields=["kind", "section_type", "rack_count", "stretch_full"]
        )


def backwards(apps, schema_editor):
    CavaBlock = apps.get_model("storage", "CavaBlock")
    CavaBlock.objects.filter(kind="floor").delete()
    for block in CavaBlock.objects.filter(kind="section"):
        block.kind = "aisle"
        block.section_type = ""
        block.save(update_fields=["kind", "section_type"])


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0023_alter_positionnomenclature_aisle_prefix"),
    ]

    operations = [
        migrations.AddField(
            model_name="cavablock",
            name="section_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("pasillo", "Pasillo"),
                    ("tunel", "Túnel"),
                    ("division", "División"),
                ],
                default="",
                help_text="Solo section: pasillo, tunel o division.",
                max_length=12,
                verbose_name="tipo de sección",
            ),
        ),
        migrations.AddField(
            model_name="cavablock",
            name="stretch_full",
            field=models.BooleanField(
                default=True,
                help_text="Si True, la sección estira al 100% del eje de su orientación.",
                verbose_name="100%",
            ),
        ),
        migrations.AlterField(
            model_name="cavablock",
            name="kind",
            field=models.CharField(
                choices=[
                    ("rack", "Rack"),
                    ("section", "Sección"),
                    ("floor", "Piso"),
                    ("aisle", "Pasillo"),  # temporal hasta RunPython
                ],
                max_length=8,
                verbose_name="tipo",
            ),
        ),
        migrations.AlterField(
            model_name="cavablock",
            name="name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Nombre visible de la sección (ej. Pasillo 1, Tunel 1, Linea 1). El code se deriva de aquí.",
                max_length=64,
                verbose_name="nombre",
            ),
        ),
        migrations.AlterField(
            model_name="cavablock",
            name="code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Token en el código de posición. En sección: derivado del nombre (P1). En rack: inicio de serie.",
                max_length=32,
                verbose_name="código",
            ),
        ),
        migrations.AlterField(
            model_name="cavablock",
            name="rack_count",
            field=models.PositiveIntegerField(
                default=1,
                help_text="En rack: cantidad de racks. En floor: posiciones de piso. En section: 0.",
                verbose_name="cantidad",
            ),
        ),
        migrations.AlterField(
            model_name="cavablock",
            name="aisle_code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Token de sección ligada al rack. Vacío = heredar el contiguo; '-' = sin sección.",
                max_length=32,
                verbose_name="pasillo",
            ),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="cavablock",
            name="kind",
            field=models.CharField(
                choices=[
                    ("rack", "Rack"),
                    ("section", "Sección"),
                    ("floor", "Piso"),
                ],
                max_length=8,
                verbose_name="tipo",
            ),
        ),
    ]
