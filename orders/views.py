import uuid
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import Organization
from .access import audit, get_order, is_customer, visible_orders
from .forms import AdditionalFileForm, CommentForm, OrderForm, QuoteForm, RevisionForm
from .models import Comment, Order, OrderFile
from .pricing import quote
from .services import accept_price, attach_file, create_order, event, transition


@login_required
@require_GET
def order_list(request):
    qs = visible_orders(request.user).select_related("service", "organization")
    status = request.GET.get("status", "")
    search = request.GET.get("q", "").strip()
    if status:
        qs = qs.filter(status__in=(Order.Status.IN_PROGRESS, Order.Status.QUALITY)) if status == Order.Status.IN_PROGRESS and not request.user.is_staff else qs.filter(status=status)
    if search:
        number = search.upper().removeprefix("CASE-").removeprefix("CAD-")
        qs = qs.filter(pk=int(number)) if number.isdecimal() and len(number) <= 18 else qs.none()
    statuses = [(value, label) for value, label in Order.Status.choices if value != Order.Status.QUALITY or request.user.is_staff]
    return render(request, "orders/list.html", {"page": Paginator(qs, 20).get_page(request.GET.get("page")),
        "statuses": statuses, "status": status, "q": search})


def require_client(request):
    if request.user.is_staff or not request.user.company_id:
        raise PermissionDenied("Нужен клиентский аккаунт организации.")


@login_required
@require_POST
def order_quote(request):
    require_client(request)
    form = QuoteForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)
    organization = Organization.objects.get(pk=request.user.company_id)
    result = quote(organization, form.cleaned_data["service"], len(form.cleaned_data["positions"]))
    return JsonResponse({key: str(value) if isinstance(value, Decimal) else value for key, value in result.items()})


@login_required
@require_http_methods(["GET", "POST"])
def order_create(request):
    require_client(request)
    form = OrderForm(request.POST or None, request.FILES or None, initial={"submission_key": uuid.uuid4()})
    if request.method == "POST":
        try:
            key = uuid.UUID(request.POST.get("submission_key", ""))
        except (ValueError, TypeError):
            key = None
        existing = visible_orders(request.user).filter(submission_key=key).first() if key else None
        if existing:
            return redirect("orders:detail", pk=existing.pk)
        if form.is_valid():
            try:
                order = create_order(request.user, form)
            except ValidationError as exc:
                form.add_error(None, exc)
            except IntegrityError:
                form.add_error(None, "Ключ отправки уже использован. Откройте новую форму заказа.")
            else:
                messages.success(request, f"Заказ {order.case_number} отправлен.")
                return redirect("orders:detail", pk=order.pk)
    order_of_kinds = ["crown", "bridge", "inlay", "implant"]
    services = sorted(form.fields["service"].queryset, key=lambda service: order_of_kinds.index(service.kind))
    labels = {"crown": ("Коронка", "На собственном зубе"), "bridge": ("Мост", "Мостовидный протез"),
              "inlay": ("Вкладка / накладка", "Inlay, Onlay, Overlay"), "implant": ("Коронка на импланте", "На Ti-base или готовом абатменте")}
    for service in services:
        service.title, service.subtitle = labels[service.kind]
    return render(request, "orders/create.html", {"form": form, "services": services,
        "selected_service": str(form["service"].value() or ""), "free_crowns": request.user.company.free_crown_units,
        "implant_parameters": [{"field": form[name], "unknown": form[name + "_unknown"]}
                               for name in ("implant_system", "implant_platform", "scanbody", "abutment")]})


def render_detail(request, order, file_form=None, revision_form=None):
    files = order.files.all()
    events = order.events.all()
    if not request.user.is_staff:
        files = files.exclude(purpose="result", published_at__isnull=True)
        events = events.filter(is_internal=False)
    response = render(request, "orders/detail.html", {"order": order,
        "source_files": files.exclude(purpose="result").order_by("created_at"),
        "result_files": files.filter(purpose="result").order_by("-version"), "events": events,
        "customer": is_customer(request.user, order), "comments": order.comments.select_related("author"),
        "comment_form": CommentForm(), "file_form": file_form or AdditionalFileForm(),
        "revision_form": revision_form or RevisionForm()})
    if file_form is not None or revision_form is not None:
        response["X-Form-Errors"] = "1"
    return response


@login_required
@require_GET
def order_detail(request, pk):
    return render_detail(request, get_order(request.user, pk))


@login_required
@require_POST
def add_comment(request, pk):
    order = get_order(request.user, pk, "comment")
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
    get_order(request.user, pk, "upload")
    saved = None
    try:
        with transaction.atomic():
            order = get_order(request.user, pk, "upload", lock=True)
            Organization.objects.select_for_update().get(pk=order.organization_id)
            if order.status != Order.Status.NEEDS_DATA:
                form.add_error(None, "Дополнительный файл можно добавить после запроса команды.")
            elif form.is_valid():
                try:
                    saved = attach_file(order, request.user, form.cleaned_data["file"], form.cleaned_data["purpose"])
                except ValidationError as exc:
                    form.add_error("file", exc)
                else:
                    event(order, request.user, "Добавлен запрошенный файл")
                    messages.success(request, "Файл добавлен.")
                    return redirect("orders:detail", pk=pk)
    except Exception:
        if saved:
            saved.file.delete(save=False)
        raise
    return render_detail(request, order, file_form=form)


@login_required
@require_POST
def client_action(request, pk, action):
    order = get_order(request.user, pk, action)
    if not is_customer(request.user, order):
        raise Http404
    if action == "accept-price":
        try:
            expected = Decimal(request.POST.get("price", ""))
            if not expected.is_finite():
                raise InvalidOperation
            accept_price(pk, request.user, expected)
        except (InvalidOperation, ValidationError) as exc:
            messages.error(request, "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Некорректная цена.")
        else:
            messages.success(request, "Стоимость подтверждена. Команда может принять заказ в работу.")
        return redirect("orders:detail", pk=pk)
    target = {"confirm": Order.Status.COMPLETED, "revision": Order.Status.REVISION}.get(action)
    if not target:
        raise Http404
    form = RevisionForm(request.POST, request.FILES) if action == "revision" else None
    if form and not form.is_valid():
        return render_detail(request, order, revision_form=form)
    saved = None
    try:
        with transaction.atomic():
            if form and form.cleaned_data.get("file"):
                Organization.objects.select_for_update().get(pk=order.organization_id)
                saved = attach_file(order, request.user, form.cleaned_data["file"], OrderFile.Purpose.EXPLANATION)
            transition(pk, request.user, target, form.cleaned_data["text"] if form else "")
    except ValidationError as exc:
        if saved:
            saved.file.delete(save=False)
        if form:
            form.add_error(None, exc)
            return render_detail(request, order, revision_form=form)
        messages.error(request, "; ".join(exc.messages))
    except Exception:
        if saved:
            saved.file.delete(save=False)
        raise
    else:
        messages.success(request, "Исправление запрошено." if form else "Результат подтверждён. Оплата отмечается командой после получения.")
    return redirect("orders:detail", pk=pk)


@login_required
@require_GET
def download_file(request, pk):
    obj = OrderFile.objects.filter(order__in=visible_orders(request.user), pk=pk).first()
    allowed = obj is not None and (request.user.is_staff or obj.purpose != "result" or obj.published_at is not None)
    audit(request.user, f"file:{pk}", "download", allowed)
    if not allowed:
        raise Http404
    try:
        response = FileResponse(obj.file.open("rb"), as_attachment=True, filename=obj.original_name, content_type="application/octet-stream")
    except FileNotFoundError:
        raise Http404
    response["Cache-Control"] = "private, no-store"
    return response
