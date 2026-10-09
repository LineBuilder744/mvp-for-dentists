import re

from django import forms
from .models import Order, OrderFile, Service
from .validators import validate_stl
from .widgets import DropFileInput


class OrderForm(forms.ModelForm):
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    working = forms.FileField(label="Рабочая челюсть · STL", validators=[validate_stl], widget=DropFileInput)
    antagonist = forms.FileField(label="Антагонист · STL", required=False, validators=[validate_stl], widget=DropFileInput)
    bite = forms.FileField(label="Прикус · STL", required=False, validators=[validate_stl], widget=DropFileInput)
    scanbody_file = forms.FileField(label="Scanbody · STL", required=False, validators=[validate_stl], widget=DropFileInput)

    class Meta:
        model = Order
        fields = ["service", "teeth", "units", "jaw", "material", "requirements", "implant_system", "scanbody", "abutment"]
        widgets = {"requirements": forms.Textarea(attrs={"rows": 4}), "units": forms.NumberInput(attrs={"min": 1, "max": 32})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = Service.objects.filter(is_active=True)
        for name in ("working", "antagonist", "bite", "scanbody_file"):
            self.fields[name].widget.attrs["accept"] = ".stl"

    def clean_teeth(self):
        value = self.cleaned_data["teeth"].strip()
        if value:
            tokens = re.split(r"[\s,;]+", value)
            valid = {str(q * 10 + t) for q in range(1, 5) for t in range(1, 9)}
            valid |= {str(q * 10 + t) for q in range(5, 9) for t in range(1, 6)}
            if any(t not in valid for t in tokens) or len(tokens) != len(set(tokens)):
                raise forms.ValidationError("Укажите уникальные номера зубов FDI через запятую, например 16, 17.")
            return ", ".join(tokens)
        return value

    def clean_units(self):
        value = self.cleaned_data["units"]
        if value > 32:
            raise forms.ValidationError("Не более 32 единиц в одном заказе.")
        return value

    def clean(self):
        data = super().clean()
        service = data.get("service")
        if not service:
            return data
        if service.kind == Service.Kind.MODEL:
            if not data.get("jaw"):
                self.add_error("jaw", "Выберите челюсть.")
        elif not data.get("teeth"):
            self.add_error("teeth", "Укажите номера зубов.")
        if service.kind == Service.Kind.IMPLANT:
            for name in ("implant_system", "scanbody", "abutment", "scanbody_file"):
                if not data.get(name):
                    self.add_error(name, "Обязательно для коронки на имплантате.")
        return data


class CommentForm(forms.Form):
    text = forms.CharField(label="Комментарий", max_length=5000, widget=forms.Textarea(attrs={"rows": 3}))


class AdditionalFileForm(forms.Form):
    purpose = forms.ChoiceField(label="Назначение", choices=[(p.value, p.label) for p in (
        OrderFile.Purpose.WORKING,
        OrderFile.Purpose.ANTAGONIST,
        OrderFile.Purpose.BITE,
        OrderFile.Purpose.SCANBODY,
    )])
    file = forms.FileField(label="STL-файл", validators=[validate_stl], widget=DropFileInput)
