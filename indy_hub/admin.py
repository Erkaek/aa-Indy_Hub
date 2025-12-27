"""
Django admin configuration for indy_hub models
"""

# Django
from django.contrib import admin

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
    UserOnboardingProgress,
)


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
        "seller",
        "item_count",
        "total_price_display",
        "status",
        "created_at",
        "approved_by",
    ]
    list_filter = ["status", "created_at", "seller"]
    search_fields = ["seller__username", "id"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (
            "Order Information",
            {"fields": ("seller", "status")},
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
        return f"{obj.total_price:,.2f} ISK"


@admin.register(MaterialExchangeBuyOrder)
class MaterialExchangeBuyOrderAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "buyer",
        "item_count",
        "total_price_display",
        "status",
        "created_at",
        "approved_by",
    ]
    list_filter = ["status", "created_at", "buyer"]
    search_fields = ["buyer__username", "id"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (
            "Order Information",
            {"fields": ("buyer", "status")},
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
        return f"{obj.total_price:,.2f} ISK"


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
