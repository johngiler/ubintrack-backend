from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("task", "0007_tasklog_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskline",
            name="counted_quantity",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name="cantidad contada",
            ),
        ),
        migrations.AddField(
            model_name="taskline",
            name="verified_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="verificado en",
            ),
        ),
        migrations.AlterField(
            model_name="taskline",
            name="line_status",
            field=models.CharField(
                choices=[
                    ("pendiente", "Pendiente"),
                    ("contado", "Contado"),
                    ("diferencia", "Diferencia"),
                    ("no_existe", "No existe bin"),
                    ("omitido", "Omitido"),
                ],
                default="pendiente",
                max_length=20,
                verbose_name="estado de línea",
            ),
        ),
    ]
