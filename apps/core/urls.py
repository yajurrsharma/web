from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("contact/", views.contact, name="contact"),
    path("grievance/", views.grievance, name="grievance"),
    path("business-details/", views.business_details, name="business_details"),
    path("p/<slug:slug>/", views.page, name="page"),
    path("offline/", views.offline, name="offline"),
    path("healthz/", views.healthz, name="healthz"),
]
