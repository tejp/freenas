from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('system', '0043_2fa_authentication'),
    ]

    operations = [
        migrations.AddField(
            model_name='certificateauthority',
            name='cert_revoked',
            field=models.BooleanField(
                default=False,
                verbose_name='Revoked Certificate'
            )
        ),
        migrations.AddField(
            model_name='certificate',
            name='cert_revoked',
            field=models.BooleanField(
                default=False,
                verbose_name='Revoked Certificate'
            )
        )
    ]
