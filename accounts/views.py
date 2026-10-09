import logging

from django.contrib import messages
from django.core.mail import send_mail
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_http_methods

from .forms import RegistrationForm, ResendVerificationForm
from .models import User
from .tokens import email_verification_token

logger = logging.getLogger(__name__)


def safe_next(request):
    value = request.POST.get("next", request.GET.get("next", ""))
    if url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return value
    return reverse("orders:list")


def send_verification(request, user, next_url):
    from urllib.parse import urlencode
    url = request.build_absolute_uri(reverse("verify_email", kwargs={
        "uidb64": urlsafe_base64_encode(force_bytes(user.pk)), "token": email_verification_token.make_token(user),
    })) + "?" + urlencode({"next": next_url})
    try:
        send_mail("Подтверждение email · CAD", f"Подтвердите email, открыв ссылку:\n{url}\nСсылка действует 3 дня.", None, [user.email])
    except Exception:
        logger.exception("Verification email could not be sent for user %s", user.pk)
        messages.warning(request, "Не удалось отправить письмо. Аккаунт сохранён; запросите письмо повторно позже.")


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("orders:list")
    form = RegistrationForm(request.POST or None)
    next_url = safe_next(request)
    if request.method == "POST" and form.is_valid():
        try:
            user = form.save()
        except IntegrityError:
            form.add_error("email", "Этот email уже зарегистрирован.")
        else:
            send_verification(request, user, next_url)
            return render(request, "accounts/verification_sent.html")
    return render(request, "accounts/register.html", {"form": form, "next": next_url})


@require_http_methods(["GET", "POST"])
def verify_email(request, uidb64, token):
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (User.DoesNotExist, ValueError, TypeError, OverflowError, UnicodeDecodeError):
        user = None
    valid = user is not None and email_verification_token.check_token(user, token)
    if valid and request.method == "POST":
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        messages.success(request, "Email подтверждён. Войдите в кабинет.")
        from urllib.parse import urlencode
        return redirect(reverse("login") + "?" + urlencode({"next": safe_next(request)}))
    return render(request, "accounts/verify_email.html", {"valid": valid, "next": safe_next(request)})


@require_http_methods(["GET", "POST"])
def resend_verification(request):
    form = ResendVerificationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.filter(email__iexact=form.cleaned_data["email"], email_verified=False, is_active=True).first()
        if user:
            send_verification(request, user, safe_next(request))
        return render(request, "accounts/verification_sent.html")
    return render(request, "accounts/form.html", {"form": form, "title": "Подтверждение email", "button": "Отправить письмо"})
