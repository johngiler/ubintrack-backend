from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0003_rename_bin_remove_bin_exit'),
        ('order', '0001_initial'),
    ]

    operations = [
        migrations.RenameField(
            model_name='exitproduct',
            old_name='bin_entry_product',
            new_name='bin_product',
        ),
        migrations.RenameField(
            model_name='exitmovement',
            old_name='bin_entry',
            new_name='bin',
        ),
    ]
