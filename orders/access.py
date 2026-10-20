from django.http import Http404

from .models import Order, OrderAccess


def can_manage(user, order):
    return user.is_order_admin or (user.is_designer and order.designer_id == user.pk)


def is_customer(user, order):
    return not user.is_staff and user.company_id is not None and user.company_id == order.organization_id


def visible_orders(user):
    if user.is_order_admin:
        return Order.objects.all()
    if user.is_designer:
        return Order.objects.filter(designer=user)
    if not user.is_staff and user.company_id:
        return Order.objects.filter(organization_id=user.company_id)
    return Order.objects.none()


def audit(user, reference, action, allowed):
    OrderAccess.objects.create(actor=user, reference=str(reference), action=action, allowed=allowed)


def get_order(user, pk, action="view", lock=False):
    queryset = visible_orders(user).select_related("service", "organization", "designer", "client")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    order = queryset.filter(pk=pk).first()
    audit(user, pk, action, order is not None)
    if order is None:
        raise Http404
    return order
