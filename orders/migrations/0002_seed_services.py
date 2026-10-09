from django.db import migrations


def seed_services(apps, schema_editor):
    Service = apps.get_model("orders", "Service")
    for kind, name in [
        ("crown", "Полноанатомическая коронка"),
        ("implant", "Коронка на имплантате"),
        ("inlay", "Вкладка / накладка Inlay / Onlay"),
        ("bridge", "Мост"),
        ("model", "Подготовка STL-модели челюсти для печати"),
    ]:
        Service.objects.get_or_create(kind=kind, defaults={"name": name})


class Migration(migrations.Migration):
    dependencies = [("orders", "0001_initial")]
    operations = [migrations.RunPython(seed_services, migrations.RunPython.noop)]
