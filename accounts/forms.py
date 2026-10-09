from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import User


class RegistrationForm(UserCreationForm):
    first_name = forms.CharField(label="Имя", max_length=150, error_messages={"required": "Укажите имя."})

    class Meta:
        model = User
        fields = ["first_name", "email", "client_type", "organization"]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Этот email уже зарегистрирован.")
        return email

    def clean_first_name(self):
        name = self.cleaned_data["first_name"].strip()
        if not name:
            raise forms.ValidationError("Укажите имя.")
        return name


class ResendVerificationForm(forms.Form):
    email = forms.EmailField(label="Email")
