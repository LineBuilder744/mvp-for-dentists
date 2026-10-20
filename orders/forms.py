from django import forms

from .models import Order, OrderFile, Service
from .validators import validate_attachment, validate_stl
from .widgets import DropFileInput, MultipleDropFileInput

SUPPORTED_KINDS = (Service.Kind.CROWN, Service.Kind.BRIDGE, Service.Kind.INLAY, Service.Kind.IMPLANT)
TEETH = [(str(q * 10 + t), str(q * 10 + t)) for q in range(1, 5) for t in range(1, 9)]
IMPLANT_FIELDS = ("implant_system", "implant_platform", "scanbody", "abutment")


class TeethField(forms.MultipleChoiceField):
    def __init__(self, **kwargs):
        super().__init__(choices=TEETH, required=False, widget=forms.CheckboxSelectMultiple, **kwargs)

    def clean(self, value):
        value = super().clean(value)
        if len(value) != len(set(value)):
            raise forms.ValidationError("Каждый зуб можно выбрать только один раз.")
        return sorted(value, key=int)


def selected_positions(data, service, add_error):
    if service.kind == Service.Kind.BRIDGE:
        if "support_teeth" not in data or "pontic_teeth" not in data:
            return []
        supports, pontics = data.get("support_teeth", []), data.get("pontic_teeth", [])
        if len(supports) < 2:
            add_error("support_teeth", "Выберите минимум два опорных зуба.")
        if not pontics:
            add_error("pontic_teeth", "Выберите промежуточные зубы.")
        if set(supports) & set(pontics):
            add_error("pontic_teeth", "Один зуб не может быть опорным и промежуточным одновременно.")
        positions = set(supports) | set(pontics)
        # Conventional bridges must form one bounded span on one jaw.
        arches = [list(range(18, 10, -1)) + list(range(21, 29)),
                  list(range(48, 40, -1)) + list(range(31, 39))]
        valid_span = False
        for arch in arches:
            if positions and positions.issubset({str(t) for t in arch}):
                indexes = sorted(arch.index(int(t)) for t in positions)
                valid_span = indexes == list(range(indexes[0], indexes[-1] + 1)) and str(arch[indexes[0]]) in supports and str(arch[indexes[-1]]) in supports
        if positions and not valid_span:
            add_error("pontic_teeth", "Для стандартного моста выберите непрерывный участок одной челюсти с опорами по краям.")
        return sorted(positions, key=int)
    if "selected_teeth" not in data:
        return []
    positions = data["selected_teeth"]
    if not positions:
        add_error("selected_teeth", "Выберите хотя бы один зуб.")
    return positions


class QuoteForm(forms.Form):
    service = forms.ModelChoiceField(queryset=Service.objects.none())
    selected_teeth = TeethField(label="Номера зубов")
    support_teeth = TeethField(label="Опорные зубы")
    pontic_teeth = TeethField(label="Промежуточные зубы")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = Service.objects.filter(is_active=True, kind__in=SUPPORTED_KINDS)

    def clean(self):
        data = super().clean()
        if data.get("service"):
            data["positions"] = selected_positions(data, data["service"], self.add_error)
        return data


class OrderForm(forms.ModelForm):
    review_order = forms.BooleanField(label="Параметры заказа и выбранные файлы проверены")
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    selected_teeth = TeethField(label="Номера зубов")
    support_teeth = TeethField(label="Опорные зубы")
    pontic_teeth = TeethField(label="Промежуточные зубы")
    material = forms.ChoiceField(label="Материал", choices=[("", "Выберите материал"), *Order.Material.choices])
    design = forms.ChoiceField(label="Тип дизайна", choices=Order.Design.choices, required=False, initial=Order.Design.ANATOMY)
    inlay_type = forms.ChoiceField(label="Тип вкладки / накладки", choices=[("", "Выберите тип"), *Order.InlayType.choices], required=False)
    implant_type = forms.ChoiceField(label="Тип конструкции", choices=[("", "Выберите тип"), *Order.ImplantType.choices], required=False)
    implant_system_unknown = forms.BooleanField(label="Не знаю / уточню", required=False)
    implant_platform_unknown = forms.BooleanField(label="Не знаю / уточню", required=False)
    scanbody_unknown = forms.BooleanField(label="Не знаю / уточню", required=False)
    abutment_unknown = forms.BooleanField(label="Не знаю / уточню", required=False)
    working = forms.FileField(label="Рабочая челюсть", validators=[validate_stl], widget=DropFileInput)
    antagonist = forms.FileField(label="Антагонист", required=False, validators=[validate_stl], widget=DropFileInput)
    bite = forms.FileField(label="Прикус / регистрация", required=False, validators=[validate_stl], widget=DropFileInput)
    scanbody_file = forms.FileField(label="Скан со scanbody", required=False, validators=[validate_stl], widget=DropFileInput)

    class Meta:
        model = Order
        fields = ["service", "support_teeth", "pontic_teeth", "material", "design", "inlay_type", "implant_type",
                  "requirements", "implant_system", "implant_platform", "scanbody", "abutment", "scanbody_on_working"]
        labels = {"requirements": "Комментарий", "implant_system": "Производитель и система импланта",
                  "scanbody": "Производитель и модель scanbody", "abutment": "Производитель и модель Ti-base"}
        widgets = {"requirements": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = Service.objects.filter(is_active=True, kind__in=SUPPORTED_KINDS)
        self.fields["additional_files"] = MultipleFileField(label="Дополнительные файлы", required=False)

    def clean(self):
        data = super().clean()
        service = data.get("service")
        if not service:
            return data
        positions = selected_positions(data, service, self.add_error)
        self.instance.teeth = ", ".join(positions)
        self.instance.units = max(1, len(positions))
        if service.kind != Service.Kind.BRIDGE:
            data["support_teeth"] = data["pontic_teeth"] = []
        if service.kind == Service.Kind.INLAY and not data.get("inlay_type"):
            self.add_error("inlay_type", "Выберите Inlay, Onlay или Overlay.")
        if service.kind != Service.Kind.INLAY:
            data["inlay_type"] = ""
        data["design"] = (data.get("design") or Order.Design.ANATOMY) if service.kind == Service.Kind.CROWN else ""
        unknown = []
        if service.kind == Service.Kind.IMPLANT:
            if not data.get("implant_type"):
                self.add_error("implant_type", "Выберите тип имплантной конструкции.")
            if data.get("implant_type") == Order.ImplantType.TI_BASE:
                for name in IMPLANT_FIELDS:
                    if data.get(name + "_unknown"):
                        unknown.append(name)
                        data[name] = ""
                    elif not data.get(name):
                        self.add_error(name, "Заполните параметр или выберите «Не знаю / уточню».")
                if not data.get("scanbody_on_working") and not data.get("scanbody_file") and "scanbody" not in unknown:
                    self.add_error("scanbody_file", "Загрузите скан или отметьте, что scanbody присутствует на рабочем скане.")
            else:
                for name in IMPLANT_FIELDS:
                    data[name] = ""
                data["scanbody_file"] = None
                data["scanbody_on_working"] = False
        else:
            for name in IMPLANT_FIELDS:
                data[name] = ""
            data["implant_type"] = ""
            data["scanbody_file"] = None
            data["scanbody_on_working"] = False
        self.instance.unknown_implant_fields = unknown
        files_count = sum(bool(data.get(name)) for name in ("working", "antagonist", "bite", "scanbody_file")) + len(data.get("additional_files", []))
        if files_count > 10:
            self.add_error("additional_files", "Всего можно добавить не более 10 исходных файлов.")
        return data


class MultipleFileField(forms.FileField):
    widget = MultipleDropFileInput(attrs={"accept": ".stl,.jpg,.jpeg,.png"})

    def clean(self, data, initial=None):
        values = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        cleaned = []
        for value in values:
            value = super().clean(value, initial)
            validate_attachment(value)
            cleaned.append(value)
        return cleaned


class CommentForm(forms.Form):
    text = forms.CharField(label="Комментарий", max_length=5000, widget=forms.Textarea(attrs={"rows": 3}))


class RevisionForm(CommentForm):
    text = forms.CharField(label="Что нужно исправить", max_length=5000,
                          widget=forms.Textarea(attrs={"rows": 3, "id": "id_revision_text"}))
    file = forms.FileField(label="Файл к исправлению (необязательно)", required=False,
                           validators=[validate_attachment], widget=DropFileInput(attrs={"accept": ".stl,.jpg,.jpeg,.png"}))


class AdditionalFileForm(forms.Form):
    purpose = forms.ChoiceField(label="Назначение", choices=[(p.value, p.label) for p in (
        OrderFile.Purpose.WORKING, OrderFile.Purpose.ANTAGONIST, OrderFile.Purpose.BITE,
        OrderFile.Purpose.SCANBODY, OrderFile.Purpose.ADDITIONAL,
    )])
    file = forms.FileField(label="Файл", validators=[validate_attachment],
                           widget=DropFileInput(attrs={"accept": ".stl,.jpg,.jpeg,.png"}))
