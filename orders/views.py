import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import AdditionalFileForm, CommentForm, OrderForm
from .models import Comment, Order, OrderFile
from .services import attach_file, create_order, event, transition


def visible_orders(user):
    return Order.objects.all() if user.is_staff else Order.objects.filter(client=user)


@login_required
@require_GET
def order_list(request):
    qs = Order.objects.filter(client=request.user).select_related("service")
    status = request.GET.get("status", "")
    search = request.GET.get("q", "").strip()
    if status:
        qs = qs.filter(status=status)
    if search:
        number = search.upper().removeprefix("CASE-")
        qs = qs.filter(pk=int(number)) if number.isdecimal() and len(number) <= 18 else qs.none()
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    return render(request, "orders/list.html", {"page": page, "statuses": Order.Status.choices, "status": status, "q": search})


@login_required
@require_http_methods(["GET", "POST"])
def order_create(request):
    form = OrderForm(request.POST or None, request.FILES or None, initial={"submission_key": uuid.uuid4()})
    if request.method == "POST":
        # A retry uses the original order even if the service has since been disabled.
        try:
            key = uuid.UUID(request.POST.get("submission_key", ""))
        except (ValueError, TypeError):
            key = None
        existing = Order.objects.filter(client=request.user, submission_key=key).first() if key else None
        if existing:
            return redirect("orders:detail", pk=existing.pk)
        if form.is_valid():
            try:
                order = create_order(request.user, form)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Заказ {order.case_number} отправлен.")
                return redirect("orders:detail", pk=order.pk)
    return render(request, "orders/create.html", {"form": form})


@login_required
@require_GET
def order_detail(request, pk):
    order = get_object_or_404(visible_orders(request.user).select_related("service", "client"), pk=pk)
    files = order.files.all()
    if not request.user.is_staff:
        files = files.exclude(purpose=OrderFile.Purpose.RESULT, published_at__isnull=True)
    events = order.events.all()
    if not request.user.is_staff:
        events = events.filter(is_internal=False)
    return render(request, "orders/detail.html", {"order": order, "files": files, "events": events,
        "comments": order.comments.select_related("author"), "comment_form": CommentForm(), "file_form": AdditionalFileForm()})


@login_required
@require_POST
def add_comment(request, pk):
    order = get_object_or_404(visible_orders(request.user), pk=pk)
    form = CommentForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            Comment.objects.create(order=order, author=request.user, text=form.cleaned_data["text"])
            event(order, request.user, "Добавлен комментарий")
        messages.success(request, "Комментарий отправлен.")
    else:
        messages.error(request, "Введите комментарий до 5000 символов.")
    return redirect("orders:detail", pk=pk)


@login_required
@require_POST
def add_file(request, pk):
    form = AdditionalFileForm(request.POST, request.FILES)
    with transaction.atomic():
        order = get_object_or_404(visible_orders(request.user).select_for_update(), pk=pk)
        if order.status != Order.Status.NEEDS_DATA:
            messages.error(request, "Дополнительный файл можно добавить после запроса команды.")
        elif order.files.exclude(purpose=OrderFile.Purpose.RESULT).count() >= settings.MAX_ATTACHMENTS_PER_ORDER:
            messages.error(request, "Достигнут лимит: 10 исходных файлов на заказ.")
        elif form.is_valid():
            attach_file(order, request.user, form.cleaned_data["file"], form.cleaned_data["purpose"])
            event(order, request.user, "Добавлен запрошенный файл")
            messages.success(request, "Файл добавлен.")
        else:
            messages.error(request, "; ".join(str(error) for errors in form.errors.values() for error in errors))
    return redirect("orders:detail", pk=pk)


@login_required
@require_POST
def client_action(request, pk, action):
    order = get_object_or_404(Order, pk=pk, client=request.user)
    target = {"confirm": Order.Status.COMPLETED, "revision": Order.Status.REVISION}.get(action)
    if not target:
        raise Http404
    form = CommentForm(request.POST) if action == "revision" else None
    if form and not form.is_valid():
        messages.error(request, "Опишите необходимые изменения (до 5000 символов).")
    else:
        try:
            transition(order.pk, request.user, target, form.cleaned_data["text"] if form else "")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Корректировка запрошена." if form else "Результат подтверждён. Оплата отмечается командой после получения.")
    return redirect("orders:detail", pk=pk)


@login_required
@require_GET
def download_file(request, pk):
    obj = get_object_or_404(OrderFile.objects.filter(order__in=visible_orders(request.user)), pk=pk)
    if obj.purpose == OrderFile.Purpose.RESULT and not obj.published_at and not request.user.is_staff:
        raise Http404
    try:
        response = FileResponse(obj.file.open("rb"), as_attachment=True, filename=obj.original_name, content_type="application/octet-stream")
    except FileNotFoundError:
        raise Http404
    response["Cache-Control"] = "private, no-store"
    return response
