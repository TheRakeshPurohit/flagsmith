# Generated by Django 4.2.16 on 2024-11-08 18:14

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tags", "0006_alter_tag_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tag",
            name="color",
            field=models.CharField(
                default="#6837FC",
                help_text="Hexadecimal value of the tag color",
                max_length=10,
            ),
        ),
    ]