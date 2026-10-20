import re
import uuid
from pathlib import PurePosixPath

from django.db import migrations


def anonymize_file_names(apps, schema_editor):
    OrderFile = apps.get_model("orders", "OrderFile")
    for file in OrderFile.objects.all().iterator():
        stored_name = PurePosixPath(file.file.name).name
        if re.fullmatch(r"[0-9a-f]{32}\.(?:stl|png|jpe?g)", stored_name):
            name = stored_name
        else:
            suffix = PurePosixPath(stored_name).suffix.lower()
            suffix = suffix if suffix in (".stl", ".png", ".jpg", ".jpeg") else ".bin"
            name = uuid.uuid4().hex + suffix
        OrderFile.objects.filter(pk=file.pk).update(original_name=name)


class Migration(migrations.Migration):
    dependencies = [("orders", "0009_alter_service_kind")]
    operations = [migrations.RunPython(anonymize_file_names, migrations.RunPython.noop)]
