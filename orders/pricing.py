from decimal import Decimal

from .models import Service, Tariff


def quote(organization, service, units):
    tariff = Tariff.objects.filter(organization=organization, service=service).first()
    rate = tariff.unit_price if tariff else service.unit_price
    free_units = min(units, organization.free_crown_units) if service.kind == Service.Kind.CROWN else 0
    paid_units = units - free_units
    total = Decimal("0.00") if not paid_units else (rate * paid_units if rate is not None else None)
    return {"unit_price": rate, "total": total, "free_units": free_units, "units": units}
