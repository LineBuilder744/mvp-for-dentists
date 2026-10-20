import hashlib
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.utils import timezone

from .models import RequestBudget


class RequestLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        protected = request.path.startswith(("/orders/", "/accounts/", "/admin/"))
        if request.method == "POST" and protected:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
            if length > settings.MAX_REQUEST_UPLOAD_BYTES:
                return JsonResponse({"error": "Суммарный размер загрузки превышает 2 ГБ."}, status=413)
            now = timezone.now()
            principal = f"user:{request.user.pk}" if request.user.is_authenticated else f"ip:{request.META.get('REMOTE_ADDR', '')}"
            is_quote = request.path == "/orders/quote/"
            scope = "quote" if is_quote else "write"
            key = hashlib.sha256(f"{scope}:{principal}:{int(now.timestamp()) // 60}".encode()).hexdigest()
            with transaction.atomic():
                RequestBudget.objects.filter(expires_at__lt=now).delete()
                budget, _ = RequestBudget.objects.get_or_create(key=key, defaults={"expires_at": now + timedelta(minutes=2)})
                limit = settings.QUOTE_REQUESTS_PER_MINUTE if is_quote else settings.WRITE_REQUESTS_PER_MINUTE
                changed = RequestBudget.objects.filter(pk=budget.pk, requests__lt=limit).update(requests=F("requests") + 1)
                if not changed:
                    response = JsonResponse({"error": "Слишком много запросов. Повторите через минуту."}, status=429)
                    response["Retry-After"] = "60"
                    return response
                if length:
                    hour_key = hashlib.sha256(f"upload:{principal}:{int(now.timestamp()) // 3600}".encode()).hexdigest()
                    hour, _ = RequestBudget.objects.get_or_create(key=hour_key, defaults={"expires_at": now + timedelta(hours=2)})
                    allowed = settings.UPLOAD_BYTES_PER_HOUR - length
                    changed = RequestBudget.objects.filter(pk=hour.pk, bytes__lte=allowed).update(bytes=F("bytes") + length) if allowed >= 0 else 0
                    if not changed:
                        return JsonResponse({"error": "Часовой лимит загрузок исчерпан. Повторите позже."}, status=429)
        return self.get_response(request)
