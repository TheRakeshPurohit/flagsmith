# Generated by Django 3.2.12 on 2022-03-16 11:26

from django.db import migrations, models


def remove_null_version_feature_states(apps, schema_editor):  # type: ignore[no-untyped-def]
    feature_state_model = apps.get_model("features", "FeatureState")
    feature_state_model.objects.filter(version__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0038_remove_old_versions_and_drafts"),
    ]

    operations = [
        migrations.AlterField(
            model_name="featurestate",
            name="version",
            field=models.IntegerField(default=1, null=True),
        ),
        migrations.AlterField(
            model_name="historicalfeaturestate",
            name="version",
            field=models.IntegerField(default=1, null=True),
        ),
        migrations.RunPython(
            migrations.RunPython.noop, reverse_code=remove_null_version_feature_states
        ),
    ]
