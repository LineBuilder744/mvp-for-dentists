from django.core.management.base import BaseCommand
from orders.backups import create_backup


class Command(BaseCommand):
    help = "Создать согласованную копию SQLite и файлов, упомянутых в снимке базы."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        self.stdout.write(str(create_backup(options["output"])))
