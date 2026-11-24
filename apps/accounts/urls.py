from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("accounts/login/", views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/register/", views.register, name="register"),
    path("accounts/verify-email/", views.verify_email, name="verify_email"),
    path("accounts/verify-email/resend/", views.resend_verification, name="resend_verification"),
    path("accounts/login/code/", views.otp_login_request, name="otp_login"),
    path("accounts/login/code/verify/", views.otp_login_verify, name="otp_login_verify"),
    path("accounts/password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("accounts/password-reset/sent/", auth_views.PasswordResetDoneView.as_view(
        template_name="accounts/password_reset_done.html"), name="password_reset_done"),
    path("accounts/reset/<uidb64>/<token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("accounts/reset/done/", auth_views.PasswordResetCompleteView.as_view(
        template_name="accounts/password_reset_complete.html"), name="password_reset_complete"),
    path("accounts/password-change/", views.PasswordChangeView.as_view(), name="password_change"),

    # Customer account area

    path("account/", views.dashboard, name="dashboard"),
    path("account/profile/", views.profile, name="profile"),
    path("account/addresses/", views.address_list, name="addresses"),
    path("account/addresses/new/", views.address_create, name="address_create"),
    path("account/addresses/<int:pk>/edit/", views.address_edit, name="address_edit"),
    path("account/addresses/<int:pk>/delete/", views.address_delete, name="address_delete"),
]
