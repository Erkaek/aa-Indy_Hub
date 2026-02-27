"""Django admin configuration for indy_hub models."""

# Django
from django import forms
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin
from django.contrib.auth.models import Group, User

from .models import (
    Blueprint,
    CharacterSettings,
    CorporationSharingSetting,
    IndustryJob,
    MaterialExchangeBuyOrder,
    MaterialExchangeBuyOrderItem,
    MaterialExchangeConfig,
    MaterialExchangeSellOrder,
    MaterialExchangeSellOrderItem,
    MaterialExchangeStock,
    MaterialExchangeTransaction,
    NotificationWebhook,
    UserOnboardingProgress,
)


class IndyHubGroupAdminForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        permissions_field = self.fields.get("permissions")
        if permissions_field:
            permissions_field.label_from_instance = _indy_hub_permission_label


def _indy_hub_permission_label(permission):
    target_codenames = {
        "can_access_indy_hub",
        "can_manage_corp_bp_requests",
        "can_manage_material_hub",
    }
    if (
        permission.content_type.app_label == "indy_hub"
        and permission.codename in target_codenames
    ):
        return f"indy_hub | {permission.name}"
    return str(permission)


try:
    _registered_group_admin = admin.site._registry[Group].__class__
except KeyError:
    _registered_group_admin = GroupAdmin


class IndyHubGroupAdmin(_registered_group_admin):
    form = IndyHubGroupAdminForm


class IndyHubUserAdminForm(forms.ModelForm):
    class Meta:
        model = User
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user_permissions_field = self.fields.get("user_permissions")
        if user_permissions_field:
            user_permissions_field.label_from_instance = _indy_hub_permission_label


try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass

admin.site.register(Group, IndyHubGroupAdmin)


try:
    _registered_user_admin = admin.site._registry[User].__class__
except KeyError:
    _registered_user_admin = admin.ModelAdmin


class IndyHubUserAdmin(_registered_user_admin):
    form = IndyHubUserAdminForm


try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass

admin.site.register(User, IndyHubUserAdmin)


@admin.register(Blueprint)
class BlueprintAdmin(admin.ModelAdmin):
    list_display = [
        "type_name",
        "owner_user",
        "character_id",
        "quantity",
        "material_efficiency",
        "time_efficiency",
        "runs",
        "last_updated",
    ]
    list_filter = ["owner_user", "character_id", "quantity", "last_updated"]
    search_fields = ["type_name", "type_id", "owner_user__username"]
    readonly_fields = ["item_id", "last_updated", "created_at"]

    fieldsets = (
        (
            "Basic Information",
            {
                "fields": (
                    "owner_user",
                    "character_id",
                    "item_id",
                    "type_id",
                    "type_name",
                )
            },
        ),
        ("Location", {"fields": ("location_id", "location_name", "location_flag")}),
        (
            "Blueprint Details",
            {"fields": ("quantity", "material_efficiency", "time_efficiency", "runs")},
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "last_updated"), "classes": ("collapse",)},
        ),
    )


@admin.register(IndustryJob)
class IndustryJobAdmin(admin.ModelAdmin):
    list_display = [
        "job_id",
        "activity_name",
        "blueprint_type_name",
        "owner_user",
        "character_id",
        "status",
        "runs",
        "location_name",
        "start_date",
        "end_date",
    ]
    list_filter = ["status", "activity_id", "owner_user", "character_id", "start_date"]
    search_fields = [
        "blueprint_type_name",
        "product_type_name",
        "activity_name",
        "owner_user__username",
        "job_id",
    ]
    readonly_fields = ["job_id", "last_updated", "created_at", "start_date", "end_date"]
    fieldsets = (
        (
            "Job Information",
            {
                "fields": (
                    "owner_user",
                    "character_id",
                    "job_id",
                    "installer_id",
                    "status",
                )
            },
        ),
        (
            "Activity Details",
            {"fields": ("activity_id", "activity_name", "runs", "duration")},
        ),
        (
            "Blueprint Information",
            {"fields": ("blueprint_id", "blueprint_type_id", "blueprint_type_name")},
        ),
        ("Product Information", {"fields": ("product_type_id", "product_type_name")}),
        (
            "Locations",
            {
                "fields": (
                    "station_id",
                    "location_name",
                ),
                "classes": ("collapse",),
            },
        ),
        ("Financial", {"fields": ("cost", "licensed_runs"), "classes": ("collapse",)}),
        (
            "Invention/Research",
            {"fields": ("probability", "successful_runs"), "classes": ("collapse",)},
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "start_date",
                    "end_date",
                    "pause_date",
                    "completed_date",
                    "created_at",
                    "last_updated",
                ),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(CharacterSettings)
class CharacterSettingsAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "character_id",
        "jobs_notify_completed",
        "allow_copy_requests",
        "copy_sharing_scope",
        "updated_at",
    ]
    list_filter = [
        "jobs_notify_completed",
        "allow_copy_requests",
        "copy_sharing_scope",
        "updated_at",
    ]
    search_fields = ["user__username", "character_id"]
    readonly_fields = ["updated_at"]
    fieldsets = (
        (
            "Character Settings",
            {
                "fields": (
                    "user",
                    "character_id",
                    "jobs_notify_completed",
                    "allow_copy_requests",
                    "copy_sharing_scope",
                    "updated_at",
                )
            },
        ),
    )


@admin.register(UserOnboardingProgress)
class UserOnboardingProgressAdmin(admin.ModelAdmin):
    list_display = ["user", "dismissed", "updated_at"]
    search_fields = ["user__username"]
    list_filter = ["dismissed"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "user",
                    "dismissed",
                    "manual_steps",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )


@admin.register(CorporationSharingSetting)
class CorporationSharingSettingAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "corporation_id",
        "corporation_name",
        "share_scope",
        "allow_copy_requests",
        "has_manual_whitelist",
        "updated_at",
    ]
    list_filter = ["share_scope", "allow_copy_requests", "updated_at"]
    search_fields = ["user__username", "corporation_id", "corporation_name"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "user",
                    "corporation_id",
                    "corporation_name",
                    "share_scope",
                    "allow_copy_requests",
                    "authorized_characters",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(boolean=True, description="Whitelisted")
    def has_manual_whitelist(self, obj: CorporationSharingSetting) -> bool:
        return obj.restricts_characters


@admin.register(NotificationWebhook)
class NotificationWebhookAdmin(admin.ModelAdmin):
    class NotificationWebhookForm(forms.ModelForm):
        corporations = forms.MultipleChoiceField(
            required=False,
            choices=[],
            widget=forms.SelectMultiple(attrs={"size": "10"}),
            label="Corporations",
            help_text="Select one or more corporations for blueprint sharing webhooks.",
        )

        class Meta:
            model = NotificationWebhook
            fields = (
                "name",
                "webhook_type",
                "webhook_url",
                "ping_here",
                "is_active",
                "corporations",
            )

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            corp_choices = list(
                CorporationSharingSetting.objects.values_list(
                    "corporation_id", "corporation_name"
                )
                .distinct()
                .order_by("corporation_name")
            )
            self.fields["corporations"].choices = [
                (str(corp_id), corp_name or str(corp_id))
                for corp_id, corp_name in corp_choices
            ]
            if "ping_here" in self.fields:
                self.fields["ping_here"].help_text = (
                    "Send an @here mention with webhook notifications."
                )

            instance = getattr(self, "instance", None)
            if instance and instance.pk and instance.corporation_ids:
                self.fields["corporations"].initial = [
                    str(corp_id) for corp_id in instance.corporation_ids
                ]

            selected_type = self.data.get("webhook_type") or (
                instance.webhook_type if instance else None
            )
            if selected_type == NotificationWebhook.TYPE_MATERIAL_EXCHANGE:
                self.fields["corporations"].disabled = True

        def clean(self):
            cleaned = super().clean()
            webhook_type = cleaned.get("webhook_type")
            corp_values = cleaned.get("corporations") or []

            if webhook_type == NotificationWebhook.TYPE_BLUEPRINT_SHARING:
                if not corp_values:
                    self.add_error(
                        "corporations",
                        "At least one corporation is required for blueprint sharing webhooks.",
                    )
            elif webhook_type == NotificationWebhook.TYPE_MATERIAL_EXCHANGE:
                cleaned["corporations"] = []

            corp_map = {
                str(corp_id): (corp_id, corp_name)
                for corp_id, corp_name in CorporationSharingSetting.objects.values_list(
                    "corporation_id", "corporation_name"
                ).distinct()
            }

            corp_ids: list[int] = []
            corp_names: list[str] = []
            for corp_id_str in corp_values:
                corp_id, corp_name = corp_map.get(
                    str(corp_id_str),
                    (int(corp_id_str), str(corp_id_str)),
                )
                corp_ids.append(int(corp_id))
                corp_names.append(corp_name or str(corp_id))

            self.instance.corporation_ids = corp_ids
            self.instance.corporation_names = corp_names

            return cleaned

        def save(self, commit=True):
            instance = super().save(commit=False)
            corp_values = self.cleaned_data.get("corporations") or []

            corp_map = {
                str(corp_id): (corp_id, corp_name)
                for corp_id, corp_name in CorporationSharingSetting.objects.values_list(
                    "corporation_id", "corporation_name"
                ).distinct()
            }

            corp_ids: list[int] = []
            corp_names: list[str] = []
            for corp_id_str in corp_values:
                corp_id, corp_name = corp_map.get(
                    str(corp_id_str),
                    (int(corp_id_str), str(corp_id_str)),
                )
                corp_ids.append(int(corp_id))
                corp_names.append(corp_name or str(corp_id))

            instance.corporation_ids = corp_ids
            instance.corporation_names = corp_names

            if instance.webhook_type == NotificationWebhook.TYPE_MATERIAL_EXCHANGE:
                instance.corporation_ids = []
                instance.corporation_names = []

            if commit:
                instance.save()
            return instance

    form = NotificationWebhookForm
    list_display = [
        "name",
        "webhook_type",
        "corporation_list",
        "ping_here",
        "is_active",
        "updated_at",
    ]
    list_filter = ["webhook_type", "is_active"]
    search_fields = ["name", "webhook_url", "corporation_names"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "webhook_type",
                    "webhook_url",
                    "ping_here",
                    "is_active",
                    "corporations",
                )
            },
        ),
        (
            "Audit",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description="Corporations")
    def corporation_list(self, obj: NotificationWebhook) -> str:
        return ", ".join(obj.corporation_names or [])

    class Media:
        js = ("indy_hub/js/admin_notification_webhook.js",)


@admin.register(MaterialExchangeConfig)
class MaterialExchangeConfigAdmin(admin.ModelAdmin):
    list_display = [
        "corporation_id",
        "structure_name",
        "hangar_division",
        "sell_markup_percent",
        "sell_markup_base",
        "buy_markup_percent",
        "buy_markup_base",
        "is_active",
        "last_stock_sync",
        "last_price_sync",
    ]
    list_filter = ["is_active", "last_stock_sync"]
    readonly_fields = ["last_stock_sync", "last_price_sync", "created_at", "updated_at"]
    fieldsets = (
        (
            "Corporation Settings",
            {
                "fields": (
                    "corporation_id",
                    "structure_id",
                    "structure_name",
                    "hangar_division",
                    "is_active",
                )
            },
        ),
        (
            "Pricing Configuration",
            {
                "fields": (
                    ("sell_markup_percent", "sell_markup_base"),
                    ("buy_markup_percent", "buy_markup_base"),
                )
            },
        ),
        (
            "Sync Status",
            {
                "fields": (
                    "last_stock_sync",
                    "last_price_sync",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(MaterialExchangeStock)
class MaterialExchangeStockAdmin(admin.ModelAdmin):
    list_display = [
        "type_name",
        "quantity",
        "jita_buy_price",
        "jita_sell_price",
        "sell_price_to_member",
        "buy_price_from_member",
    ]
    list_filter = ["quantity"]
    search_fields = ["type_name", "type_id"]
    readonly_fields = ["sell_price_to_member", "buy_price_from_member"]


@admin.register(MaterialExchangeSellOrder)
class MaterialExchangeSellOrderAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "order_reference",
        "seller",
        "item_count",
        "total_price_display",
        "status",
        "created_at",
        "approved_by",
    ]
    list_filter = ["status", "created_at", "seller"]
    search_fields = ["seller__username", "id", "order_reference"]
    readonly_fields = ["created_at", "updated_at", "order_reference"]
    fieldsets = (
        (
            "Order Information",
            {"fields": ("order_reference", "seller", "status")},
        ),
        (
            "Status & Approval",
            {
                "fields": (
                    "approved_by",
                    "payment_verified_by",
                    "payment_journal_ref",
                )
            },
        ),
        (
            "Notes",
            {"fields": ("notes",)},
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    @admin.display(description="Items")
    def item_count(self, obj):
        return obj.items.count()

    @admin.display(description="Total Price")
    def total_price_display(self, obj):
        return f"{obj.total_price:,.0f} ISK"


@admin.register(MaterialExchangeBuyOrder)
class MaterialExchangeBuyOrderAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "order_reference",
        "buyer",
        "item_count",
        "total_price_display",
        "status",
        "created_at",
        "approved_by",
    ]
    list_filter = ["status", "created_at", "buyer"]
    search_fields = ["buyer__username", "id", "order_reference"]
    readonly_fields = ["created_at", "updated_at", "order_reference"]
    fieldsets = (
        (
            "Order Information",
            {"fields": ("order_reference", "buyer", "status")},
        ),
        (
            "Status & Fulfillment",
            {"fields": ("approved_by", "delivered_by", "delivery_method")},
        ),
        (
            "Notes",
            {"fields": ("notes",)},
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    @admin.display(description="Items")
    def item_count(self, obj):
        return obj.items.count()

    @admin.display(description="Total Price")
    def total_price_display(self, obj):
        return f"{obj.total_price:,.0f} ISK"


@admin.register(MaterialExchangeTransaction)
class MaterialExchangeTransactionAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "transaction_type",
        "user",
        "type_name",
        "quantity",
        "total_price",
        "completed_at",
    ]
    list_filter = ["transaction_type", "completed_at", "user"]
    search_fields = ["user__username", "type_name", "id"]
    readonly_fields = ["completed_at"]
    fieldsets = (
        (
            "Transaction Details",
            {
                "fields": (
                    "transaction_type",
                    "user",
                    "type_id",
                    "type_name",
                    "quantity",
                )
            },
        ),
        (
            "Financial Information",
            {"fields": ("unit_price", "total_price")},
        ),
        (
            "Related Orders",
            {"fields": ("sell_order", "buy_order")},
        ),
        ("Timestamp", {"fields": ("completed_at",)}),
    )


@admin.register(MaterialExchangeSellOrderItem)
class MaterialExchangeSellOrderItemAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "order_id",
        "type_name",
        "quantity",
        "unit_price",
        "total_price",
        "esi_contract_validated",
    ]
    list_filter = ["esi_contract_validated", "created_at"]
    search_fields = ["type_name", "order__id"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (
            "Item Information",
            {"fields": ("order", "type_id", "type_name", "quantity")},
        ),
        (
            "Pricing",
            {"fields": ("unit_price", "total_price")},
        ),
        (
            "ESI Validation",
            {
                "fields": (
                    "esi_contract_id",
                    "esi_contract_validated",
                    "esi_validation_checked_at",
                )
            },
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    @admin.display(description="Order")
    def order_id(self, obj):
        return f"Sell #{obj.order.id}"


@admin.register(MaterialExchangeBuyOrderItem)
class MaterialExchangeBuyOrderItemAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "order_id",
        "type_name",
        "quantity",
        "unit_price",
        "total_price",
        "esi_contract_validated",
    ]
    list_filter = ["esi_contract_validated", "created_at"]
    search_fields = ["type_name", "order__id"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (
            "Item Information",
            {"fields": ("order", "type_id", "type_name", "quantity")},
        ),
        (
            "Pricing",
            {"fields": ("unit_price", "total_price", "stock_available_at_creation")},
        ),
        (
            "ESI Validation",
            {
                "fields": (
                    "esi_contract_id",
                    "esi_contract_validated",
                    "esi_validation_checked_at",
                )
            },
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    @admin.display(description="Order")
    def order_id(self, obj):
        return f"Buy #{obj.order.id}"
