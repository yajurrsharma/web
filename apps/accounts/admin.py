from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import Address, EmailOTP, User


class AdminUserCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("email", "full_name", "mobile")


class AdminUserChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = AdminUserCreationForm
    form = AdminUserChangeForm
    model = User

    list_display = ("email", "full_name", "mobile", "is_verified", "marketing_opt_in", "is_active", "is_staff",
                    "date_joined")
    list_filter = ("is_verified", "marketing_opt_in", "is_active", "is_staff", "is_superuser")
    search_fields = ("email", "full_name", "mobile")
    ordering = ("-date_joined",)
    # Consent can only come from the customer, so staff see it but cannot tick it for them.
    readonly_fields = ("date_joined", "updated_at", "last_login", "marketing_opt_in", "marketing_opt_in_at")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "mobile", "is_verified")}),
        ("Offer emails (customer's own choice)", {"fields": ("marketing_opt_in", "marketing_opt_in_at")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined", "updated_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",),
                "fields": ("email", "full_name", "mobile", "password1", "password2")}),
    )



@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("full_name", "user", "city", "state_code", "pincode", "is_default")
    search_fields = ("full_name", "user__email", "pincode", "phone")
    list_filter = ("state_code",)


@admin.register(EmailOTP)
class EmailOTPAdmin(admin.ModelAdmin):
    """For support/debugging only: shows whether a code was issued and used — never the code."""

    list_display = ("email", "purpose", "created_at", "expires_at", "attempts", "used_at", "invalidated")
    list_filter = ("purpose", "invalidated")
    search_fields = ("email",)
    exclude = ("code_hash",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
