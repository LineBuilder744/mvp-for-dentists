from django.db import migrations


def backfill(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Organization = apps.get_model("accounts", "Organization")
    Order = apps.get_model("orders", "Order")
    for user in User.objects.all().iterator():
        if user.is_staff:
            User.objects.filter(pk=user.pk).update(role="admin")
        if not user.company_id:
            # Names are untrusted registration data, never a basis for sharing access.
            company = Organization.objects.create(name=user.organization or f"Кабинет {user.first_name}")
            User.objects.filter(pk=user.pk).update(company=company)
            user.company_id = company.pk
        Order.objects.filter(client_id=user.pk, organization__isnull=True).update(organization_id=user.company_id)
    for order in Order.objects.filter(public_id__isnull=True).iterator():
        Order.objects.filter(pk=order.pk).update(public_id=f"CASE-{order.pk:06d}")
    Service = apps.get_model("orders", "Service")
    Service.objects.filter(kind="model").update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [("orders", "0004_requestbudget_order_clarification_resolved_and_more"),
                    ("accounts", "0002_organization_user_role_user_company")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
