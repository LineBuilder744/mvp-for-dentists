from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from accounts.models import Organization
from .access import can_manage, is_customer
from .forms import SUPPORTED_KINDS
from .models import Order, OrderEvent, OrderFile, Service
from .pricing import quote
from .validators import validate_attachment, validate_stl


def event(order, actor, description, is_internal=False):
    return OrderEvent.objects.create(order=order, actor=actor, description=description, is_internal=is_internal)


def check_storage(organization_id, extra_bytes):
    used = OrderFile.objects.filter(order__organization_id=organization_id).aggregate(size=Sum("size"))["size"] or 0
    if used + extra_bytes > settings.ORGANIZATION_STORAGE_BYTES:
        raise ValidationError("Лимит файлов организации исчерпан. Обратитесь к администратору.")


def attach_file(order, actor, upload, purpose, version=None):
    if not (can_manage(actor, order) or is_customer(actor, order)):
        raise ValidationError("Недостаточно прав.")
    if purpose in (OrderFile.Purpose.ADDITIONAL, OrderFile.Purpose.EXPLANATION):
        validate_attachment(upload)
    else:
        validate_stl(upload)
    check_storage(order.organization_id, upload.size)
    if purpose != OrderFile.Purpose.RESULT and order.files.exclude(purpose="result").count() >= settings.MAX_ATTACHMENTS_PER_ORDER:
        raise ValidationError("Не более 10 исходных файлов на заказ.")
    obj = OrderFile(order=order, uploaded_by=actor, original_name=Path(upload.name).name[:255],
                    size=upload.size, purpose=purpose, version=version)
    obj.file.save(upload.name, upload, save=False)
    try:
        obj.save()
    except Exception:
        obj.file.delete(save=False)
        raise
    return obj


def create_order(client, form):
    saved_files = []
    try:
        with transaction.atomic():
            client = get_user_model().objects.select_for_update().get(pk=client.pk)
            if client.is_staff or not client.company_id:
                raise ValidationError("Заказы создаются из клиентского кабинета организации.")
            organization = Organization.objects.select_for_update().get(pk=client.company_id)
            existing = Order.objects.filter(organization=organization, submission_key=form.cleaned_data["submission_key"]).first()
            if existing:
                return existing
            service = Service.objects.get(pk=form.cleaned_data["service"].pk)
            if not service.is_active or service.kind not in SUPPORTED_KINDS:
                raise ValidationError("Эта услуга больше недоступна.")
            order = form.save(commit=False)
            order.client = client
            order.organization = organization
            order.submission_key = form.cleaned_data["submission_key"]
            pricing = quote(organization, service, order.units)
            order.quoted_unit_price = pricing["unit_price"]
            order.estimated_price = pricing["total"]
            order.free_units = pricing["free_units"]
            if order.estimated_price == 0:
                order.payment_status = Order.Payment.NOT_REQUIRED
            if order.unknown_implant_fields:
                order.status = Order.Status.NEEDS_DATA
            organization.free_crown_units -= order.free_units
            organization.save(update_fields=["free_crown_units"])
            order.save()
            for field, purpose in [("working", "working"), ("antagonist", "antagonist"), ("bite", "bite"), ("scanbody_file", "scanbody")]:
                if form.cleaned_data.get(field):
                    saved_files.append(attach_file(order, client, form.cleaned_data[field], purpose))
            for upload in form.cleaned_data.get("additional_files", []):
                saved_files.append(attach_file(order, client, upload, OrderFile.Purpose.ADDITIONAL))
            event(order, client, "Заказ создан")
            if order.unknown_implant_fields:
                event(order, client, "Требуется уточнение имплантных параметров перед принятием в работу.")
            return order
    except Exception:
        for obj in saved_files:
            obj.file.delete(save=False)
        raise


TRANSITIONS = {
    Order.Status.NEW: {Order.Status.IN_PROGRESS, Order.Status.NEEDS_DATA, Order.Status.CANCELLED},
    Order.Status.IN_PROGRESS: {Order.Status.EXTRA, Order.Status.NEEDS_DATA, Order.Status.QUALITY, Order.Status.READY, Order.Status.CANCELLED},
    Order.Status.EXTRA: {Order.Status.IN_PROGRESS, Order.Status.NEEDS_DATA, Order.Status.QUALITY, Order.Status.READY, Order.Status.CANCELLED},
    Order.Status.NEEDS_DATA: {Order.Status.IN_PROGRESS, Order.Status.CANCELLED},
    Order.Status.QUALITY: {Order.Status.IN_PROGRESS, Order.Status.READY, Order.Status.REVISION, Order.Status.CANCELLED},
    Order.Status.READY: {Order.Status.REVISION, Order.Status.COMPLETED, Order.Status.CANCELLED},
    Order.Status.REVISION: {Order.Status.IN_PROGRESS, Order.Status.READY, Order.Status.NEEDS_DATA, Order.Status.EXTRA, Order.Status.QUALITY, Order.Status.CANCELLED},
}


def check_transition(order, actor, target, reason=""):
    if not can_manage(actor, order) and not (is_customer(actor, order) and target in (Order.Status.REVISION, Order.Status.COMPLETED)):
        raise ValidationError("Недостаточно прав.")
    if target not in TRANSITIONS.get(order.status, set()):
        raise ValidationError("Этот переход статуса недоступен.")
    if target == Order.Status.COMPLETED and not (actor.is_order_admin or is_customer(actor, order)):
        raise ValidationError("Результат подтверждает клиент либо администратор при ручном согласовании.")
    if target in (Order.Status.EXTRA, Order.Status.NEEDS_DATA, Order.Status.REVISION, Order.Status.CANCELLED) and not reason.strip():
        raise ValidationError("Укажите причину или конкретный запрос.")
    if target == Order.Status.IN_PROGRESS:
        if order.unknown_implant_fields and not order.clarification_resolved:
            raise ValidationError("Сначала уточните имплантные параметры и отметьте их проверку.")
        if order.implant_type == Order.ImplantType.TI_BASE:
            if not all(getattr(order, name).strip() for name in ("implant_system", "implant_platform", "scanbody", "abutment")):
                raise ValidationError("Для Ti-base необходимо заполнить все имплантные параметры.")
            if not order.scanbody_on_working and not order.files.filter(purpose="scanbody").exists():
                raise ValidationError("Нужен скан со scanbody или отметка о его наличии на рабочем скане.")
        if not order.files.filter(purpose="working").exists():
            raise ValidationError("Отсутствует рабочий скан.")
        if order.price_pending or order.effective_price is None:
            raise ValidationError("До начала работы клиент должен согласовать стоимость.")
        if not order.designer_id or not order.designer.is_designer or not order.designer.is_active or not order.designer.email_verified:
            raise ValidationError("Назначьте ответственного дизайнера.")
    if target in (Order.Status.READY, Order.Status.COMPLETED):
        if order.price_pending:
            raise ValidationError("Клиент ещё не подтвердил новую стоимость.")
    if target == Order.Status.READY:
        results = order.files.filter(purpose="result", published_at__isnull=False, reviewed_at__isnull=False)
        if order.revision_requested_at:
            results = results.filter(published_at__gt=order.revision_requested_at)
        if not results.exists():
            raise ValidationError("Сначала проверьте и опубликуйте готовый STL новой версии.")


@transaction.atomic
def transition(order_id, actor, target, reason=""):
    order = Order.objects.select_for_update().get(pk=order_id)
    check_transition(order, actor, target, reason)
    now = timezone.now()
    if target == Order.Status.REVISION:
        order.revision_requested_at = now
    if target == Order.Status.IN_PROGRESS and not order.accepted_at:
        order.accepted_at = now
        if not order.deadline:
            order.deadline = now + timedelta(hours=24)
    if target == Order.Status.EXTRA:
        order.deadline = None
    if target == Order.Status.CANCELLED and not order.accepted_at and order.free_units:
        organization = Organization.objects.select_for_update().get(pk=order.organization_id)
        organization.free_crown_units += order.free_units
        organization.save(update_fields=["free_crown_units"])
        event(order, actor, f"Возвращено бесплатных единиц: {order.free_units}")
    if target == Order.Status.COMPLETED:
        order.confirmed_at = now
        order.payment_status = Order.Payment.NOT_REQUIRED if order.effective_price == 0 else Order.Payment.PENDING
    previous = order.get_status_display()
    order.status = target
    order.save()
    event(order, actor, f"{previous} → {order.get_status_display()}" + (f": {reason.strip()}" if reason else ""),
          is_internal=target == Order.Status.QUALITY or previous == Order.Status.QUALITY.label)
    if target == Order.Status.IN_PROGRESS:
        event(order, actor, f"Ожидаемый срок: {timezone.localtime(order.deadline):%d.%m.%Y %H:%M}" if order.deadline else "Срок уточняется")
    if target == Order.Status.EXTRA:
        event(order, actor, "Срок уточняется")
    return order


@transaction.atomic
def propose_price(order_id, actor, price, reason):
    order = Order.objects.select_for_update().get(pk=order_id)
    if not actor.is_order_admin or price is None or price < 0 or not reason.strip():
        raise ValidationError("Нужны права администратора, неотрицательная цена и обоснование.")
    if order.status in (Order.Status.COMPLETED, Order.Status.CANCELLED):
        raise ValidationError("Заказ закрыт.")
    order.proposed_price = price
    order.price_reason = reason.strip()
    order.price_accepted_at = None
    order.status = Order.Status.NEEDS_DATA
    order.deadline = None
    order.save()
    event(order, actor, f"Предложена стоимость {price:.2f} ₽: {reason.strip()}. Работа приостановлена до согласования.")


@transaction.atomic
def accept_price(order_id, actor, expected_price):
    order = Order.objects.select_for_update().get(pk=order_id)
    if not is_customer(actor, order) or not order.price_pending or order.proposed_price != expected_price:
        raise ValidationError("Предложение изменилось или уже подтверждено. Обновите карточку.")
    if order.status in (Order.Status.COMPLETED, Order.Status.CANCELLED):
        raise ValidationError("Заказ закрыт.")
    order.price_accepted_at = timezone.now()
    order.payment_status = Order.Payment.NOT_REQUIRED if order.proposed_price == 0 else Order.Payment.AFTER_REVIEW
    order.save()
    event(order, actor, f"Клиент подтвердил стоимость {order.proposed_price:.2f} ₽")


@transaction.atomic
def upload_result(order_id, actor, upload):
    order = Order.objects.select_for_update().get(pk=order_id)
    if not can_manage(actor, order):
        raise ValidationError("Недостаточно прав.")
    if order.status in (Order.Status.COMPLETED, Order.Status.CANCELLED):
        raise ValidationError("Заказ закрыт.")
    Organization.objects.select_for_update().get(pk=order.organization_id)
    version = (order.files.aggregate(last=Max("version"))["last"] or 0) + 1
    obj = attach_file(order, actor, upload, OrderFile.Purpose.RESULT, version)
    try:
        event(order, actor, f"Загружен результат, версия {version} (не опубликован)", is_internal=True)
    except Exception:
        obj.file.delete(save=False)
        raise
    return obj


@transaction.atomic
def review_result(file_id, actor):
    obj = OrderFile.objects.select_related("order").get(pk=file_id, purpose="result")
    order = Order.objects.select_for_update().get(pk=obj.order_id)
    obj.refresh_from_db()
    if not can_manage(actor, order) or order.status in (Order.Status.COMPLETED, Order.Status.CANCELLED):
        raise ValidationError("Проверка результата недоступна.")
    if not obj.reviewed_at:
        obj.reviewed_at, obj.reviewed_by = timezone.now(), actor
        obj.save(update_fields=["reviewed_at", "reviewed_by"])
        event(order, actor, f"Проверен результат, версия {obj.version}", is_internal=True)
    return obj


@transaction.atomic
def publish_result(file_id, actor):
    obj = OrderFile.objects.get(pk=file_id, purpose="result")
    order = Order.objects.select_for_update().get(pk=obj.order_id)
    obj.refresh_from_db()
    if not can_manage(actor, order):
        raise ValidationError("Недостаточно прав.")
    if obj.published_at:
        return obj
    if order.status in (Order.Status.COMPLETED, Order.Status.CANCELLED) or not obj.reviewed_at:
        raise ValidationError("Сначала ответственный специалист должен подтвердить проверку результата.")
    if order.status not in (Order.Status.IN_PROGRESS, Order.Status.QUALITY, Order.Status.EXTRA, Order.Status.REVISION) or order.price_pending:
        raise ValidationError("Выдача доступна только для принятого в работу заказа с согласованной стоимостью.")
    obj.published_at = timezone.now()
    obj.save(update_fields=["published_at"])
    event(order, actor, f"Опубликован результат, версия {obj.version}")
    return obj
