import copy

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from accounts.models import User
from .access import audit, visible_orders
from .models import Comment, Order, OrderAccess, OrderEvent, OrderFile, Service, Tariff
from .services import TRANSITIONS, check_transition, event, propose_price, publish_result, review_result, transition, upload_result
from .validators import validate_stl


class OrderAdminForm(forms.ModelForm):
    next_status = forms.ChoiceField(label="Перевести в статус", required=False)
    reason = forms.CharField(label="Причина / запрос клиенту", required=False, max_length=5000, widget=forms.Textarea(attrs={"rows": 3}))
    result_upload = forms.FileField(label="Загрузить новую версию STL (черновик)", required=False, validators=[validate_stl])
    publish_file = forms.ModelChoiceField(label="Опубликовать результат", queryset=OrderFile.objects.none(), required=False)
    quality_confirmed = forms.BooleanField(label="Я проверил выбранный STL и подтверждаю его выдачу", required=False)
    new_price = forms.DecimalField(label="Предложить новую общую стоимость, ₽", max_digits=12, decimal_places=2, min_value=0, required=False)

    class Meta:
        model = Order
        fields = ["deadline", "payment_status", "designer", "clarification_resolved", "implant_system", "implant_platform", "scanbody", "abutment"]

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields["next_status"].choices = [("", "Без изменения")] + [
            (value, label) for value, label in Order.Status.choices if value in TRANSITIONS.get(self.instance.status, set())
            and not (actor and actor.is_designer and value == Order.Status.COMPLETED)]
        self.fields["publish_file"].queryset = self.instance.files.filter(purpose="result", published_at__isnull=True) if self.instance.pk else OrderFile.objects.none()
        if "designer" in self.fields:
            self.fields["designer"].queryset = User.objects.filter(is_active=True, is_staff=True, role=User.Role.DESIGNER, email_verified=True)

    def clean(self):
        data = super().clean()
        target = data.get("next_status")
        candidate = copy.copy(self.instance)
        for name in ("designer", "deadline", "clarification_resolved", "implant_system", "implant_platform", "scanbody", "abutment"):
            if name in data:
                setattr(candidate, name, data[name])
        if target and self.actor:
            try:
                if target != Order.Status.READY:
                    check_transition(candidate, self.actor, target, data.get("reason", ""))
            except ValidationError as exc:
                self.add_error("next_status", exc)
        selected = data.get("publish_file")
        if selected and not (selected.reviewed_at or data.get("quality_confirmed")):
            self.add_error("quality_confirmed", "Подтвердите проверку выбранного результата перед публикацией.")
        if target == Order.Status.READY:
            results = self.instance.files.filter(purpose="result", published_at__isnull=False, reviewed_at__isnull=False)
            if self.instance.revision_requested_at:
                results = results.filter(published_at__gt=self.instance.revision_requested_at)
            if not selected and not results.exists():
                self.add_error("next_status", "Сначала проверьте и опубликуйте готовый STL новой версии.")
            if self.instance.price_pending:
                self.add_error("next_status", "Клиент ещё не подтвердил новую стоимость.")
        if self.instance.status in (Order.Status.COMPLETED, Order.Status.CANCELLED) and (data.get("result_upload") or selected or data.get("new_price") is not None):
            raise ValidationError("Заказ закрыт.")
        if data.get("new_price") is not None:
            if not self.actor or not self.actor.is_order_admin:
                self.add_error("new_price", "Требуются права администратора.")
            if not data.get("reason", "").strip():
                self.add_error("reason", "Укажите обоснование новой цены.")
            if target:
                self.add_error("next_status", "Сначала отправьте новую стоимость на согласование клиенту.")
        payment = data.get("payment_status", self.instance.payment_status)
        if payment in (Order.Payment.PAID, Order.Payment.PENDING) and self.instance.status != Order.Status.COMPLETED and target != Order.Status.COMPLETED:
            self.add_error("payment_status", "Сначала подтвердите результат.")
        if self.instance.confirmed_at and payment == Order.Payment.AFTER_REVIEW:
            self.add_error("payment_status", "Результат уже подтверждён.")
        return data


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_order_admin or request.user.is_designer

    def has_change_permission(self, request, obj=None):
        return False


class FileInline(ReadOnlyInline):
    model = OrderFile
    fk_name = "order"
    fields = ("original_name", "purpose", "version", "reviewed_by", "reviewed_at", "published_at", "download")
    readonly_fields = fields

    @admin.display(description="Скачать")
    def download(self, obj):
        return format_html('<a href="{}">Скачать</a>', reverse("orders:download", args=[obj.pk]))


class EventInline(ReadOnlyInline):
    model = OrderEvent
    fields = ("created_at", "actor", "description")
    readonly_fields = fields


class CommentInline(ReadOnlyInline):
    model = Comment
    fields = ("created_at", "author", "text")
    readonly_fields = fields


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    form = OrderAdminForm
    list_display = ("case_number", "organization", "service", "units", "designer", "status", "deadline", "effective_price", "payment_status")
    list_filter = ("status", "payment_status", "service", "designer")
    search_fields = ("public_id", "client__email", "client__first_name", "organization__name")
    list_select_related = ("client", "service", "organization", "designer")
    readonly_fields = ("case_number", "client", "organization", "service", "teeth", "units", "material", "requirements",
                       "support_teeth", "pontic_teeth", "design", "inlay_type", "implant_type",
                       "unknown_implant_fields", "scanbody_on_working", "status", "accepted_at", "confirmed_at",
                       "created_at", "cabinet_link", "estimated_price", "free_units", "proposed_price", "price_reason", "price_accepted_at")
    fieldsets = (
        ("Заказ", {"fields": ("case_number", "organization", "client", "service", "teeth", "support_teeth", "pontic_teeth", "units",
                                "material", "design", "inlay_type", "implant_type", "requirements", "created_at", "cabinet_link")}),
        ("Имплант", {"fields": ("implant_system", "implant_platform", "scanbody", "abutment", "unknown_implant_fields", "scanbody_on_working", "clarification_resolved")}),
        ("Обработка", {"fields": ("designer", "status", "next_status", "reason", "deadline", "accepted_at", "confirmed_at")}),
        ("Стоимость", {"fields": ("estimated_price", "free_units", "proposed_price", "price_reason", "price_accepted_at", "new_price", "payment_status")}),
        ("Проверка и выдача", {"fields": ("result_upload", "publish_file", "quality_confirmed")}),
    )
    inlines = [FileInline, CommentInline, EventInline]

    def get_queryset(self, request):
        return super().get_queryset(request).filter(pk__in=visible_orders(request.user))

    def get_list_filter(self, request):
        return self.list_filter if request.user.is_order_admin else ("status", "service")

    def get_object(self, request, object_id, from_field=None):
        obj = super().get_object(request, object_id, from_field)
        audit(request.user, object_id, "admin", obj is not None)
        return obj

    def has_module_permission(self, request):
        return request.user.is_order_admin or request.user.is_designer

    def has_view_permission(self, request, obj=None):
        return request.user.is_order_admin or (request.user.is_designer and (obj is None or obj.designer_id == request.user.pk))

    has_change_permission = has_view_permission

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields + (("designer", "payment_status") if not request.user.is_order_admin else ())

    def get_fieldsets(self, request, obj=None):
        sections = []
        for title, config in self.fieldsets:
            fields = list(config["fields"])
            if title == "Имплант" and obj and obj.service.kind != "implant":
                continue
            if title == "Заказ" and obj and obj.service.kind == "surgical_guide":
                fields = [name for name in fields if name not in ("teeth", "support_teeth", "pontic_teeth", "material", "design", "inlay_type", "implant_type")]
            if not request.user.is_order_admin and "new_price" in fields:
                fields.remove("new_price")
            sections.append((title, {"fields": tuple(fields)}))
        return sections

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)

        class ActorForm(base):
            def __init__(self, *args, **form_kwargs):
                form_kwargs["actor"] = request.user
                super().__init__(*args, **form_kwargs)

        return ActorForm

    @admin.display(description="Карточка и комментарии")
    def cabinet_link(self, obj):
        return format_html('<a href="{}">Открыть карточку</a>', reverse("orders:detail", args=[obj.pk]))

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect(request.path)

    def save_model(self, request, obj, form, change):
        old = Order.objects.select_for_update().get(pk=obj.pk)
        for name in ("deadline", "payment_status", "designer", "clarification_resolved", "implant_system", "implant_platform", "scanbody", "abutment"):
            if name in form.changed_data:
                previous = getattr(old, name)
                setattr(old, name, getattr(obj, name))
                event(old, request.user, f"{old._meta.get_field(name).verbose_name}: {previous} → {getattr(old, name)}")
        old.save()
        uploaded = None
        try:
            if form.cleaned_data.get("result_upload"):
                uploaded = upload_result(old.pk, request.user, form.cleaned_data["result_upload"])
            if form.cleaned_data.get("publish_file"):
                selected = form.cleaned_data["publish_file"]
                if form.cleaned_data.get("quality_confirmed"):
                    review_result(selected.pk, request.user)
                publish_result(selected.pk, request.user)
            if form.cleaned_data.get("new_price") is not None:
                propose_price(old.pk, request.user, form.cleaned_data["new_price"], form.cleaned_data.get("reason", ""))
            elif form.cleaned_data.get("next_status"):
                transition(old.pk, request.user, form.cleaned_data["next_status"], form.cleaned_data.get("reason", ""))
            elif form.cleaned_data.get("reason"):
                Comment.objects.create(order=old, author=request.user, text=form.cleaned_data["reason"])
                event(old, request.user, "Добавлен комментарий команды")
        except Exception:
            if uploaded:
                uploaded.file.delete(save=False)
            raise
        obj.refresh_from_db()


class AdminOnly(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_order_admin

    def has_view_permission(self, request, obj=None):
        return request.user.is_order_admin

    has_change_permission = has_view_permission
    has_add_permission = has_view_permission
    has_delete_permission = has_view_permission


@admin.register(Service)
class ServiceAdmin(AdminOnly):
    list_display = ("name", "kind", "unit_price", "is_active")
    list_editable = ("unit_price", "is_active")


@admin.register(Tariff)
class TariffAdmin(AdminOnly):
    list_display = ("organization", "service", "unit_price")
    list_filter = ("service",)
    search_fields = ("organization__name",)


@admin.register(OrderAccess)
class AccessAdmin(AdminOnly):
    list_display = ("created_at", "actor", "reference", "action", "allowed")
    list_filter = ("allowed", "action")
    readonly_fields = ("created_at", "actor", "reference", "action", "allowed")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.site_header = "CAD · Команда"
admin.site.site_title = "CAD"
admin.site.index_title = "Клиенты и заказы"
