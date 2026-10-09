import re
from unittest.mock import patch

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import User


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AuthTests(TestCase):
    def register(self, **overrides):
        data = {"first_name": "Анна", "email": "Anna@EXAMPLE.com", "client_type": "doctor",
                "organization": "", "password1": "Strong-pass-814!", "password2": "Strong-pass-814!", "next": "/orders/new/"}
        data.update(overrides)
        return self.client.post(reverse("register"), data)

    def test_registration_confirmation_and_login(self):
        self.assertEqual(self.register().status_code, 200)
        user = User.objects.get(email="anna@example.com")
        self.assertFalse(user.email_verified)
        self.assertNotEqual(user.password, "Strong-pass-814!")
        self.assertFalse(self.client.login(email=user.email, password="Strong-pass-814!"))
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r"http://testserver(/\S+)", mail.outbox[0].body).group(1)
        self.client.get(link)
        user.refresh_from_db()
        self.assertFalse(user.email_verified)
        self.assertEqual(self.client.post(link, {"next": "/orders/new/"}).status_code, 302)
        self.assertTrue(self.client.login(email="ANNA@EXAMPLE.COM", password="Strong-pass-814!"))
        self.assertContains(self.client.get(link), "Ссылка недействительна")

    def test_case_insensitive_email_and_blank_name(self):
        self.register()
        self.assertContains(self.register(email="ANNA@example.com"), "уже зарегистрирован")
        self.assertContains(self.register(email="other@example.com", first_name="   "), "Укажите имя")
        self.assertEqual(User.objects.count(), 1)

    def test_email_failure_keeps_account(self):
        with self.assertLogs("accounts.views", level="ERROR"), patch("accounts.views.send_mail", side_effect=OSError("SMTP unavailable")):
            self.assertEqual(self.register().status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    def test_password_reset(self):
        user = User.objects.create_user("client@example.com", "Old-pass-814!", first_name="Анна", email_verified=True)
        response = self.client.post(reverse("password_reset"), {"email": user.email})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        url = reverse("password_reset_confirm", kwargs={"uidb64": urlsafe_base64_encode(force_bytes(user.pk)), "token": default_token_generator.make_token(user)})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.client.post(response.url, {"new_password1": "New-strong-923!", "new_password2": "New-strong-923!"})
        self.assertTrue(self.client.login(email=user.email, password="New-strong-923!"))
        self.assertFalse(self.client.login(email=user.email, password="Old-pass-814!"))

    def test_next_does_not_redirect_outside_site(self):
        response = self.client.get(reverse("register"), {"next": "https://evil.example/"})
        self.assertEqual(response.context["next"], "/orders/")

    def test_invalid_verification_link(self):
        response = self.client.get(reverse("verify_email", args=["bad", "bad"]))
        self.assertContains(response, "Ссылка недействительна")

    def test_csrf_and_post_logout(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse("register"), {}).status_code, 403)
        self.assertEqual(client.get(reverse("logout")).status_code, 405)
