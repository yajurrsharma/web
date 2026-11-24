from django import forms

from .models import ContactMessage


class ContactForm(forms.ModelForm):
    # Honeypot: real people never fill it.
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={
        "tabindex": "-1", "autocomplete": "off", "class": "hp-field", "aria-hidden": "true"}))

    class Meta:
        model = ContactMessage
        fields = ["name", "email", "phone", "subject", "message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != "website":
                field.widget.attrs.setdefault("class", "form-control")
