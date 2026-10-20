import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.validators import MinValueValidator
from django.db import models

private_storage = FileSystemStorage(location=settings.PRIVATE_STORAGE_ROOT)


def private_file_path(instance, filename):
    return f"orders/{instance.order_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class Service(models.Model):
    class Kind(models.TextChoices):
        CROWN = "crown", "Коронка"
        IMPLANT = "implant", "Коронка на импланте"
        INLAY = "inlay", "Вкладка / накладка"
        BRIDGE = "bridge", "Мост"
        MODEL = "model", "Модель челюсти для печати"

    name = models.CharField("Название", max_length=150)
    kind = models.CharField("Вид", max_length=20, choices=Kind, unique=True)
    is_active = models.BooleanField("Доступна для заказа", default=True)
    unit_price = models.DecimalField("Базовая цена за единицу, ₽", max_digits=12, decimal_places=2,
                                    null=True, blank=True, validators=[MinValueValidator(0)])

    class Meta:
        verbose_name = "Услуга"
        verbose_name_plural = "Услуги"
        constraints = [models.CheckConstraint(condition=models.Q(unit_price__isnull=True) | models.Q(unit_price__gte=0), name="service_price_nonnegative")]

    def __str__(self):
        return self.name


class Tariff(models.Model):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, verbose_name="Организация")
    service = models.ForeignKey(Service, on_delete=models.CASCADE, verbose_name="Услуга")
    unit_price = models.DecimalField("Цена за единицу, ₽", max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])

    class Meta:
        verbose_name = "Индивидуальный тариф"
        verbose_name_plural = "Индивидуальные тарифы"
        constraints = [models.UniqueConstraint(fields=["organization", "service"], name="unique_organization_tariff"),
                       models.CheckConstraint(condition=models.Q(unit_price__gte=0), name="tariff_nonnegative")]

    def __str__(self):
        return f"{self.organization} · {self.service}"


class Order(models.Model):
    class Material(models.TextChoices):
        ZIRCONIA = "zirconia", "Диоксид циркония"
        EMAX = "emax", "Дисиликат лития (E.max)"
        METAL = "metal", "Металл / под металлокерамику"
        OTHER = "other", "Другое"

    class Design(models.TextChoices):
        ANATOMY = "anatomy", "Полная анатомия"
        CUTBACK = "cutback", "Cutback"
        FRAME = "frame", "Каркас"

    class InlayType(models.TextChoices):
        INLAY = "inlay", "Inlay"
        ONLAY = "onlay", "Onlay"
        OVERLAY = "overlay", "Overlay"

    class ImplantType(models.TextChoices):
        TI_BASE = "ti_base", "Коронка на Ti-base"
        ABUTMENT = "ready_abutment", "Коронка на готовом абатменте"

    class Status(models.TextChoices):
        NEW = "new", "Новый"
        IN_PROGRESS = "in_progress", "В работе"
        EXTRA = "extra", "Дополнительная обработка"
        NEEDS_DATA = "needs_data", "Требуется уточнение"
        QUALITY = "quality", "На проверке качества"
        READY = "ready", "Готов"
        REVISION = "revision", "На доработке"
        COMPLETED = "completed", "Закрыт"
        CANCELLED = "cancelled", "Отменён"

    class Payment(models.TextChoices):
        AFTER_REVIEW = "after_review", "Оплата после проверки результата"
        NOT_REQUIRED = "not_required", "Не требуется"
        PENDING = "pending", "Ожидает оплаты"
        PAID = "paid", "Оплачен"

    class Jaw(models.TextChoices):
        UPPER = "upper", "Верхняя"
        LOWER = "lower", "Нижняя"
        BOTH = "both", "Обе челюсти"

    client = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders", verbose_name="Клиент")
    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="orders", verbose_name="Организация")
    designer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="assigned_orders", verbose_name="Дизайнер")
    public_id = models.CharField(max_length=24, unique=True, null=True, editable=False)
    service = models.ForeignKey(Service, on_delete=models.PROTECT, verbose_name="Услуга")
    submission_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    teeth = models.CharField("Номера зубов (FDI)", max_length=150, blank=True)
    support_teeth = models.JSONField("Опорные зубы", default=list, blank=True)
    pontic_teeth = models.JSONField("Промежуточные зубы", default=list, blank=True)
    design = models.CharField("Тип дизайна", max_length=20, choices=Design, blank=True)
    inlay_type = models.CharField("Тип вкладки", max_length=20, choices=InlayType, blank=True)
    implant_type = models.CharField("Тип имплантной конструкции", max_length=20, choices=ImplantType, blank=True)
    implant_platform = models.CharField("Платформа / соединение", max_length=150, blank=True)
    unknown_implant_fields = models.JSONField("Параметры, требующие уточнения", default=list, blank=True)
    scanbody_on_working = models.BooleanField("Scanbody на рабочем скане", default=False)
    clarification_resolved = models.BooleanField("Имплантные параметры уточнены", default=False)
    quoted_unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    estimated_price = models.DecimalField("Предварительная стоимость, ₽", max_digits=14, decimal_places=2, null=True, blank=True)
    free_units = models.PositiveSmallIntegerField("Бесплатных единиц в заказе", default=0)
    proposed_price = models.DecimalField("Предложенная стоимость, ₽", max_digits=12, decimal_places=2,
                                        null=True, blank=True, validators=[MinValueValidator(0)])
    price_reason = models.TextField("Обоснование новой стоимости", max_length=3000, blank=True)
    price_accepted_at = models.DateTimeField(null=True, blank=True)
    units = models.PositiveSmallIntegerField("Количество единиц", default=1, validators=[MinValueValidator(1)])
    jaw = models.CharField("Челюсть", max_length=10, choices=Jaw, blank=True)
    material = models.CharField("Материал", max_length=150, blank=True)
    requirements = models.TextField("Требования", blank=True, max_length=5000)
    implant_system = models.CharField("Система имплантата", max_length=150, blank=True)
    scanbody = models.CharField("Scanbody", max_length=150, blank=True)
    abutment = models.CharField("Основание / абатмент", max_length=150, blank=True)
    status = models.CharField("Статус", max_length=20, choices=Status, default=Status.NEW, db_index=True)
    payment_status = models.CharField("Оплата", max_length=20, choices=Payment, default=Payment.AFTER_REVIEW, db_index=True)
    deadline = models.DateTimeField("Ожидаемый срок", null=True, blank=True)
    accepted_at = models.DateTimeField("Принят в работу", null=True, blank=True)
    confirmed_at = models.DateTimeField("Результат подтверждён", null=True, blank=True)
    revision_requested_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        constraints = [models.CheckConstraint(condition=models.Q(units__gte=1), name="order_units_positive")]
        constraints += [models.CheckConstraint(condition=models.Q(estimated_price__isnull=True) | models.Q(estimated_price__gte=0), name="order_estimate_nonnegative"),
                        models.CheckConstraint(condition=models.Q(proposed_price__isnull=True) | models.Q(proposed_price__gte=0), name="order_proposal_nonnegative"),
                        models.CheckConstraint(condition=models.Q(free_units__lte=models.F("units")), name="order_free_units_within_total")]
        indexes = [models.Index(fields=["client", "status"])]

    @property
    def case_number(self):
        return self.public_id or (f"CAD-{self.pk:06d}" if self.pk else "Новый заказ")

    @property
    def effective_price(self):
        if self.proposed_price is not None and self.price_accepted_at:
            return self.proposed_price
        return self.estimated_price

    @property
    def material_label(self):
        return dict(self.Material.choices).get(self.material, self.material)

    @property
    def price_pending(self):
        return self.proposed_price is not None and not self.price_accepted_at

    @property
    def client_status(self):
        return "В работе" if self.status == self.Status.QUALITY else self.get_status_display()

    def save(self, *args, **kwargs):
        if not self.organization_id and self.client_id:
            self.organization_id = self.client.company_id
        super().save(*args, **kwargs)
        if not self.public_id:
            self.public_id = f"CAD-{self.pk:06d}"
            type(self).objects.filter(pk=self.pk).update(public_id=self.public_id)

    def __str__(self):
        return self.case_number


class OrderFile(models.Model):
    class Purpose(models.TextChoices):
        WORKING = "working", "Рабочая челюсть"
        ANTAGONIST = "antagonist", "Антагонист"
        BITE = "bite", "Прикус"
        SCANBODY = "scanbody", "Scanbody"
        EXPLANATION = "explanation", "Пояснение"
        ADDITIONAL = "additional", "Дополнительный файл"
        RESULT = "result", "Готовый STL"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="files", verbose_name="Заказ")
    file = models.FileField("Файл", storage=private_storage, upload_to=private_file_path, max_length=250)
    original_name = models.CharField("Имя файла", max_length=255)
    size = models.PositiveBigIntegerField("Размер")
    purpose = models.CharField("Назначение", max_length=20, choices=Purpose)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name="Автор")
    version = models.PositiveIntegerField("Версия результата", null=True, blank=True)
    published_at = models.DateTimeField("Опубликован", null=True, blank=True)
    reviewed_at = models.DateTimeField("Проверен специалистом", null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="reviewed_files", verbose_name="Проверил")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Файл заказа"
        verbose_name_plural = "Файлы заказа"
        constraints = [
            models.UniqueConstraint(fields=["order", "version"], name="unique_result_version"),
            models.CheckConstraint(condition=(models.Q(purpose="result", version__gte=1, version__isnull=False) |
                                              (~models.Q(purpose="result") & models.Q(version__isnull=True, published_at__isnull=True))),
                                   name="result_has_version"),
        ]

    def __str__(self):
        return f"{self.order} · {self.original_name}"


class Comment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="comments", verbose_name="Заказ")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name="Автор")
    text = models.TextField("Комментарий", max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Комментарий"
        verbose_name_plural = "Комментарии"


class OrderEvent(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    description = models.TextField("Событие")
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField("Дата", auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Событие заказа"
        verbose_name_plural = "История заказа"


class OrderAccess(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reference = models.CharField(max_length=80)
    allowed = models.BooleanField()
    action = models.CharField(max_length=30)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Доступ к заказу"
        verbose_name_plural = "Журнал доступа"


class RequestBudget(models.Model):
    key = models.CharField(max_length=64, unique=True)
    requests = models.PositiveIntegerField(default=0)
    bytes = models.PositiveBigIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
