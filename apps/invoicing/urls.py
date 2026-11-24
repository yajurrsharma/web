from django.urls import path

from . import views

app_name = "invoicing"

# Numbers like LLD/2627/00001 are written with dashes in URLs: LLD-2627-00001
urlpatterns = [
    path("invoice/<str:number>.pdf", views.invoice_pdf, name="invoice_pdf"),
    path("credit-note/<str:number>.pdf", views.credit_note_pdf, name="credit_note_pdf"),
]
