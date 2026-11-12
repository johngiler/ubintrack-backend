from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0006_sidebar_logo_gradient_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="printer_ip",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Impresora de red del owner. Si vacío, se usa el fallback del entorno.",
                max_length=255,
                verbose_name="IP o host de impresora",
            ),
        ),
        migrations.AddField(
            model_name="workspace",
            name="printer_port",
            field=models.PositiveIntegerField(
                default=9100,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(65535),
                ],
                verbose_name="Puerto de impresora",
            ),
        ),
        migrations.AddField(
            model_name="workspace",
            name="label_width_in",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("4.00"),
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0.50")),
                    django.core.validators.MaxValueValidator(Decimal("12.00")),
                ],
                verbose_name="Ancho de etiqueta (pulgadas)",
            ),
        ),
        migrations.AddField(
            model_name="workspace",
            name="label_height_in",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("2.00"),
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0.50")),
                    django.core.validators.MaxValueValidator(Decimal("12.00")),
                ],
                verbose_name="Alto de etiqueta (pulgadas)",
            ),
        ),
        migrations.AddField(
            model_name="workspace",
            name="printer_dpi",
            field=models.PositiveIntegerField(
                default=203,
                validators=[
                    django.core.validators.MinValueValidator(72),
                    django.core.validators.MaxValueValidator(600),
                ],
                verbose_name="DPI de impresora",
            ),
        ),
        migrations.AddField(
            model_name="workspace",
            name="printer_language",
            field=models.CharField(
                choices=[("zpl", "ZPL")],
                default="zpl",
                max_length=16,
                verbose_name="Lenguaje de impresora",
            ),
        ),
    ]
