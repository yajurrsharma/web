from django import forms
from django.core.exceptions import ValidationError

from apps.core.gst import validate_gstin
from apps.core.validators import validate_image_upload

from .models import Order, OrderRequest, RequestPhoto


class CheckoutForm(forms.Form):
    shipping_address = forms.ModelChoiceField(queryset=None, widget=forms.RadioSelect, empty_label=None,
                                              error_messages={"required": "Choose a delivery address."})
    billing_same = forms.BooleanField(required=False, initial=True, label="Billing address is the same")
    billing_address = forms.ModelChoiceField(queryset=None, required=False, widget=forms.RadioSelect,
                                             empty_label=None)
    customer_gstin = forms.CharField(max_length=15, required=False, label="GSTIN (optional, for business purchases)",
                                     widget=forms.TextInput(attrs={"class": "form-control text-uppercase",
                                                                   "autocomplete": "off"}))
    delivery_instructions = forms.CharField(max_length=300, required=False, label="Delivery instructions (optional)",
                                            widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}))
    payment_method = forms.ChoiceField(choices=Order.METHOD_CHOICES, widget=forms.RadioSelect)
    checkout_token = forms.CharField(widget=forms.HiddenInput, max_length=64)

    def __init__(self, *args, user, payment_choices, **kwargs):
        super().__init__(*args, **kwargs)
        addresses = user.addresses.all()
        self.fields["shipping_address"].queryset = addresses
        self.fields["billing_address"].queryset = addresses
        self.fields["payment_method"].choices = payment_choices
        for name in ("shipping_address", "billing_address", "payment_method"):
            self.fields[name].widget.attrs["class"] = "form-check-input"
        self.fields["billing_same"].widget.attrs["class"] = "form-check-input"
        default = addresses.filter(is_default=True).first() or addresses.first()
        if default and not self.is_bound:
            self.initial["shipping_address"] = default.pk
        if payment_choices and not self.is_bound:
            self.initial["payment_method"] = payment_choices[0][0]

    def clean_customer_gstin(self):
        value = self.cleaned_data["customer_gstin"].strip().upper()
        if value:
            validate_gstin(value)
        return value

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("billing_same") and not cleaned.get("billing_address") and \
                cleaned.get("shipping_address"):
            raise ValidationError("Choose a billing address or tick 'same as delivery'.")
        return cleaned


class ReasonForm(forms.Form):
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}))


class PhotoInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class PhotosField(forms.FileField):
    """Several photos in one input; each is checked in ProblemForm.clean_photos."""

    widget = PhotoInput

    def clean(self, data, initial=None):
        return [f for f in (data if isinstance(data, (list, tuple)) else [data]) if f]


class ProblemForm(forms.Form):
    """Report a problem with a delivered order: what went wrong, details, and photos as proof."""

    problem = forms.ChoiceField(choices=OrderRequest.PROBLEM_CHOICES, widget=forms.RadioSelect,
                                error_messages={"required": "Choose what went wrong."})
    reason = forms.CharField(max_length=500, label="Tell us more",
                             widget=forms.Textarea(attrs={"rows": 3, "class": "form-control",
                                                          "placeholder": "Which item, and what is wrong with it?"}))
    photos = PhotosField(required=False, label="Photos",
                             widget=PhotoInput(attrs={"class": "form-control", "accept": "image/*"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["problem"].widget.attrs["class"] = "form-check-input"

    def clean_photos(self):
        files = self.cleaned_data["photos"]
        if len(files) > RequestPhoto.MAX_PER_REQUEST:
            raise ValidationError(f"Attach at most {RequestPhoto.MAX_PER_REQUEST} photos.")
        for f in files:
            validate_image_upload(f)
        return files

    def clean(self):
        cleaned = super().clean()
        problem = cleaned.get("problem")
        if problem and problem not in OrderRequest.PHOTO_OPTIONAL and "photos" in cleaned and not cleaned["photos"]:
            self.add_error("photos", "Please attach at least one clear photo of the item and the parcel.")
        return cleaned
