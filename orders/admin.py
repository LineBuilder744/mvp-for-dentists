from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html

from .models import Comment, Order, OrderEvent, OrderFile, Service
from .services import TRANSITIONS, event, publish_result, transition, upload_result
from .validators import validate_stl


class OrderAdminForm(forms.ModelForm):
    next_status = forms.ChoiceField(label="Перевести в статус", required=False)
    reason = forms.CharField(label="Причина / запрос клиенту", required=False, max_length=5000, widget=forms.Textarea(attrs={"rows": 3}))
    result_upload = forms.FileField(label="Загрузить новую версию STL (черновик)", required=False, validators=[validate_stl])
    publish_file = forms.ModelChoiceField(label="Опубликовать загруженный результат", queryset=OrderFile.objects.none(), required=False)

    class Meta:
        model = Order
        fields = ["deadline", "payment_status"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["next_status"].choices = [("", "Без изменения")] + [
            (value, label) for value, label in Order.Status.choices if value in TRANSITIONS.get(self.instance.status, set())]
        self.fields["publish_file"].queryset = self.instance.files.filter(purpose="result", published_at__isnull=True) if self.instance.pk else OrderFile.objects.none()

    def clean(self):
        data = super().clean()
        target = data.get("next_status")
        if target in (Order.Status.EXTRA, Order.Status.NEEDS_DATA, Order.Status.REVISION, Order.Status.CANCELLED) and not data.get("reason", "").strip():
            self.add_error("reason", "Укажите причину или конкретный запрос клиенту.")
        if target == Order.Status.READY:
            results = self.instance.files.filter(purpose="result", published_at__isnull=False)
            if self.instance.revision_requested_at:
                results = results.filter(published_at__gt=self.instance.revision_requested_at)
            if not data.get("publish_file") and not results.exists():
                self.add_error("next_status", "Сначала загрузите и опубликуйте готовый STL новой версии.")
        if self.instance.status in (Order.Status.COMPLETED, Order.Status.CANCELLED) and (data.get("result_upload") or data.get("publish_file")):
            raise ValidationError("Заказ закрыт: загрузка и публикация недоступны.")
        payment = data.get("payment_status")
        if payment in (Order.Payment.PAID, Order.Payment.PENDING) and self.instance.status != Order.Status.COMPLETED and target != Order.Status.COMPLETED:
            self.add_error("payment_status", "Сначала подтвердите результат.")
        if self.instance.confirmed_at and payment == Order.Payment.AFTER_REVIEW:
            self.add_error("payment_status", "Результат уже подтверждён.")
        if target == Order.Status.EXTRA and data.get("deadline"):
            self.add_error("deadline", "При дополнительной обработке срок сбрасывается. Сохраните, затем укажите новый срок отдельным действием.")
        return data


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class FileInline(ReadOnlyInline):
    model = OrderFile
    fields = ("original_name", "purpose", "version", "published_at", "download")
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
    list_display = ("case_number", "client", "service", "units", "status", "deadline", "payment_status", "created_at")
    list_filter = ("status", "payment_status", "service")
    search_fields = ("client__email", "client__first_name", "client__organization")
    list_select_related = ("client", "service")
    readonly_fields = ("case_number", "client", "service", "teeth", "units", "jaw", "material", "requirements", "implant_system", "scanbody", "abutment", "status", "accepted_at", "confirmed_at", "created_at", "cabinet_link")
    fieldsets = (
        ("Заказ", {"fields": ("case_number", "client", "service", "teeth", "units", "jaw", "material", "requirements", "implant_system", "scanbody", "abutment", "created_at", "cabinet_link")}),
        ("Обработка", {"fields": ("status", "next_status", "reason", "deadline", "accepted_at", "confirmed_at", "payment_status")}),
        ("Результат", {"fields": ("result_upload", "publish_file")}),
    )
    inlines = [FileInline, CommentInline, EventInline]

    @admin.display(description="Карточка и комментарии")
    def cabinet_link(self, obj):
        return format_html('<a href="{}">Открыть карточку</a>', reverse("orders:detail", args=[obj.pk]))

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_search_results(self, request, queryset, search_term):
        results, duplicates = super().get_search_results(request, queryset, search_term)
        number = search_term.upper().removeprefix("CASE-")
        if number.isdecimal() and len(number) <= 18:
            results = results | queryset.filter(pk=int(number))
        return results, duplicates

    def save_model(self, request, obj, form, change):
        old = Order.objects.select_for_update().get(pk=obj.pk)
        # Keep concurrent status updates; this form edits only deadline and payment.
        old.deadline = obj.deadline
        old.payment_status = obj.payment_status
        previous = Order.objects.get(pk=obj.pk)
        old.save(update_fields=["deadline", "payment_status", "updated_at"])
        if previous.deadline != old.deadline:
            event(old, request.user, f"Срок изменён: {old.deadline.isoformat()}" if old.deadline else "Срок уточняется")
        if previous.payment_status != old.payment_status:
            event(old, request.user, f"Оплата: {old.get_payment_status_display()}")
        if form.cleaned_data.get("result_upload"):
            upload_result(old.pk, request.user, form.cleaned_data["result_upload"])
        if form.cleaned_data.get("publish_file"):
            publish_result(form.cleaned_data["publish_file"].pk, request.user)
        if form.cleaned_data.get("next_status"):
            transition(old.pk, request.user, form.cleaned_data["next_status"], form.cleaned_data.get("reason", ""))
        elif form.cleaned_data.get("reason"):
            Comment.objects.create(order=old, author=request.user, text=form.cleaned_data["reason"])
            event(old, request.user, "Добавлен комментарий команды")
        obj.refresh_from_db()


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "is_active")
    list_editable = ("is_active",)

admin.site.site_header = "CAD · Команда"
admin.site.site_title = "CAD"
admin.site.index_title = "Клиенты и заказы"
