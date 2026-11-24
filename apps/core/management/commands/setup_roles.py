"""
Create staff groups. Assign a staff user (is_staff=True) to one or more groups
in the admin; superusers have every permission.

    python manage.py setup_roles
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

ROLES = {
    "Store Manager": [
        "orders.manage_orders", "orders.view_reports", "orders.issue_refunds", "orders.view_order",
        "invoicing.view_invoice", "invoicing.add_invoice", "invoicing.view_creditnote",
        "inventory.view_stockmovement", "inventory.add_stockmovement",
        "products.view_product", "products.add_product", "products.change_product", "products.delete_product",
        "products.view_productvariant", "products.add_productvariant", "products.change_productvariant",
        "products.view_cost_price", "products.view_productmedia", "products.add_productmedia",
        "products.change_productmedia", "products.delete_productmedia",
        "products.view_category", "products.add_category", "products.change_category",
        "products.view_taxclass", "cart.view_coupon", "cart.add_coupon", "cart.change_coupon",
        "core.view_banner", "core.add_banner", "core.change_banner", "core.delete_banner",
        "core.view_page", "core.change_page", "core.view_contactmessage", "core.change_contactmessage",
        "core.view_sitesettings", "core.change_sitesettings", "accounts.view_user",
        "payments.view_payment", "payments.view_refund",
        "campaigns.view_campaign", "campaigns.add_campaign", "campaigns.change_campaign",
        "cart.view_promotion", "cart.add_promotion", "cart.change_promotion",
        "products.view_review", "products.change_review",
    ],
    "Order Staff": [
        "orders.manage_orders", "orders.view_order", "invoicing.view_invoice", "invoicing.add_invoice",
        "inventory.view_stockmovement", "products.view_product", "products.view_productvariant",
        "core.view_contactmessage", "core.change_contactmessage", "payments.view_payment",
    ],
    "Inventory Staff": [
        "inventory.view_stockmovement", "inventory.add_stockmovement", "products.view_product",
        "products.change_product", "products.view_productvariant", "products.change_productvariant",
        "products.view_productmedia", "products.add_productmedia", "products.change_productmedia",
        "products.view_category",
    ],
    "Accounts": [
        "orders.view_reports", "orders.issue_refunds", "orders.view_order", "orders.manage_orders",
        "invoicing.view_invoice", "invoicing.add_invoice", "invoicing.view_creditnote",
        "invoicing.view_invoicesequence", "payments.view_payment", "payments.view_refund",
        "payments.view_webhookevent", "products.view_taxclass", "products.change_taxclass", "products.add_taxclass",
        "products.view_cost_price", "core.view_auditlog",
    ],
}


class Command(BaseCommand):
    help = "Create or update the staff permission groups."

    def handle(self, *args, **opts):
        for role, perms in ROLES.items():
            group, _ = Group.objects.get_or_create(name=role)
            objs = []
            for code in perms:
                app_label, codename = code.split(".")
                perm = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
                if perm is None:
                    self.stderr.write(f"  missing permission {code}")
                    continue
                objs.append(perm)
            group.permissions.set(objs)
            self.stdout.write(self.style.SUCCESS(f"{role}: {len(objs)} permissions"))
