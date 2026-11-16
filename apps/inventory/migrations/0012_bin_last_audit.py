from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0011_alter_bin_label_include_observation"),
    ]

    operations = [
        migrations.AddField(
            model_name="bin",
            name="last_audit",
            field=models.DateTimeField(
                blank=True,
                help_text="Última vez que se contrastó inventario en este bin.",
                null=True,
                verbose_name="última auditoría",
            ),
        ),
        migrations.AddField(
            model_name="bin",
            name="last_audit_quantity",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name="cantidad en última auditoría",
            ),
        ),
    ]
