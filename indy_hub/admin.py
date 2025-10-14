"""
Django admin configuration for indy_hub models
"""

# Django
from django.contrib import admin

from .models import (
    Blueprint,
    CharacterSettings,
    IndustryJob,
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
