import shutil
import struct
import tempfile
import uuid
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.contrib.admin.sites import AdminSite
from django.urls import reverse

from accounts.models import User
from .admin import OrderAdmin, OrderAdminForm
from .forms import OrderForm
from .models import Comment, Order, OrderFile, Service, private_storage
from .services import attach_file, create_order, publish_result, transition, upload_result
from .validators import validate_stl


def scan(name="scan.stl"):
    return SimpleUploadedFile(name, b"binary STL".ljust(80, b" ") + struct.pack("<I", 1) + struct.pack("<12fH", *([0.0] * 12), 0))


class OrderTests(TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.storage_patch = patch.object(private_storage, "_location", self.directory)
        self.storage_patch.start()
        private_storage.__dict__.pop("location", None)
        private_storage.__dict__.pop("base_location", None)
        self.addCleanup(self.clean_storage)
        self.owner = User.objects.create_user("owner@example.com", "pass", first_name="Анна", email_verified=True)
        self.other = User.objects.create_user("other@example.com", "pass", first_name="Иван", email_verified=True)
        self.staff = User.objects.create_superuser("staff@example.com", "pass", first_name="Команда")
        self.service = Service.objects.get(kind="crown")
        self.order = Order.objects.create(client=self.owner, service=self.service, teeth="16", units=1)
        self.client.force_login(self.owner)

    def clean_storage(self):
        self.storage_patch.stop()
        private_storage.__dict__.pop("location", None)
        private_storage.__dict__.pop("base_location", None)
        shutil.rmtree(self.directory)

    def payload(self, **extra):
        data = {"service": self.service.pk, "teeth": "16", "units": 1, "submission_key": str(uuid.uuid4()), "working": scan()}
        data.update(extra)
        return data

    def test_create_and_retry_no_duplicate(self):
        data = self.payload()
        response = self.client.post(reverse("orders:create"), data)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.exclude(pk=self.order.pk).get()
        self.assertEqual(order.case_number, f"CASE-{order.pk:06d}")
        self.assertEqual(order.files.count(), 1)
        response2 = self.client.post(reverse("orders:create"), {"submission_key": data["submission_key"]})
        self.assertEqual(response2.url, response.url)
        self.assertEqual(Order.objects.count(), 2)

    def test_order_list_only_own_and_search(self):
        foreign = Order.objects.create(client=self.other, service=self.service)
        response = self.client.get(reverse("orders:list"))
        self.assertContains(response, self.order.case_number)
        self.assertNotContains(response, foreign.case_number)
        response = self.client.get(reverse("orders:list"), {"q": self.order.case_number})
        self.assertContains(response, self.order.case_number)

    def test_source_and_result_sections_keep_drafts_private(self):
        source = attach_file(self.order, self.owner, scan("source.stl"), OrderFile.Purpose.WORKING)
        result = upload_result(self.order.pk, self.staff, scan("result.stl"))
        draft = upload_result(self.order.pk, self.staff, scan("draft.stl"))
        publish_result(result.pk, self.staff)
        response = self.client.get(reverse("orders:detail", args=[self.order.pk]))
        self.assertEqual(list(response.context["source_files"]), [source])
        self.assertEqual(list(response.context["result_files"]), [result])
        self.assertContains(response, "Ваши файлы")
        self.assertContains(response, "Результаты CAD")
        self.assertNotContains(response, "draft.stl")
        self.client.force_login(self.staff)
        response = self.client.get(reverse("orders:detail", args=[self.order.pk]))
        self.assertEqual(list(response.context["result_files"]), [draft, result])

    def test_disabled_service_and_service_validation(self):
        self.service.is_active = False
        self.service.save()
        response = self.client.post(reverse("orders:create"), self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 1)
        implant = Service.objects.get(kind="implant")
        response = self.client.post(reverse("orders:create"), self.payload(service=implant.pk))
        self.assertContains(response, "Обязательно для коронки")
        model = Service.objects.get(kind="model")
        response = self.client.post(reverse("orders:create"), self.payload(service=model.pk, teeth=""))
        self.assertContains(response, "Выберите челюсть")

    def test_private_access_on_every_endpoint(self):
        file = upload_result(self.order.pk, self.staff, scan())
        self.client.force_login(self.other)
        for name, kwargs in [("detail", {"pk": self.order.pk}), ("download", {"pk": file.pk})]:
            self.assertEqual(self.client.get(reverse(f"orders:{name}", kwargs=kwargs)).status_code, 404)
        for name, kwargs, data in [
            ("comment", {"pk": self.order.pk}, {"text": "Чужой комментарий"}),
            ("add_file", {"pk": self.order.pk}, {"purpose": "working", "file": scan()}),
            ("action", {"pk": self.order.pk, "action": "confirm"}, {}),
        ]:
            self.assertEqual(self.client.post(reverse(f"orders:{name}", kwargs=kwargs), data).status_code, 404)
        self.assertEqual(Comment.objects.count(), 0)

    def test_publication_payment_and_revision_cycle(self):
        transition(self.order.pk, self.staff, Order.Status.IN_PROGRESS)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.deadline)
        with self.assertRaises(ValidationError):
            transition(self.order.pk, self.staff, Order.Status.READY)
        first = upload_result(self.order.pk, self.staff, scan("first.stl"))
        self.assertEqual(self.client.get(reverse("orders:download", args=[first.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("orders:detail", args=[self.order.pk])), "first.stl")
        publish_result(first.pk, self.staff)
        transition(self.order.pk, self.staff, Order.Status.READY)
        response = self.client.get(reverse("orders:download", args=[first.pk]))
        self.assertEqual(response.status_code, 200)
        response.close()
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.Payment.AFTER_REVIEW)
        transition(self.order.pk, self.owner, Order.Status.REVISION, "Исправить контакт")
        with self.assertRaises(ValidationError):
            transition(self.order.pk, self.staff, Order.Status.READY)
        second = upload_result(self.order.pk, self.staff, scan("second.stl"))
        self.assertEqual(second.version, 2)
        self.assertNotEqual(first.file.name, second.file.name)
        publish_result(second.pk, self.staff)
        transition(self.order.pk, self.staff, Order.Status.READY)
        transition(self.order.pk, self.owner, Order.Status.COMPLETED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.Payment.PENDING)
        self.assertIsNotNone(self.order.confirmed_at)
        self.assertEqual(self.order.files.count(), 2)

    def test_status_permissions_reasons_and_closed_order(self):
        with self.assertRaises(ValidationError):
            transition(self.order.pk, self.owner, Order.Status.IN_PROGRESS)
        with self.assertRaises(ValidationError):
            transition(self.order.pk, self.staff, Order.Status.NEEDS_DATA)
        transition(self.order.pk, self.staff, Order.Status.CANCELLED, "Отказ клиента")
        with self.assertRaises(ValidationError):
            upload_result(self.order.pk, self.staff, scan())

    def test_extra_processing_clears_deadline_and_records_reason(self):
        transition(self.order.pk, self.staff, Order.Status.IN_PROGRESS)
        transition(self.order.pk, self.staff, Order.Status.EXTRA, "Сложная геометрия")
        self.order.refresh_from_db()
        self.assertIsNone(self.order.deadline)
        self.assertTrue(self.order.events.filter(description__contains="Сложная геометрия").exists())

    def test_source_upload_only_when_requested(self):
        url = reverse("orders:add_file", args=[self.order.pk])
        self.client.post(url, {"purpose": "working", "file": scan()})
        self.assertEqual(self.order.files.count(), 0)
        transition(self.order.pk, self.staff, Order.Status.NEEDS_DATA, "Нужен прикус")
        self.client.post(url, {"purpose": "bite", "file": scan()})
        self.assertEqual(self.order.files.count(), 1)

    def test_stl_validation(self):
        validate_stl(scan())
        ascii_file = SimpleUploadedFile("ascii.stl", b"solid test\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid test\n")
        validate_stl(ascii_file)
        for bad in [SimpleUploadedFile("bad.stl", b"not STL"), SimpleUploadedFile("bad.exe", b"x"), SimpleUploadedFile("bad.stl", b"solid\nendsolid\n")]:
            with self.assertRaises(ValidationError):
                validate_stl(bad)
        upload = scan()
        upload.size = 201 * 1024 * 1024
        with self.assertRaises(ValidationError):
            validate_stl(upload)

    def test_failed_order_rolls_back_database_and_files(self):
        data = self.payload()
        upload = data.pop("working")
        form = OrderForm(data, {"working": upload})
        self.assertTrue(form.is_valid(), form.errors)
        with patch("orders.services.event", side_effect=RuntimeError("event failed")):
            with self.assertRaises(RuntimeError):
                create_order(self.owner, form)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(OrderFile.objects.count(), 0)
        from pathlib import Path
        self.assertFalse(any(p.is_file() for p in Path(self.directory).rglob("*")))

    def test_admin_cannot_mark_paid_before_confirmation(self):
        form = OrderAdminForm({"payment_status": "paid"}, instance=self.order)
        self.assertFalse(form.is_valid())
        self.assertIn("payment_status", form.errors)

    def test_admin_status_result_and_payment_actions(self):
        request = RequestFactory().post("/admin/")
        request.user = self.staff
        model_admin = OrderAdmin(Order, AdminSite())

        def save(data, files=None):
            self.order.refresh_from_db()
            form = OrderAdminForm({"payment_status": self.order.payment_status, **data}, files, instance=self.order)
            self.assertTrue(form.is_valid(), form.errors)
            model_admin.save_model(request, form.save(commit=False), form, True)

        save({"next_status": "in_progress"})
        save({}, {"result_upload": scan()})
        file = self.order.files.get()
        self.assertIsNone(file.published_at)
        save({"publish_file": file.pk, "next_status": "ready"})
        save({"next_status": "completed"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "pending")
        save({"payment_status": "paid"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")
        self.assertTrue(self.order.events.filter(description__contains="Оплачен").exists())

    def test_admin_pages_render(self):
        self.client.force_login(self.staff)
        for name, args in [("admin:accounts_user_changelist", []), ("admin:orders_order_changelist", []), ("admin:orders_order_change", [self.order.pk]), ("admin:accounts_user_add", [])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)

    def test_anonymous_redirect_and_no_media_route(self):
        self.client.logout()
        response = self.client.get(reverse("orders:create"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("next=/orders/new/", response.url)
        self.assertEqual(self.client.get("/private_uploads/scan.stl").status_code, 404)
