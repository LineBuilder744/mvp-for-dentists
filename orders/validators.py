import re
import struct
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError


def validate_attachment(upload):
    if Path(upload.name).suffix.lower() == ".stl":
        return validate_stl(upload)
    from PIL import Image, UnidentifiedImageError
    if Path(upload.name).suffix.lower() not in (".jpg", ".jpeg", ".png"):
        raise ValidationError("Допустимы STL, JPG и PNG.")
    if not upload.size or upload.size > 10 * 1024 * 1024:
        raise ValidationError("Изображение должно быть непустым и не больше 10 МБ.")
    try:
        image = Image.open(upload)
        expected = "PNG" if Path(upload.name).suffix.lower() == ".png" else "JPEG"
        if image.format != expected or image.width * image.height > 20_000_000:
            raise ValidationError("Неверный формат изображения или размер больше 20 мегапикселей.")
        image.verify()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, SyntaxError, ValueError):
        raise ValidationError("Изображение повреждено или не соответствует формату JPG/PNG.")
    finally:
        upload.seek(0)


def validate_stl(upload):
    if Path(upload.name).suffix.lower() != ".stl":
        raise ValidationError("Принимаются только STL-файлы.")
    if not upload.size or upload.size > settings.MAX_UPLOAD_BYTES:
        raise ValidationError("Файл должен быть непустым и не больше 200 МБ.")
    try:
        upload.seek(0)
        head = upload.read(84)
        if len(head) == 84:
            triangles = struct.unpack("<I", head[80:84])[0]
            if triangles > 0 and upload.size == 84 + 50 * triangles:
                return
        # Validate ASCII structure without loading a 200 MB scan into memory.
        upload.seek(0)
        lines = (line.strip() for line in upload if line.strip())
        first = next(lines, b"")
        if not first.startswith(b"solid"):
            raise ValidationError("Файл не соответствует формату STL.")
        number = rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
        vector = number + rb"\s+" + number + rb"\s+" + number
        facet = re.compile(rb"facet\s+normal\s+" + vector)
        vertex = re.compile(rb"vertex\s+" + vector)
        count = 0
        while True:
            line = next(lines, b"")
            if line.startswith(b"endsolid"):
                if count and not next(lines, b""):
                    return
                break
            if not facet.fullmatch(line) or next(lines, b"") != b"outer loop":
                break
            if any(not vertex.fullmatch(next(lines, b"")) for _ in range(3)):
                break
            if next(lines, b"") != b"endloop" or next(lines, b"") != b"endfacet":
                break
            count += 1
        raise ValidationError("Повреждённый или неподдерживаемый STL-файл.")
    finally:
        upload.seek(0)
