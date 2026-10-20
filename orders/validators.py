import re
import struct
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError


def validate_ct_dicom(upload):
    import pydicom
    from pydicom.encaps import generate_frames
    from pydicom.errors import BytesLengthException, InvalidDicomError

    if Path(upload.name).suffix.lower() != ".dcm":
        raise ValidationError("Для снимка КТ загрузите DICOM-файл (.dcm).")
    if not upload.size or upload.size > settings.MAX_UPLOAD_BYTES:
        raise ValidationError("DICOM-файл должен быть непустым и не больше 200 МБ.")
    try:
        upload.seek(0)
        dataset = pydicom.dcmread(upload, force=False)
        syntax = dataset.file_meta.TransferSyntaxUID
        if dataset.Modality != "CT" or int(getattr(dataset, "NumberOfFrames", 1)) != 1:
            raise ValueError("A single CT image is required")
        rows, columns = int(dataset.Rows), int(dataset.Columns)
        bits = int(dataset.BitsAllocated)
        if not (0 < rows * columns <= 20_000_000 and rows > 0 and columns > 0):
            raise ValueError("Invalid image dimensions")
        if int(dataset.SamplesPerPixel) != 1 or dataset.PhotometricInterpretation not in ("MONOCHROME1", "MONOCHROME2"):
            raise ValueError("A monochrome CT image is required")
        if bits not in (8, 16, 32) or not (0 < int(dataset.BitsStored) <= bits):
            raise ValueError("Invalid bit depth")
        if int(dataset.HighBit) != int(dataset.BitsStored) - 1 or int(dataset.PixelRepresentation) not in (0, 1):
            raise ValueError("Invalid pixel encoding")
        pixels = dataset.PixelData
        if syntax.is_compressed:
            frames = iter(generate_frames(pixels, number_of_frames=1))
            if not next(frames, b"") or next(frames, None) is not None:
                raise ValueError("Invalid encapsulated image")
        else:
            expected = rows * columns * (bits // 8)
            if len(pixels) != expected + expected % 2:
                raise ValueError("Truncated pixel data")
    except (InvalidDicomError, BytesLengthException, AttributeError, KeyError, TypeError, ValueError, EOFError, OSError, struct.error, NotImplementedError):
        raise ValidationError("Некорректный DICOM: нужен один двумерный снимок КТ с пиксельными данными.")
    finally:
        upload.seek(0)


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
