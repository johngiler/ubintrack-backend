from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_customuser_client_and_roles"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ClientUser",
        ),
    ]
