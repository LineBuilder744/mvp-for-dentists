from django.db import migrations


def seed(apps, schema_editor):
    Service = apps.get_model("orders", "Service")
    Service.objects.get_or_create(kind="surgical_guide", defaults={"name": "Хирургический шаблон", "is_active": True})


class Migration(migrations.Migration):
    dependencies = [("orders", "0011_alter_orderfile_purpose_alter_service_kind")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
