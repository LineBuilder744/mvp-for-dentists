from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from orders.models import Order, OrderFile, private_storage


class Command(BaseCommand):
    help = "Просмотреть или удалить закрытые заказы по согласованному сроку хранения."

    def add_arguments(self, parser):
        parser.add_argument("--older-than-days", type=int, required=True)
        parser.add_argument("--execute", action="store_true")

    def handle(self, *args, **options):
        days = options["older_than_days"]
        if days < 1:
            raise CommandError("Срок хранения должен быть положительным.")
        with transaction.atomic():
            orders = Order.objects.select_for_update().filter(status__in=["completed", "cancelled"], updated_at__lt=timezone.now() - timedelta(days=days))
            ids = list(orders.values_list("pk", flat=True))
            names = list(OrderFile.objects.filter(order_id__in=ids).values_list("file", flat=True))
            self.stdout.write(f"Закрытых заказов: {len(ids)}; файлов: {len(names)}. {'Удаление' if options['execute'] else 'Только просмотр' }.")
            if options["execute"]:
                Order.objects.filter(pk__in=ids).delete()
                transaction.on_commit(lambda: [private_storage.delete(name) for name in names])
