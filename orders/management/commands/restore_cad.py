from django.core.management.base import BaseCommand
from orders.backups import restore_backup


class Command(BaseCommand):
    help = "Проверить и восстановить SQLite и файлы в новый изолированный каталог."

    def add_arguments(self, parser):
        parser.add_argument("--archive", required=True)
        parser.add_argument("--target", required=True)

    def handle(self, *args, **options):
        self.stdout.write(str(restore_backup(options["archive"], options["target"])))
