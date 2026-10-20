from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from .models import Organization, User


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
    search_fields = ("email", "first_name", "organization", "company__name")
    list_filter = ("role", "client_type", "email_verified", "is_staff", "is_active")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Клиент", {"fields": ("first_name", "last_name", "client_type", "organization", "company", "email_verified", "role")}),
        ("Права", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Даты", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "first_name", "client_type", "organization", "company", "role", "password1", "password2", "email_verified", "is_staff")}),)

    def has_module_permission(self, request):
        return request.user.is_order_admin

    def has_view_permission(self, request, obj=None):
        return request.user.is_order_admin

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "free_crown_units", "created_at")
    search_fields = ("name",)

    def has_module_permission(self, request):
        return request.user.is_order_admin

    def has_view_permission(self, request, obj=None):
        return request.user.is_order_admin

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission
