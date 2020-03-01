# Generated by Django 2.2.10 on 2020-02-19 23:43

from django.db import migrations


from environments.permissions import ENVIRONMENT_PERMISSIONS


def create_default_permissions(apps, schema_editor):
    EnvironmentPermission = apps.get_model('environments', 'EnvironmentPermission')

    environment_permissions = []
    for permission in ENVIRONMENT_PERMISSIONS:
        environment_permissions.append(EnvironmentPermission(key=permission[0], description=permission[1]))

    EnvironmentPermission.objects.bulk_create(environment_permissions)


class Migration(migrations.Migration):

    dependencies = [
        ('environments', '0009_auto_20200219_1922'),
    ]

    operations = [
        migrations.RunPython(create_default_permissions, reverse_code=lambda *args: None)
    ]
