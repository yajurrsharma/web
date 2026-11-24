from django.urls import path

from . import views

app_name = "products"

urlpatterns = [
    path("shop/", views.shop, name="shop"),
    path("search/", views.search, name="search"),
    path("category/<slug:slug>/", views.category, name="category"),
    path("product/<slug:slug>/", views.detail, name="detail"),
    path("product/<slug:slug>/review/", views.write_review, name="review"),
]
