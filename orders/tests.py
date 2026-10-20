import io
import shutil
import struct
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import Organization, User
from .admin import OrderAdmin, OrderAdminForm
from .forms import OrderForm
from .models import Order, OrderAccess, OrderFile, Service, Tariff, private_storage
from .services import accept_price, attach_file, create_order, propose_price, publish_result, review_result, transition, upload_result
from .validators import validate_attachment, validate_stl


def scan(name="scan.stl"):
    return SimpleUploadedFile(name, b"binary STL".ljust(80, b" ") + struct.pack("<I", 1) + struct.pack("<12fH", *([0.0] * 12), 0))


def picture():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
    return SimpleUploadedFile("correction.png", buffer.getvalue(), content_type="image/png")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class OrderTests(TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.storage_patch = patch.object(private_storage, "_location", self.directory)
        self.storage_patch.start()
        private_storage.__dict__.pop("location", None)
        private_storage.__dict__.pop("base_location", None)
        self.addCleanup(self.clean_storage)
        self.owner = User.objects.create_user("owner@example.com", "pass", first_name="Анна", email_verified=True)
        self.colleague = User.objects.create_user("colleague@example.com", "pass", first_name="Иван", company=self.owner.company, email_verified=True)
        self.other = User.objects.create_user("other@example.com", "pass", first_name="Пётр", email_verified=True)
        self.staff = User.objects.create_superuser("staff@example.com", "pass", first_name="Команда")
        self.designer = User.objects.create_user("designer@example.com", "pass", first_name="Дизайнер", is_staff=True, role="designer", email_verified=True)
        self.second_designer = User.objects.create_user("second@example.com", "pass", first_name="Другой дизайнер", is_staff=True, role="designer", email_verified=True)
        self.service = Service.objects.get(kind="crown")
        self.service.unit_price = Decimal("500.00")
        self.service.save()
        self.order = Order.objects.create(client=self.owner, service=self.service, teeth="16", units=1,
                                           estimated_price=500, designer=self.designer)
        attach_file(self.order, self.owner, scan("working.stl"), "working")
        self.client.force_login(self.owner)

    def clean_storage(self):
        self.storage_patch.stop()
        private_storage.__dict__.pop("location", None)
        private_storage.__dict__.pop("base_location", None)
        shutil.rmtree(self.directory)

    def payload(self, **extra):
        data = {"service": self.service.pk, "selected_teeth": ["16"], "material": "zirconia",
                "submission_key": str(uuid.uuid4()), "working": scan(), "review_order": "on"}
        data.update(extra)
        return data

    def implant_payload(self, **extra):
        return self.payload(service=Service.objects.get(kind="implant").pk, implant_type="ti_base",
                            implant_system="System", implant_platform="Platform", scanbody="Scanbody", abutment="Ti-base",
                            scanbody_on_working="on", **extra)

    def make_ready(self):
        transition(self.order.pk, self.staff, "in_progress")
        result = upload_result(self.order.pk, self.designer, scan("result.stl"))
        review_result(result.pk, self.designer)
        publish_result(result.pk, self.designer)
        transition(self.order.pk, self.designer, "ready")
        return result

    def test_four_services_and_no_manual_quantity(self):
        response = self.client.get(reverse("orders:create"))
        self.assertEqual(len(response.context["services"]), 4)
        self.assertNotContains(response, 'name="units"')
        self.assertNotContains(response, 'name="jaw"')
        self.assertContains(response, "data-order-builder")

    def test_create_and_retry_no_duplicate_and_quantity_cannot_be_forged(self):
        data = self.payload(selected_teeth=["16", "17"], units=999, estimated_price=1)
        response = self.client.post(reverse("orders:create"), data)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.exclude(pk=self.order.pk).get()
        self.assertEqual(order.case_number, f"CAD-{order.pk:06d}")
        self.assertEqual(order.organization, self.owner.company)
        self.assertEqual(order.units, 2)
        self.assertEqual(order.estimated_price, Decimal("1000.00"))
        response2 = self.client.post(reverse("orders:create"), {"submission_key": data["submission_key"]})
        self.assertEqual(response2.url, response.url)
        self.assertEqual(Order.objects.count(), 2)

    def test_multiple_teeth_same_kind_and_irrelevant_implant_fields_removed(self):
        self.client.post(reverse("orders:create"), self.payload(implant_system="Injected", implant_type="ti_base", abutment="Injected"))
        order = Order.objects.exclude(pk=self.order.pk).get()
        self.assertEqual(order.implant_type, "")
        self.assertEqual(order.implant_system, "")
        self.assertEqual(order.abutment, "")

    def test_bridge_count_overlap_invalid_fdi_and_span(self):
        service = Service.objects.get(kind="bridge")
        data = self.payload(service=service.pk, support_teeth=["35", "37"], pontic_teeth=["36"])
        self.assertEqual(self.client.post(reverse("orders:create"), data).status_code, 302)
        order = Order.objects.exclude(pk=self.order.pk).get()
        self.assertEqual(order.units, 3)
        self.assertEqual(order.teeth, "35, 36, 37")
        for supports, pontics in [(["35", "37"], ["35"]), (["35", "37"], ["99"]), (["35", "47"], ["36"]), (["35", "38"], ["36"])]:
            response = self.client.post(reverse("orders:create"), self.payload(service=service.pk, support_teeth=supports, pontic_teeth=pontics))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.assertEqual(Order.objects.count(), 2)

    def test_inlay_material_and_review_are_required(self):
        response = self.client.post(reverse("orders:create"), self.payload(service=Service.objects.get(kind="inlay").pk, material="", review_order=""))
        self.assertTrue({"inlay_type", "material", "review_order"}.issubset(response.context["form"].errors))

    def test_disabled_and_model_services_cannot_be_ordered(self):
        self.service.is_active = False
        self.service.save()
        for service in [self.service, Service.objects.get(kind="model")]:
            response = self.client.post(reverse("orders:create"), self.payload(service=service.pk))
            self.assertEqual(response.status_code, 200)
            self.assertIn("service", response.context["form"].errors)

    def test_tibase_working_scan_avoids_duplicate_upload(self):
        response = self.client.post(reverse("orders:create"), self.implant_payload())
        self.assertEqual(response.status_code, 302)
        order = Order.objects.exclude(pk=self.order.pk).get()
        self.assertTrue(order.scanbody_on_working)
        self.assertEqual(order.files.count(), 1)

    def test_tibase_missing_scan_requires_upload_or_unknown(self):
        data = self.implant_payload()
        data.pop("scanbody_on_working")
        response = self.client.post(reverse("orders:create"), data)
        self.assertIn("scanbody_file", response.context["form"].errors)

    def test_unknown_implant_parameters_block_production_until_review(self):
        data = self.implant_payload(implant_system_unknown="on")
        self.client.post(reverse("orders:create"), data)
        order = Order.objects.exclude(pk=self.order.pk).get()
        self.assertEqual(order.status, "needs_data")
        self.assertIsNone(order.deadline)
        order.designer = self.designer
        order.estimated_price = 600
        order.save()
        with self.assertRaises(ValidationError):
            transition(order.pk, self.staff, "in_progress")
        order.clarification_resolved = True
        order.implant_system = "Уточнённая система"
        order.save()
        transition(order.pk, self.staff, "in_progress")
        order.refresh_from_db()
        self.assertIsNotNone(order.accepted_at)
        self.assertEqual((order.deadline - order.accepted_at).total_seconds(), 86400)

    def test_ready_abutment_does_not_require_tibase_or_scanbody(self):
        data = self.payload(service=Service.objects.get(kind="implant").pk, implant_type="ready_abutment")
        self.assertEqual(self.client.post(reverse("orders:create"), data).status_code, 302)
        order = Order.objects.exclude(pk=self.order.pk).get()
        self.assertEqual(order.status, "new")
        self.assertEqual(order.scanbody, "")

    def test_quote_uses_individual_tariff_and_server_side_units(self):
        Tariff.objects.create(organization=self.owner.company, service=self.service, unit_price="275.50")
        response = self.client.post(reverse("orders:quote"), {"service": self.service.pk, "selected_teeth": ["16", "17"], "units": 9})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], "551.00")
        self.assertEqual(response.json()["units"], 2)

    def test_no_tariff_shows_unknown_price_and_cannot_start(self):
        self.service.unit_price = None
        self.service.save()
        self.client.post(reverse("orders:create"), self.payload())
        order = Order.objects.exclude(pk=self.order.pk).get()
        self.assertIsNone(order.estimated_price)
        order.designer = self.designer
        order.save()
        with self.assertRaises(ValidationError):
            transition(order.pk, self.staff, "in_progress")

    def test_free_units_shared_between_organization_accounts(self):
        organization = self.owner.company
        organization.free_crown_units = 3
        organization.save()
        first = self.payload(selected_teeth=["16", "17"])
        self.client.post(reverse("orders:create"), first)
        a = Order.objects.exclude(pk=self.order.pk).get()
        self.assertEqual(a.free_units, 2)
        self.assertEqual(a.estimated_price, 0)
        self.client.force_login(self.colleague)
        self.client.post(reverse("orders:create"), self.payload(selected_teeth=["26", "27"]))
        b = Order.objects.exclude(pk__in=[self.order.pk, a.pk]).get()
        self.assertEqual(b.free_units, 1)
        self.assertEqual(b.estimated_price, 500)
        organization.refresh_from_db()
        self.assertEqual(organization.free_crown_units, 0)
        transition(a.pk, self.staff, "cancelled", "Отказ до принятия")
        organization.refresh_from_db()
        self.assertEqual(organization.free_crown_units, 2)
        with self.assertRaises(ValidationError):
            transition(a.pk, self.staff, "cancelled", "Повтор")

    def test_same_company_name_does_not_grant_access_or_free_units(self):
        other = User.objects.create_user("clone@example.com", "pass", first_name="Clone", organization=self.owner.company.name)
        self.assertNotEqual(other.company_id, self.owner.company_id)
        self.assertEqual(other.company.free_crown_units, 0)

    def test_org_colleague_can_view_download_and_confirm(self):
        result = self.make_ready()
        self.client.force_login(self.colleague)
        self.assertEqual(self.client.get(reverse("orders:detail", args=[self.order.pk])).status_code, 200)
        response = self.client.get(reverse("orders:download", args=[result.pk]))
        self.assertEqual(response.status_code, 200)
        response.close()
        self.client.post(reverse("orders:action", args=[self.order.pk, "confirm"]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "completed")
        self.assertEqual(self.order.payment_status, "pending")

    def test_other_organization_denied_on_all_endpoints_and_audited(self):
        file = self.order.files.get()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("orders:detail", args=[self.order.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("orders:download", args=[file.pk])).status_code, 404)
        for name, args, data in [("comment", [self.order.pk], {"text": "x"}),
                                  ("add_file", [self.order.pk], {"purpose": "working", "file": scan()}),
                                  ("action", [self.order.pk, "confirm"], {})]:
            self.assertEqual(self.client.post(reverse(f"orders:{name}", args=args), data).status_code, 404)
        self.assertTrue(OrderAccess.objects.filter(actor=self.other, allowed=False).exists())

    def test_designer_only_sees_assigned_orders_in_cabinet_admin_and_files(self):
        foreign = Order.objects.create(client=self.other, service=self.service, designer=self.second_designer)
        foreign_file = attach_file(foreign, self.other, scan(), "working")
        self.client.force_login(self.designer)
        self.assertContains(self.client.get(reverse("orders:list")), self.order.case_number)
        self.assertNotContains(self.client.get(reverse("orders:list")), foreign.case_number)
        self.assertEqual(self.client.get(reverse("orders:detail", args=[foreign.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("orders:download", args=[foreign_file.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("admin:orders_order_change", args=[self.order.pk])).status_code, 200)
        response = self.client.get(reverse("admin:orders_order_change", args=[foreign.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse("admin:accounts_user_changelist")).status_code, 403)
        with self.assertRaises(ValidationError):
            upload_result(foreign.pk, self.designer, scan())

    def test_drafts_private_and_quality_review_required(self):
        transition(self.order.pk, self.staff, "in_progress")
        result = upload_result(self.order.pk, self.designer, scan("draft.stl"))
        with self.assertRaises(ValidationError):
            publish_result(result.pk, self.designer)
        self.assertEqual(self.client.get(reverse("orders:download", args=[result.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("orders:detail", args=[self.order.pk])), result.original_name)
        review_result(result.pk, self.designer)
        publish_result(result.pk, self.designer)
        transition(self.order.pk, self.designer, "ready")
        self.assertEqual(self.client.get(reverse("orders:download", args=[result.pk])).status_code, 200)

    def test_quality_status_hidden_from_customer(self):
        transition(self.order.pk, self.staff, "in_progress")
        transition(self.order.pk, self.designer, "quality")
        self.assertNotContains(self.client.get(reverse("orders:detail", args=[self.order.pk])), "На проверке качества")

    def test_designer_cannot_confirm_result_on_behalf_of_customer(self):
        self.make_ready()
        with self.assertRaises(ValidationError):
            transition(self.order.pk, self.designer, "completed")

    def test_price_proposal_requires_customer_acceptance_and_blocks_stale_price(self):
        propose_price(self.order.pk, self.staff, Decimal("750"), "Нестандартный случай")
        with self.assertRaises(ValidationError):
            transition(self.order.pk, self.staff, "in_progress")
        with self.assertRaises(ValidationError):
            accept_price(self.order.pk, self.colleague, Decimal("700"))
        accept_price(self.order.pk, self.colleague, Decimal("750"))
        transition(self.order.pk, self.staff, "in_progress")
        self.order.refresh_from_db()
        self.assertEqual(self.order.effective_price, 750)
        self.assertEqual(self.order.payment_status, "after_review")

    def test_revision_with_image_keeps_previous_file_and_requires_new_version(self):
        first = self.make_ready()
        response = self.client.post(reverse("orders:action", args=[self.order.pk, "revision"]), {"text": "Исправить контакт", "file": picture()})
        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "revision")
        self.assertEqual(self.order.files.filter(purpose="explanation").count(), 1)
        with self.assertRaises(ValidationError):
            transition(self.order.pk, self.staff, "ready")
        second = upload_result(self.order.pk, self.designer, scan("v2.stl"))
        self.assertEqual(second.version, 2)
        self.assertNotEqual(first.file.name, second.file.name)
        review_result(second.pk, self.designer)
        publish_result(second.pk, self.designer)
        transition(self.order.pk, self.designer, "ready")
        response = self.client.get(reverse("orders:detail", args=[self.order.pk]))
        self.assertEqual(list(response.context["result_files"]), [second, first])
        self.assertTrue(all(file.purpose != "result" for file in response.context["source_files"]))

    def test_invalid_revision_does_not_change_status(self):
        self.make_ready()
        response = self.client.post(reverse("orders:action", args=[self.order.pk, "revision"]), {"text": "", "file": picture()})
        self.assertEqual(response["X-Form-Errors"], "1")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "ready")

    def test_safe_upload_validation(self):
        validate_stl(scan())
        validate_attachment(picture())
        for bad in [SimpleUploadedFile("bad.stl", b"not STL"), SimpleUploadedFile("bad.png", b"<script>"),
                    SimpleUploadedFile("bad.exe", b"x"), SimpleUploadedFile("bad.stl", b"solid\nendsolid\n")]:
            with self.assertRaises(ValidationError):
                validate_attachment(bad)
        upload = scan()
        upload.size = 201 * 1024 * 1024
        with self.assertRaises(ValidationError):
            validate_stl(upload)

    @override_settings(ORGANIZATION_STORAGE_BYTES=150)
    def test_organization_storage_limit(self):
        with self.assertRaises(ValidationError):
            attach_file(self.order, self.owner, scan(), "working")

    @override_settings(WRITE_REQUESTS_PER_MINUTE=1)
    def test_request_rate_limit(self):
        url = reverse("orders:comment", args=[self.order.pk])
        self.assertEqual(self.client.post(url, {"text": "first"}).status_code, 302)
        self.assertEqual(self.client.post(url, {"text": "second"}).status_code, 429)

    def test_retention_is_preview_by_default_and_only_deletes_closed_orders(self):
        from datetime import timedelta
        from django.core.management import call_command
        from django.utils import timezone
        file = self.order.files.get()
        Order.objects.filter(pk=self.order.pk).update(status="completed", updated_at=timezone.now() - timedelta(days=400))
        active = Order.objects.create(client=self.owner, service=self.service)
        Order.objects.filter(pk=active.pk).update(updated_at=timezone.now() - timedelta(days=400))
        call_command("purge_orders", older_than_days=365, stdout=io.StringIO())
        self.assertTrue(Order.objects.filter(pk=self.order.pk).exists())
        with self.captureOnCommitCallbacks(execute=True):
            call_command("purge_orders", older_than_days=365, execute=True, stdout=io.StringIO())
        self.assertFalse(Order.objects.filter(pk=self.order.pk).exists())
        self.assertTrue(Order.objects.filter(pk=active.pk).exists())
        self.assertFalse(private_storage.exists(file.file.name))

    def test_failed_submission_rolls_back_files_and_free_units(self):
        organization = self.owner.company
        organization.free_crown_units = 3
        organization.save()
        data = self.payload()
        upload = data.pop("working")
        form = OrderForm(data, {"working": upload})
        self.assertTrue(form.is_valid(), form.errors)
        before = {p for p in Path(self.directory).rglob("*") if p.is_file()}
        with patch("orders.services.event", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                create_order(self.owner, form)
        organization.refresh_from_db()
        self.assertEqual(organization.free_crown_units, 3)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual({p for p in Path(self.directory).rglob("*") if p.is_file()}, before)

    def test_admin_form_and_model_actions(self):
        request = RequestFactory().post("/admin/")
        request.user = self.staff
        model_admin = OrderAdmin(Order, AdminSite())

        def save(data, files=None):
            self.order.refresh_from_db()
            form = OrderAdminForm({"payment_status": self.order.payment_status, "designer": self.designer.pk, **data}, files, instance=self.order, actor=self.staff)
            self.assertTrue(form.is_valid(), form.errors)
            model_admin.save_model(request, form.save(commit=False), form, True)

        save({"next_status": "in_progress"})
        save({}, {"result_upload": scan()})
        result = self.order.files.get(purpose="result")
        invalid = OrderAdminForm({"payment_status": "after_review", "designer": self.designer.pk, "publish_file": result.pk}, instance=self.order, actor=self.staff)
        self.assertFalse(invalid.is_valid())
        save({"publish_file": result.pk, "quality_confirmed": "on", "next_status": "ready"})
        save({"next_status": "completed"})
        save({"payment_status": "paid"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")

    def test_admin_pages_and_client_is_not_staff(self):
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)
        self.client.force_login(self.staff)
        for name, args in [("admin:accounts_user_changelist", []), ("admin:orders_order_change", [self.order.pk]),
                           ("admin:accounts_user_add", []), ("admin:orders_tariff_changelist", []), ("admin:orders_orderaccess_changelist", [])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)

    def test_anonymous_redirect_and_private_storage_not_public(self):
        self.client.logout()
        response = self.client.get(reverse("orders:create"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("next=/orders/new/", response.url)
        self.assertEqual(self.client.get("/private_uploads/scan.stl").status_code, 404)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class BackupTests(TransactionTestCase):
    def test_backup_restores_order_and_its_private_file(self):
        import sqlite3
        from .backups import create_backup, restore_backup
        with tempfile.TemporaryDirectory() as directory, override_settings(PRIVATE_STORAGE_ROOT=Path(directory) / "uploads"):
            with patch.object(private_storage, "_location", str(Path(directory) / "uploads")):
                private_storage.__dict__.pop("location", None)
                private_storage.__dict__.pop("base_location", None)
                try:
                    owner = User.objects.create_user("backup@example.com", "pass", first_name="Тест")
                    service, _ = Service.objects.get_or_create(kind="crown", defaults={"name": "Коронка"})
                    order = Order.objects.create(client=owner, service=service)
                    original = scan()
                    expected = original.read()
                    original.seek(0)
                    file = attach_file(order, owner, original, "working")
                    archive = create_backup(Path(directory) / "backup.tar.gz")
                    restored = restore_backup(archive, Path(directory) / "restored")
                    with sqlite3.connect(restored / "database.sqlite3") as database:
                        self.assertEqual(database.execute("SELECT public_id FROM orders_order WHERE id=?", [order.pk]).fetchone()[0], order.case_number)
                    self.assertEqual((restored / "private_uploads" / file.file.name).read_bytes(), expected)
                    self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
                    from django.core.management.base import CommandError
                    with self.assertRaises(CommandError):
                        restore_backup(archive, restored)
                finally:
                    private_storage.__dict__.pop("location", None)
                    private_storage.__dict__.pop("base_location", None)


class MigrationTests(TransactionTestCase):
    def test_legacy_accounts_and_case_numbers_migrate_without_merging_names(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        old = [("accounts", "0001_initial"), ("orders", "0003_order_revision_requested_at_orderevent_is_internal")]
        try:
            executor.migrate(old)
            state = executor.loader.project_state(old).apps
            LegacyUser = state.get_model("accounts", "User")
            LegacyService = state.get_model("orders", "Service")
            LegacyOrder = state.get_model("orders", "Order")
            LegacyFile = state.get_model("orders", "OrderFile")
            a = LegacyUser.objects.create(email="legacy-a@example.com", first_name="A", organization="Same name")
            b = LegacyUser.objects.create(email="legacy-b@example.com", first_name="B", organization="Same name")
            staff = LegacyUser.objects.create(email="legacy-staff@example.com", first_name="Team", is_staff=True)
            service, _ = LegacyService.objects.update_or_create(kind="model", defaults={"name": "Legacy model", "is_active": True})
            LegacyService.objects.update_or_create(kind="crown", defaults={"name": "Полноанатомическая коронка"})
            order = LegacyOrder.objects.create(client=a, service=service, teeth="16")
            stored_name = f"orders/{order.pk}/{'a' * 32}.stl"
            legacy_file = LegacyFile.objects.create(order=order, uploaded_by=a, file=stored_name,
                                                     original_name="legacy_source_name.stl", size=134, purpose="working")
            executor = MigrationExecutor(connection)
            executor.migrate(leaves)
            first, second = User.objects.get(pk=a.pk), User.objects.get(pk=b.pk)
            self.assertNotEqual(first.company_id, second.company_id)
            self.assertEqual(User.objects.get(pk=staff.pk).role, "admin")
            migrated = Order.objects.get(pk=order.pk)
            self.assertEqual(migrated.case_number, f"CASE-{order.pk:06d}")
            self.assertEqual(migrated.organization_id, first.company_id)
            self.assertFalse(Service.objects.get(pk=service.pk).is_active)
            self.assertEqual(Service.objects.get(kind="crown").name, "Коронка")
            migrated_file = OrderFile.objects.get(pk=legacy_file.pk)
            self.assertEqual(migrated_file.original_name, "a" * 32 + ".stl")
            self.assertEqual(migrated_file.file.name, stored_name)
        finally:
            MigrationExecutor(connection).migrate(leaves)
