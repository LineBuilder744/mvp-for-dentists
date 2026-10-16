import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def create_backup(output):
    if connection.vendor != "sqlite" or connection.in_atomic_block:
        raise CommandError("Эта команда предназначена для SQLite вне транзакции. Для PostgreSQL используйте серверный pg_dump и файловый snapshot по инструкции.")
    output = Path(output)
    if output.exists():
        raise CommandError("Файл резервной копии уже существует.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        snapshot = root / "database.sqlite3"
        connection.ensure_connection()
        with sqlite3.connect(snapshot) as target:
            connection.connection.backup(target)
            paths = [row[0] for row in target.execute("SELECT file FROM orders_orderfile")]
        manifest = {"schema": 1, "created_at": timezone.now().isoformat(), "files": {}}
        files = {"database.sqlite3": snapshot}
        private_root = Path(settings.PRIVATE_STORAGE_ROOT).resolve()
        for value in paths:
            source = (private_root / value).resolve()
            if not source.is_relative_to(private_root) or not source.is_file():
                raise CommandError(f"Отсутствует или небезопасен файл: {value}")
            files["private_uploads/" + value] = source
        for name, path in files.items():
            manifest["files"][name] = {"size": path.stat().st_size, "sha256": digest(path)}
        descriptor, temporary = tempfile.mkstemp(dir=output.parent, suffix=".partial")
        os.close(descriptor)
        try:
            with tarfile.open(temporary, "w:gz") as archive:
                for name, path in files.items():
                    archive.add(path, arcname=name, recursive=False)
                manifest_path = root / "manifest.json"
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                archive.add(manifest_path, arcname="manifest.json")
            os.link(temporary, output)
        finally:
            os.unlink(temporary)
    return output


def restore_backup(archive_path, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise CommandError("Каталог восстановления должен быть новым. Действующая база не перезаписывается.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        root = Path(directory)
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                member = archive.getmember("manifest.json")
                if not member.isfile() or member.size > 10 * 1024 * 1024:
                    raise CommandError("Некорректный манифест.")
                manifest = json.load(archive.extractfile(member))
                members = archive.getmembers()
                if manifest.get("schema") != 1 or len(members) != len(manifest["files"]) + 1 or {m.name for m in members} != {*manifest["files"], "manifest.json"}:
                    raise CommandError("Состав архива не соответствует манифесту.")
                for name, metadata in manifest["files"].items():
                    target = (root / name).resolve()
                    member = archive.getmember(name)
                    if not target.is_relative_to(root) or not member.isfile() or member.size != metadata["size"]:
                        raise CommandError("Небезопасная запись в архиве.")
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with archive.extractfile(member) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(0o600)
                    if digest(target) != metadata["sha256"]:
                        raise CommandError(f"Контрольная сумма не совпадает: {name}")
            with sqlite3.connect(root / "database.sqlite3") as database:
                if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or database.execute("PRAGMA foreign_key_check").fetchall():
                    raise CommandError("Нарушена целостность базы.")
                for (name,) in database.execute("SELECT file FROM orders_orderfile"):
                    if "private_uploads/" + name not in manifest["files"]:
                        raise CommandError("В архиве отсутствует файл из базы.")
            root.chmod(0o700)
            root.rename(destination)
        except (KeyError, ValueError, OSError, tarfile.TarError, sqlite3.Error) as exc:
            raise CommandError(f"Восстановление не выполнено: {exc}") from exc
    return destination
