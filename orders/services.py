from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Order, OrderEvent, OrderFile
from .validators import validate_stl


def event(order, actor, description, is_internal=False):
    return OrderEvent.objects.create(order=order, actor=actor, description=description, is_internal=is_internal)


def attach_file(order, actor, upload, purpose, version=None):
    validate_stl(upload)
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
            # Serializing a client's submissions prevents duplicate clicks from racing.
            get_user_model().objects.select_for_update().get(pk=client.pk)
            existing = Order.objects.filter(client=client, submission_key=form.cleaned_data["submission_key"]).first()
            if existing:
                return existing
            service = type(form.cleaned_data["service"]).objects.select_for_update().get(pk=form.cleaned_data["service"].pk)
            if not service.is_active:
                raise ValidationError("Эта услуга больше недоступна.")
            order = form.save(commit=False)
            order.client = client
            order.submission_key = form.cleaned_data["submission_key"]
            order.save()
            for field, purpose in [("working", "working"), ("antagonist", "antagonist"), ("bite", "bite"), ("scanbody_file", "scanbody")]:
                if form.cleaned_data.get(field):
                    saved_files.append(attach_file(order, client, form.cleaned_data[field], purpose))
            event(order, client, "Заказ создан")
            return order
    except Exception:
        for obj in saved_files:
            obj.file.delete(save=False)
        raise


TRANSITIONS = {
    Order.Status.NEW: {Order.Status.IN_PROGRESS, Order.Status.NEEDS_DATA, Order.Status.CANCELLED},
    Order.Status.IN_PROGRESS: {Order.Status.EXTRA, Order.Status.NEEDS_DATA, Order.Status.READY, Order.Status.CANCELLED},
    Order.Status.EXTRA: {Order.Status.IN_PROGRESS, Order.Status.NEEDS_DATA, Order.Status.READY, Order.Status.CANCELLED},
    Order.Status.NEEDS_DATA: {Order.Status.IN_PROGRESS, Order.Status.CANCELLED},
    Order.Status.READY: {Order.Status.REVISION, Order.Status.COMPLETED, Order.Status.CANCELLED},
    Order.Status.REVISION: {Order.Status.READY, Order.Status.NEEDS_DATA, Order.Status.EXTRA, Order.Status.CANCELLED},
}


@transaction.atomic
def transition(order_id, actor, target, reason=""):
    order = Order.objects.select_for_update().get(pk=order_id)
    if not actor.is_staff and (order.client_id != actor.pk or target not in (Order.Status.REVISION, Order.Status.COMPLETED)):
        raise ValidationError("Недостаточно прав.")
    if target not in TRANSITIONS.get(order.status, set()):
        raise ValidationError("Этот переход статуса недоступен.")
    if target in (Order.Status.EXTRA, Order.Status.NEEDS_DATA, Order.Status.REVISION, Order.Status.CANCELLED) and not reason.strip():
        raise ValidationError("Укажите причину или конкретный запрос.")
    if target == Order.Status.READY:
        results = order.files.filter(purpose=OrderFile.Purpose.RESULT, published_at__isnull=False)
        if order.revision_requested_at:
            results = results.filter(published_at__gt=order.revision_requested_at)
        if not results.exists():
            raise ValidationError("Сначала опубликуйте готовый STL новой версии.")
    if target == Order.Status.REVISION:
        order.revision_requested_at = timezone.now()
    if target == Order.Status.IN_PROGRESS and not order.accepted_at:
        order.accepted_at = timezone.now()
        if not order.deadline:
            order.deadline = order.accepted_at + timedelta(hours=24)
    if target == Order.Status.EXTRA:
        order.deadline = None
    if target == Order.Status.COMPLETED:
        order.confirmed_at = timezone.now()
        if order.payment_status == Order.Payment.AFTER_REVIEW:
            order.payment_status = Order.Payment.PENDING
    old_status = order.get_status_display()
    order.status = target
    order.save()
    event(order, actor, f"{old_status} → {order.get_status_display()}" + (f": {reason.strip()}" if reason else ""))
    if target == Order.Status.IN_PROGRESS:
        event(order, actor, f"Ожидаемый срок: {timezone.localtime(order.deadline):%d.%m.%Y %H:%M}" if order.deadline else "Срок уточняется")
    if target == Order.Status.EXTRA:
        event(order, actor, "Срок уточняется")
    return order


@transaction.atomic
def upload_result(order_id, actor, upload):
    if not actor.is_staff:
        raise ValidationError("Недостаточно прав.")
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status in (Order.Status.COMPLETED, Order.Status.CANCELLED):
        raise ValidationError("Заказ закрыт.")
    last_version = order.files.aggregate(last=Max("version"))["last"] or 0
    obj = attach_file(order, actor, upload, OrderFile.Purpose.RESULT, last_version + 1)
    event(order, actor, f"Загружен результат, версия {obj.version} (не опубликован)", is_internal=True)
    return obj


@transaction.atomic
def publish_result(file_id, actor):
    if not actor.is_staff:
        raise ValidationError("Недостаточно прав.")
    obj = OrderFile.objects.get(pk=file_id, purpose=OrderFile.Purpose.RESULT)
    order = Order.objects.select_for_update().get(pk=obj.order_id)
    obj.refresh_from_db()
    if obj.published_at:
        return obj
    if order.status in (Order.Status.COMPLETED, Order.Status.CANCELLED):
        raise ValidationError("Заказ закрыт.")
    obj.published_at = timezone.now()
    obj.save(update_fields=["published_at"])
    event(order, actor, f"Опубликован результат, версия {obj.version}")
    return obj
