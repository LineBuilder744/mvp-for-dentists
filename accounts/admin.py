from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from .models import User


class ClientCreationForm(AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = ("email", "first_name")


class ClientChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class ClientAdmin(UserAdmin):
    add_form = ClientCreationForm
    form = ClientChangeForm
    ordering = ("email",)
    list_display = ("email", "first_name", "client_type", "organization", "email_verified", "is_staff")
    search_fields = ("email", "first_name", "organization")
    list_filter = ("client_type", "email_verified", "is_staff", "is_active")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Клиент", {"fields": ("first_name", "last_name", "client_type", "organization", "email_verified")}),
        ("Права", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Даты", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "first_name", "client_type", "organization", "password1", "password2", "email_verified", "is_staff")}),)
