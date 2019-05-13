from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0031_remove_service_monitor'),
    ]

    operations = [
        migrations.CreateModel(
            name='OpenVPNClient',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('port', models.IntegerField(default=1194, verbose_name='Port')),
                ('protocol', models.CharField(default='UDP', max_length=4)),
                ('device_type', models.CharField(default='TUN', max_length=4)),
                ('nobind', models.BooleanField(default=True, verbose_name='Nobind')),
                (
                    'authentication_algorithm', models.CharField(
                        max_length=32, null=True, verbose_name='Authentication Algorithm'
                    )
                ),
                ('tls_crypt_auth', models.BooleanField(default=True, verbose_name='TLS Crypt Authentication')),
                ('cipher', models.CharField(max_length=32, null=True)),
                ('compression', models.CharField(max_length=32, null=True)),
                ('additional_parameters', models.TextField(default='', verbose_name='Additional Parameters')),
                (
                    'certificate', models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                        to='system.Certificate', verbose_name='Certificate'
                    )
                ),
                (
                    'certificate_authority', models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                        to='system.CertificateAuthority', verbose_name='Certificate Authority'
                    )
                ),
                ('remote', models.CharField(max_length=120, verbose_name='Remote IP/Domain')),
                ('remote_port', models.IntegerField(default=1194, verbose_name='Remote Port'))
            ],
            options={
                'verbose_name': 'OpenVPN Client'
            },
        ),
        migrations.CreateModel(
            name='OpenVPNServer',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('port', models.IntegerField(default=1194, verbose_name='Port')),
                ('protocol', models.CharField(default='UDP', max_length=4)),
                ('device_type', models.CharField(default='TUN', max_length=4)),
                ('nobind', models.BooleanField(default=True, verbose_name='Nobind')),
                (
                    'authentication_algorithm', models.CharField(
                        max_length=32, null=True, verbose_name='Authentication Algorithm'
                    )
                ),
                ('tls_crypt_auth', models.BooleanField(default=True, verbose_name='TLS Crypt Authentication')),
                ('cipher', models.CharField(max_length=32, null=True)),
                ('compression', models.CharField(max_length=32, null=True)),
                ('additional_parameters', models.TextField(default='', verbose_name='Additional Parameters')),
                (
                    'certificate', models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                        to='system.Certificate', verbose_name='Certificate'
                    )
                ),
                (
                    'certificate_authority', models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                        to='system.CertificateAuthority', verbose_name='Certificate Authority'
                    )
                ),
                ('server', models.CharField(default='10.8.0.0', verbose_name='Server IP', max_length=45)),
                ('topology', models.CharField(max_length=16, null=True, verbose_name='Topology'))
            ],
            options={
                'verbose_name': 'OpenVPN Server'
            },
        )
    ]
