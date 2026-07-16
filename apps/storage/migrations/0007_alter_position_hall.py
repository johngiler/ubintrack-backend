from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("storage", "0006_alter_position_hall"),
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
                    ("G", "Pasillo G"),
                ],
                max_length=1,
                verbose_name="pasillo",
            ),
        ),
    ]
