from django import forms


class DropFileInput(forms.FileInput):
    template_name = "orders/widgets/drop_file.html"

    def __init__(self, attrs=None):
        super().__init__({"accept": ".stl", **(attrs or {})})
