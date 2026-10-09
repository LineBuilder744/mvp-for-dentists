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
        CROWN = "crown", "Полноанатомическая коронка"
        IMPLANT = "implant", "Коронка на имплантате"
        INLAY = "inlay", "Вкладка / накладка"
        BRIDGE = "bridge", "Мост"
        MODEL = "model", "Модель челюсти для печати"

    name = models.CharField("Название", max_length=150)
    kind = models.CharField("Вид", max_length=20, choices=Kind, unique=True)
    is_active = models.BooleanField("Доступна для заказа", default=True)

    class Meta:
        verbose_name = "Услуга"
        verbose_name_plural = "Услуги"

    def __str__(self):
        return self.name


class Order(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новый"
        IN_PROGRESS = "in_progress", "В работе"
        EXTRA = "extra", "Дополнительная обработка"
        NEEDS_DATA = "needs_data", "Требуются данные"
        READY = "ready", "Готов"
        REVISION = "revision", "Корректировка"
        COMPLETED = "completed", "Завершён"
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
    service = models.ForeignKey(Service, on_delete=models.PROTECT, verbose_name="Услуга")
    submission_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    teeth = models.CharField("Номера зубов (FDI)", max_length=150, blank=True)
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
        indexes = [models.Index(fields=["client", "status"])]

    @property
    def case_number(self):
        return f"CASE-{self.pk:06d}" if self.pk else "Новый заказ"

    def __str__(self):
        return self.case_number


class OrderFile(models.Model):
    class Purpose(models.TextChoices):
        WORKING = "working", "Рабочая челюсть"
        ANTAGONIST = "antagonist", "Антагонист"
        BITE = "bite", "Прикус"
        SCANBODY = "scanbody", "Scanbody"
        EXPLANATION = "explanation", "Пояснение"
        RESULT = "result", "Готовый STL"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="files", verbose_name="Заказ")
    file = models.FileField("Файл", storage=private_storage, upload_to=private_file_path, max_length=250)
    original_name = models.CharField("Имя файла", max_length=255)
    size = models.PositiveBigIntegerField("Размер")
    purpose = models.CharField("Назначение", max_length=20, choices=Purpose)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name="Автор")
    version = models.PositiveIntegerField("Версия результата", null=True, blank=True)
    published_at = models.DateTimeField("Опубликован", null=True, blank=True)
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
