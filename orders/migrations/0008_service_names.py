from django.db import migrations


def rename_defaults(apps, schema_editor):
    Service = apps.get_model("orders", "Service")
    for kind, previous, current in [
        ("crown", "Полноанатомическая коронка", "Коронка"),
        ("implant", "Коронка на имплантате", "Коронка на импланте"),
        ("inlay", "Вкладка / накладка Inlay / Onlay", "Вкладка / накладка"),
    ]:
        Service.objects.filter(kind=kind, name=previous).update(name=current)


class Migration(migrations.Migration):
    dependencies = [("orders", "0007_alter_order_estimated_price_and_more")]
    operations = [migrations.RunPython(rename_defaults, migrations.RunPython.noop)]
