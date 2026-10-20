from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models, transaction
from django.db.models.functions import Lower


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("role", "admin")
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("email_verified", True)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("Superuser requires is_staff and is_superuser")
        return self.create_user(email, password, **extra_fields)


class Organization(models.Model):
    name = models.CharField("Организация", max_length=200)
    free_crown_units = models.PositiveIntegerField("Осталось бесплатных коронок", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Организация"
        verbose_name_plural = "Организации"

    def __str__(self):
        return self.name


class User(AbstractUser):
    class Role(models.TextChoices):
        CLIENT = "client", "Клиент"
        DESIGNER = "designer", "Дизайнер"
        ADMIN = "admin", "Администратор"

    class ClientType(models.TextChoices):
        DOCTOR = "doctor", "Врач"
        CLINIC = "clinic", "Клиника"
        TECHNICIAN = "technician", "Техник"
        LAB = "lab", "Лаборатория"

    username = None
    email = models.EmailField("Email", unique=True)
    first_name = models.CharField("Имя", max_length=150)
    client_type = models.CharField("Тип клиента", max_length=20, choices=ClientType, default=ClientType.DOCTOR)
    organization = models.CharField("Организация", max_length=200, blank=True)
    company = models.ForeignKey(Organization, verbose_name="Организация / доступ", null=True, blank=True,
                                on_delete=models.PROTECT, related_name="members")
    role = models.CharField("Роль", max_length=20, choices=Role, default=Role.CLIENT)
    email_verified = models.BooleanField("Email подтверждён", default=False)
    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name"]

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"
        constraints = [models.UniqueConstraint(Lower("email"), name="user_email_case_insensitive")]

    @property
    def is_order_admin(self):
        return self.is_superuser or (self.is_staff and self.role == self.Role.ADMIN)

    @property
    def is_designer(self):
        return self.is_staff and self.role == self.Role.DESIGNER and not self.is_superuser

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        if self._state.adding and not self.company_id and not self.is_staff:
            self.company = Organization.objects.create(name=self.organization.strip() or f"Кабинет {self.first_name}")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.first_name} ({self.email})"
