from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0022_fix_aisle_token_codes_clear_prefix"),
    ]

    operations = [
        migrations.AlterField(
            model_name="positionnomenclature",
            name="aisle_prefix",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Obsoleto: el token del pasillo ya viene completo desde el nombre (P1, T1).",
                max_length=8,
                verbose_name="prefijo de pasillo",
            ),
        ),
    ]
