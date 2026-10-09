from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
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
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("email_verified", True)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("Superuser requires is_staff and is_superuser")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
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
    email_verified = models.BooleanField("Email подтверждён", default=False)
    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name"]

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"
        constraints = [models.UniqueConstraint(Lower("email"), name="user_email_case_insensitive")]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.first_name} ({self.email})"
