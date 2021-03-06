# -*- coding: utf-8 -*-
# Generated by Django 1.10.8 on 2019-01-10 12:37
from __future__ import unicode_literals

import contextlib
import os

from django.db import migrations, models
import freenasUI.freeadmin.models.fields


def create_reporting(apps, schema_editor):
    advanced = apps.get_model('system', 'Advanced').objects.latest('id')
    systemdataset = apps.get_model('system', 'SystemDataset').objects.latest('id')

    if systemdataset.sys_rrd_usedataset:
        # Unlink this file if we were not using RAMDisk so `reporting.setup` won't overwrite system dataset's data with
        # stale RAMDisk data
        with contextlib.suppress(Exception):
            os.unlink('/data/rrd_dir.tar.bz2')

    reporting = apps.get_model('system', 'Reporting').objects.create()
    reporting.cpu_in_percentage = advanced.adv_cpu_in_percentage
    reporting.graphite = advanced.adv_graphite
    reporting.save()


class Migration(migrations.Migration):

    dependencies = [
        ('system', '0036_remove_parseable_cert_attrs'),
    ]

    operations = [
        migrations.CreateModel(
            name='Reporting',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cpu_in_percentage', models.BooleanField(default=False, help_text='When set, report CPU usage in percent instead of jiffies.', verbose_name='Report CPU usage in percent')),
                ('graphite', models.CharField(blank=True, default='', help_text='Destination hostname or IP for collectd data sent by the Graphite plugin.', max_length=120, verbose_name='Graphite Server')),
                ('graph_age', models.IntegerField(default=12, help_text='Maximum age of graph stored, in months.', verbose_name='Graph Age')),
                ('graph_points', models.IntegerField(default=1200, help_text='Number of points for each hourly, daily, weekly, monthly, yearly graph. Set this to no less than the width of your graphs in pixels.', verbose_name='Graph Points Count')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.RunPython(create_reporting),
        migrations.RemoveField(
            model_name='advanced',
            name='adv_cpu_in_percentage',
        ),
        migrations.RemoveField(
            model_name='advanced',
            name='adv_graphite',
        ),
        migrations.RemoveField(
            model_name='systemdataset',
            name='sys_rrd_usedataset',
        ),
    ]
