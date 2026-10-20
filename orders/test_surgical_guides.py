import hashlib
import io
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Order, OrderFile, Service, private_storage
from .services import transition
from .test_stl_uploads import binary_stl
from accounts.models import User
from PIL import Image
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.encaps import encapsulate
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, JPEGBaseline8Bit, generate_uid


def dicom_scan(compressed=False, **changes):
    metadata = FileMetaDataset()
    metadata.TransferSyntaxUID = ExplicitVRLittleEndian
    metadata.MediaStorageSOPClassUID = CTImageStorage
    metadata.MediaStorageSOPInstanceUID = generate_uid()
    dataset = FileDataset(None, {}, file_meta=metadata, preamble=b"\0" * 128)
    dataset.SOPClassUID = CTImageStorage
    dataset.SOPInstanceUID = metadata.MediaStorageSOPInstanceUID
    dataset.Modality = "CT"
    dataset.Rows, dataset.Columns = 2, 2
    dataset.SamplesPerPixel = 1
    dataset.PhotometricInterpretation = "MONOCHROME2"
    dataset.BitsAllocated = 16
    dataset.BitsStored = 12
    dataset.HighBit = 11
    dataset.PixelRepresentation = 0
    dataset.PixelData = b"\x01\0" * 4
    if compressed:
        metadata.TransferSyntaxUID = JPEGBaseline8Bit
        dataset.BitsAllocated = dataset.BitsStored = 8
        dataset.HighBit = 7
        image = io.BytesIO()
        Image.new("L", (2, 2), color=128).save(image, "JPEG")
        dataset.PixelData = encapsulate([image.getvalue()])
        dataset["PixelData"].is_undefined_length = True
    for name, value in changes.items():
        if value is None:
            delattr(dataset, name)
        else:
            setattr(dataset, name, value)
    output = io.BytesIO()
    dataset.save_as(output, enforce_file_format=True)
    return SimpleUploadedFile("ct-scan.dcm", output.getvalue(), content_type="application/dicom")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SurgicalGuideTests(TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.enterContext(patch.object(private_storage, "_location", directory))
        self.clear_storage_cache()
        self.addCleanup(self.clear_storage_cache)
        self.owner = User.objects.create_user("guide@example.test", "pass", first_name="Test", email_verified=True)
        self.service = Service.objects.get(kind="surgical_guide")
        self.client.force_login(self.owner)

    @staticmethod
    def clear_storage_cache():
        private_storage.__dict__.pop("location", None)
        private_storage.__dict__.pop("base_location", None)

    def payload(self, **extra):
        return {"service": self.service.pk, "submission_key": str(uuid.uuid4()), "review_order": "on",
                "working": SimpleUploadedFile("source.stl", binary_stl()), "ct_image": dicom_scan(), **extra}

    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1)
    def test_two_required_files_are_saved_privately_with_anonymous_names(self):
        data = self.payload()
        image_bytes = data["ct_image"].read()
        data["ct_image"].seek(0)
        self.assertEqual(self.client.post(reverse("orders:create"), data).status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.units, 1)
        self.assertEqual(order.teeth, "")
        self.assertEqual(order.material, "")
        self.assertEqual(order.files.count(), 2)
        files = {file.purpose: file for file in order.files.all()}
        self.assertEqual(set(files), {"working", "ct"})
        for purpose, contents in [("working", binary_stl()), ("ct", image_bytes)]:
            file = files[purpose]
            self.assertRegex(file.original_name, r"^[0-9a-f]{32}\.(stl|dcm)$")
            response = self.client.get(reverse("orders:download", args=[file.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(hashlib.sha256(b"".join(response.streaming_content)).digest(), hashlib.sha256(contents).digest())
            response.close()
        response = self.client.get(reverse("orders:detail", args=[order.pk]))
        self.assertContains(response, "Снимок КТ (2D)")
        self.assertContains(response, "Снимок STL")
        self.assertNotContains(response, "source.stl")

    def test_either_missing_file_prevents_order_creation(self):
        for name in ("working", "ct_image"):
            with self.subTest(field=name):
                data = self.payload()
                data.pop(name)
                response = self.client.post(reverse("orders:create"), data)
                self.assertIn(name, response.context["form"].errors)
                self.assertEqual(Order.objects.count(), 0)
                self.assertEqual(OrderFile.objects.count(), 0)
                self.assertFalse(any(path.is_file() for path in self.root.rglob("*")))

    def test_ct_requires_valid_dicom_content(self):
        for name, content in [("renamed.dcm", b"not DICOM"), ("scan.stl", binary_stl()), ("scan.pdf", b"%PDF")]:
            with self.subTest(name=name):
                response = self.client.post(reverse("orders:create"), self.payload(ct_image=SimpleUploadedFile(name, content)))
                self.assertIn("ct_image", response.context["form"].errors)
                self.assertFalse(Order.objects.exists())

    def test_ct_rejects_wrong_modality_multiple_frames_and_missing_pixels(self):
        for changes in [{"Modality": "MR"}, {"NumberOfFrames": 2}, {"PixelData": None}, {"PixelData": b"\0\0"}]:
            with self.subTest(changes=list(changes)):
                response = self.client.post(reverse("orders:create"), self.payload(ct_image=dicom_scan(**changes)))
                self.assertIn("ct_image", response.context["form"].errors)
                self.assertFalse(Order.objects.exists())

    def test_malformed_dicom_element_is_a_form_error(self):
        data = dicom_scan().read()
        rows = b"\x28\x00\x10\x00US\x02\x00\x02\x00"
        self.assertIn(rows, data)
        data = data.replace(rows, b"\x28\x00\x10\x00US\x03\x00\x02\x00\x00", 1)
        response = self.client.post(reverse("orders:create"), self.payload(ct_image=SimpleUploadedFile("broken.dcm", data)))
        self.assertIn("ct_image", response.context["form"].errors)
        self.assertFalse(Order.objects.exists())

    def test_requested_dicom_can_be_added_to_guide_order(self):
        self.client.post(reverse("orders:create"), self.payload())
        order = Order.objects.get()
        order.status = "needs_data"
        order.save()
        response = self.client.post(reverse("orders:add_file", args=[order.pk]), {"purpose": "ct", "file": dicom_scan()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(order.files.filter(purpose="ct").count(), 2)

    def test_single_frame_compressed_dicom_is_accepted(self):
        response = self.client.post(reverse("orders:create"), self.payload(ct_image=dicom_scan(compressed=True)))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OrderFile.objects.filter(purpose="ct").count(), 1)

    @override_settings(MAX_UPLOAD_BYTES=200)
    def test_dicom_size_limit_is_enforced(self):
        response = self.client.post(reverse("orders:create"), self.payload())
        self.assertIn("ct_image", response.context["form"].errors)
        self.assertFalse(Order.objects.exists())

    def test_quote_is_for_one_template_and_does_not_use_crown_promotion(self):
        self.service.unit_price = 1200
        self.service.save()
        self.owner.company.free_crown_units = 3
        self.owner.company.save()
        response = self.client.post(reverse("orders:quote"), {"service": self.service.pk, "selected_teeth": ["16", "17"], "units": 20})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["units"], 1)
        self.assertEqual(response.json()["total"], "1200.00")
        self.assertEqual(response.json()["free_units"], 0)

    def test_unrelated_parameters_and_extra_uploads_are_not_saved(self):
        response = self.client.post(reverse("orders:create"), self.payload(selected_teeth=["16"], material="zirconia",
            antagonist=SimpleUploadedFile("unused.stl", binary_stl()), additional_files=[SimpleUploadedFile("extra.stl", binary_stl())]))
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.teeth, "")
        self.assertEqual(order.material, "")
        self.assertEqual(order.files.count(), 2)

    def test_other_organization_cannot_download_ct(self):
        self.client.post(reverse("orders:create"), self.payload())
        file = OrderFile.objects.get(purpose="ct")
        other = User.objects.create_user("other-guide@example.test", "pass", first_name="Other", email_verified=True)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("orders:download", args=[file.pk])).status_code, 404)

    def test_production_requires_ct(self):
        self.client.post(reverse("orders:create"), self.payload())
        order = Order.objects.get()
        order.files.filter(purpose="ct").delete()
        admin = User.objects.create_superuser("guide-admin@example.test", "pass", first_name="Admin")
        with self.assertRaisesMessage(ValidationError, "снимок КТ"):
            transition(order.pk, admin, "in_progress")
