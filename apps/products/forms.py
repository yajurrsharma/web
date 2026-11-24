from django import forms

from .models import Review


class ReviewForm(forms.ModelForm):
    rating = forms.TypedChoiceField(
        coerce=int, choices=[(5, "★★★★★  Excellent"), (4, "★★★★☆  Good"), (3, "★★★☆☆  Average"),
                             (2, "★★☆☆☆  Poor"), (1, "★☆☆☆☆  Very poor")],
        widget=forms.RadioSelect, label="Your rating")

    class Meta:
        model = Review
        fields = ["rating", "title", "body", "photo"]
        labels = {"title": "Headline (optional)", "body": "Your review (optional)", "photo": "Add a photo (optional)"}
        widgets = {"title": forms.TextInput(attrs={"class": "form-control", "maxlength": 100,
                                                   "placeholder": "e.g. Lovely sandal fragrance"}),
                   "body": forms.Textarea(attrs={"class": "form-control", "rows": 4, "maxlength": 2000,
                                                 "placeholder": "How was the fragrance, burning time, packaging?"}),
                   "photo": forms.FileInput(attrs={"class": "form-control",
                                                   "accept": "image/jpeg,image/png,image/webp"})}
