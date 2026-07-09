from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0005_alter_position_place"),
    ]

    operations = [
        migrations.AlterField(
            model_name="position",
            name="hall",
            field=models.CharField(
                choices=[
                    ("A", "Pasillo A"),
                    ("B", "Pasillo B"),
                    ("C", "Pasillo C"),
                    ("D", "Pasillo D"),
                    ("E", "Pasillo E"),
                    ("F", "Pasillo F"),
                ],
                max_length=1,
                verbose_name="pasillo",
            ),
        ),
    ]
