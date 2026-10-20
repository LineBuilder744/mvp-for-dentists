import hashlib
import os
import struct
import tempfile
import uuid
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from .models import Order, OrderFile, Service, private_storage


def binary_stl():
    header = b"Dental CAD regression test".ljust(80, b" ")
    triangle = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    return header + struct.pack("<I", 1) + triangle


ASCII_STL = b"""solid test
facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 1 0 0
vertex 0 1 0
endloop
endfacet
endsolid test
"""


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class STLUploadTests(TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.storage_root = Path(directory)
        self.enterContext(patch.object(private_storage, "_location", directory))
        self.clear_storage_cache()
        self.addCleanup(self.clear_storage_cache)
        self.owner = User.objects.create_user("stl-client@example.test", "test-password", first_name="Test", email_verified=True)
        self.service = Service.objects.get(kind="crown")
        self.client.force_login(self.owner)

    @staticmethod
    def clear_storage_cache():
        private_storage.__dict__.pop("location", None)
        private_storage.__dict__.pop("base_location", None)

    def payload(self, data, name="source_scan.STL", **extra):
        return {
            "submission_key": str(uuid.uuid4()), "service": self.service.pk,
            "selected_teeth": ["16"], "material": "zirconia", "review_order": "on",
            "working": SimpleUploadedFile(name, data, content_type="application/octet-stream"), **extra,
        }

    def submit(self, data, name="source_scan.STL", **extra):
        response = self.client.post(reverse("orders:create"), self.payload(data, name, **extra))
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(pk=int(response.url.rstrip("/").split("/")[-1]))
        return order.files.get(purpose=OrderFile.Purpose.WORKING)

    def assert_round_trip(self, file, expected):
        self.assertRegex(file.file.name, rf"^orders/{file.order_id}/[0-9a-f]{{32}}\.stl$")
        self.assertEqual(file.size, len(expected))
        self.assertEqual(file.uploaded_by_id, self.owner.pk)
        with file.file.open("rb") as stored:
            self.assertEqual(stored.read(), expected)
        response = self.client.get(reverse("orders:download", args=[file.pk]))
        self.assertEqual(response.status_code, 200)
        try:
            downloaded = b"".join(response.streaming_content)
            self.assertEqual(len(downloaded), len(expected))
            self.assertEqual(hashlib.sha256(downloaded).digest(), hashlib.sha256(expected).digest())
            self.assertEqual(response["Cache-Control"], "private, no-store")
        finally:
            response.close()

    def test_binary_stl_round_trip_has_opaque_storage_name(self):
        data = binary_stl()
        self.assert_round_trip(self.submit(data), data)

    def test_ascii_stl_round_trip(self):
        self.assert_round_trip(self.submit(ASCII_STL), ASCII_STL)

    def test_identical_source_names_do_not_overwrite_previous_upload(self):
        first_data = binary_stl()
        second_data = ASCII_STL
        first = self.submit(first_data, "same_scan.stl")
        second = self.submit(second_data, "same_scan.stl")
        self.assertNotEqual(first.file.name, second.file.name)
        self.assert_round_trip(first, first_data)
        self.assert_round_trip(second, second_data)

    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1)
    def test_all_scan_roles_accept_disk_backed_uploads(self):
        data = binary_stl()
        working = self.submit(data, service=Service.objects.get(kind="implant").pk, implant_type="ti_base",
            implant_system="Test system", implant_platform="Test platform", scanbody="Test scanbody", abutment="Test Ti-base",
            antagonist=SimpleUploadedFile("antagonist.stl", data),
            bite=SimpleUploadedFile("bite.stl", data), scanbody_file=SimpleUploadedFile("scanbody.stl", data))
        files = list(working.order.files.order_by("pk"))
        self.assertEqual({file.purpose for file in files}, {"working", "antagonist", "bite", "scanbody"})
        self.assertEqual(len({file.file.name for file in files}), 4)
        for file in files:
            with self.subTest(purpose=file.purpose):
                self.assert_round_trip(file, data)

    def test_invalid_stl_does_not_create_an_order_or_stored_file(self):
        for data, name in [(b"", "empty.stl"), (binary_stl()[:-1], "truncated.stl"),
                           (b"not an STL file", "renamed.stl"), (binary_stl(), "scan.exe")]:
            with self.subTest(name=name):
                response = self.client.post(reverse("orders:create"), self.payload(data, name))
                self.assertEqual(response.status_code, 200)
                self.assertIn("working", response.context["form"].errors)
                self.assertEqual(Order.objects.count(), 0)
                self.assertEqual(OrderFile.objects.count(), 0)
                self.assertFalse(any(path.is_file() for path in self.storage_root.rglob("*")))

    @skipUnless(os.environ.get("CAD_TEST_STL_PATH"), "Set CAD_TEST_STL_PATH to test a private real-world STL")
    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1)
    def test_supplied_real_stl_round_trip(self):
        path = Path(os.environ["CAD_TEST_STL_PATH"])
        data = path.read_bytes()
        self.assertGreater(len(data), 0)
        self.assert_round_trip(self.submit(data, path.name), data)
