from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0002_initial'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='BinEntry',
            new_name='Bin',
        ),
        migrations.RenameModel(
            old_name='BinEntryProduct',
            new_name='BinProduct',
        ),
        migrations.RenameField(
            model_name='binmovement',
            old_name='bin_entry',
            new_name='bin',
        ),
        migrations.AlterModelOptions(
            name='bin',
            options={
                'verbose_name': 'bin',
                'verbose_name_plural': 'bins',
            },
        ),
        migrations.AlterModelOptions(
            name='binproduct',
            options={
                'verbose_name': 'producto de bin',
                'verbose_name_plural': 'productos de bin',
            },
        ),
        migrations.DeleteModel(
            name='BinExitProduct',
        ),
        migrations.DeleteModel(
            name='BinExit',
        ),
    ]
