# Standard Library
import logging

# Django
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

# Alliance Auth
from allianceauth.eveonline.models import EveCharacter

# Assume SDE enrichment provides EveType model
try:
    # Alliance Auth (External Libs)
    from eveuniverse.models import EveType
except ImportError:
    EveType = None

logger = logging.getLogger(__name__)


def get_type_name(type_id):
    if EveType:
        try:
            return EveType.objects.get(id=type_id).name
        except EveType.DoesNotExist:
            return str(type_id)
    return str(type_id)


def get_character_name(character_id):
    try:
        return EveCharacter.objects.get(character_id=character_id).character_name
    except EveCharacter.DoesNotExist:
        return str(character_id)


class BlueprintManager(models.Manager):
    """Manager for Blueprint operations (local only)"""

    pass


class IndustryJobManager(models.Manager):
    """Manager for Industry Job operations (local only)"""

    pass


class Blueprint(models.Model):
    owner_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="blueprints"
    )
    character_id = models.BigIntegerField()
    item_id = models.BigIntegerField(unique=True)
    blueprint_id = models.BigIntegerField(blank=True, null=True)
    type_id = models.IntegerField()
    location_id = models.BigIntegerField()
    location_flag = models.CharField(max_length=50)
    quantity = models.IntegerField()
    time_efficiency = models.IntegerField(default=0)
    material_efficiency = models.IntegerField(default=0)
    runs = models.IntegerField(default=0)
    character_name = models.CharField(max_length=255, blank=True)
    type_name = models.CharField(max_length=255, blank=True)
    last_updated = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = BlueprintManager()

    class Meta:
        verbose_name = "Blueprint"
        verbose_name_plural = "Blueprints"
        indexes = [
            models.Index(
                fields=["character_id", "type_id"],
                name="indy_hub_bl_charact_bfe16f_idx",
            ),
            models.Index(
                fields=["owner_user", "last_updated"],
                name="indy_hub_bl_owner_u_47cf92_idx",
            ),
        ]
        permissions = [
            ("can_access_indy_hub", "Can access Indy Hub module"),
        ]
        default_permissions = ()  # Disable Django's add/change/delete/view permissions

    def __str__(self):
        return f"{self.type_name or self.type_id} @ {self.character_id}"

    @property
    def is_original(self):
        return self.quantity == -1

    @property
    def is_copy(self):
        return self.quantity == -2

    @property
    def is_stack(self):
        return self.quantity > 0

    @property
    def quantity_display(self):
        """Human-readable quantity display"""
        if self.quantity == -1:
            return "Original"
        elif self.quantity == -2:
            return "Copy"
        elif self.quantity > 0:
            return f"Stack of {self.quantity}"
        else:
            return "Unknown"

    @property
    def product_type_id(self):
        """
        Attempts to determine the product type ID from blueprint type ID.
        For most blueprints in EVE: product_id = blueprint_id - 1
        Returns the type_id to use for icon display.
        """
        try:
            # Most blueprints follow the pattern: blueprint_type_id = product_type_id + 1
            potential_product_id = self.type_id - 1

            # Basic validation - product IDs should be positive
            if potential_product_id > 0:
                return potential_product_id
            else:
                # If calculation gives invalid result, return blueprint type_id
                return self.type_id
        except (TypeError, ValueError):
            # Fallback to blueprint type_id if calculation fails
            return self.type_id

    @property
    def icon_type_id(self):
        """
        Returns the type ID to use for displaying the blueprint icon.
        Uses product type ID when possible, falls back to blueprint type ID.
        """
        return self.product_type_id

    @property
    def me_progress_percentage(self):
        """Returns ME progress as percentage (0-100) for progress bar"""
        return int(min(100, (self.material_efficiency / 10.0) * 100))

    @property
    def te_progress_percentage(self):
        """Returns TE progress as percentage (0-100) for progress bar"""
        return int(min(100, (self.time_efficiency / 20.0) * 100))


class IndustryJob(models.Model):
    owner_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="industry_jobs"
    )
    character_id = models.BigIntegerField()
    job_id = models.IntegerField(unique=True)
    installer_id = models.IntegerField()
    facility_id = models.BigIntegerField()
    station_id = models.BigIntegerField(blank=True, null=True)
    activity_id = models.IntegerField()
    blueprint_id = models.BigIntegerField()
    blueprint_type_id = models.IntegerField()
    blueprint_location_id = models.BigIntegerField()
    output_location_id = models.BigIntegerField()
    runs = models.IntegerField()
    cost = models.DecimalField(max_digits=20, decimal_places=2, blank=True, null=True)
    licensed_runs = models.IntegerField(blank=True, null=True)
    probability = models.FloatField(blank=True, null=True)
    product_type_id = models.IntegerField(blank=True, null=True)
    status = models.CharField(max_length=20)
    duration = models.IntegerField()
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    pause_date = models.DateTimeField(blank=True, null=True)
    completed_date = models.DateTimeField(blank=True, null=True)
    completed_character_id = models.IntegerField(blank=True, null=True)
    successful_runs = models.IntegerField(blank=True, null=True)
    job_completed_notified = models.BooleanField(default=False)
    # Cached names for admin display
    activity_name = models.CharField(max_length=100, blank=True)
    blueprint_type_name = models.CharField(max_length=255, blank=True)
    product_type_name = models.CharField(max_length=255, blank=True)
    character_name = models.CharField(max_length=255, blank=True)
    last_updated = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = IndustryJobManager()

    class Meta:
        verbose_name = "Industry Job"
        verbose_name_plural = "Industry Jobs"
        indexes = [
            models.Index(
                fields=["character_id", "status"], name="indy_hub_in_charact_9ec4da_idx"
            ),
            models.Index(
                fields=["owner_user", "start_date"],
                name="indy_hub_in_owner_u_b59db7_idx",
            ),
            models.Index(
                fields=["activity_id", "status"], name="indy_hub_in_activit_8408d4_idx"
            ),
        ]
        default_permissions = ()

    def __str__(self):
        return f"Job {self.job_id} ({self.status})"

    @property
    def is_active(self):
        # Active only if status is active and end_date is in the future
        return (
            self.status == "active" and self.end_date and self.end_date > timezone.now()
        )

    @property
    def is_completed(self):
        # Completed when status flags delivered/ready or if end_date has passed
        if self.status in ["delivered", "ready"]:
            return True
        # treat overdue active jobs as completed
        return self.end_date and self.end_date <= timezone.now()

    @property
    def display_end_date(self):
        # Only mark as Completed if status indicates completion
        if self.is_completed:
            return "Completed"
        # Otherwise show the scheduled end date
        return self.end_date.strftime("%Y-%m-%d %H:%M") if self.end_date else ""

    @property
    def icon_url(self):
        """
        Returns the appropriate icon URL based on the job activity.
        """
        size = 32  # Default icon size for jobs

        if self.activity_id == 1:  # Manufacturing
            # For manufacturing, show the product icon (blueprint_type_id - 1)
            product_type_id = max(1, self.blueprint_type_id - 1)
            return (
                f"https://images.evetech.net/types/{product_type_id}/icon?size={size}"
            )
        elif (
            self.activity_id == 3 or self.activity_id == 4
        ):  # TE Research or ME Research
            # For research, show the blueprint original icon
            return f"https://images.evetech.net/types/{self.blueprint_type_id}/bp?size={size}"
        elif self.activity_id == 5:  # Copying
            # For copying, show the blueprint copy icon
            return f"https://images.evetech.net/types/{self.blueprint_type_id}/bpc?size={size}"
        elif self.activity_id == 8:  # Invention
            # For invention, show the product icon (the T2 item being invented)
            product_type_id = max(1, self.blueprint_type_id - 1)
            return (
                f"https://images.evetech.net/types/{product_type_id}/icon?size={size}"
            )
        elif self.activity_id == 9:  # Reverse Engineering
            # For reverse engineering, show the product icon
            product_type_id = max(1, self.blueprint_type_id - 1)
            return (
                f"https://images.evetech.net/types/{product_type_id}/icon?size={size}"
            )
        else:
            # Fallback for unknown activities - show blueprint icon
            return f"https://images.evetech.net/types/{self.blueprint_type_id}/bp?size={size}"

    @property
    def progress_percent(self):
        """Compute job progress percentage based on start and end dates"""
        if self.start_date and self.end_date:
            total = (self.end_date - self.start_date).total_seconds()
            if total <= 0:
                return 100
            elapsed = (timezone.now() - self.start_date).total_seconds()
            percent = (elapsed / total) * 100
            return int(max(0, min(100, percent)))
        return 0

    @property
    def display_eta(self):
        """Return formatted remaining time or Completed for jobs"""
        if self.end_date and self.end_date > timezone.now():
            remaining = self.end_date - timezone.now()
            total_seconds = int(remaining.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours:d}:{minutes:02d}:{seconds:02d}"
        if self.end_date:
            return "Completed"
        return ""


class CharacterUpdateTracker(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    character_id = models.BigIntegerField()
    blueprints_last_update = models.DateTimeField(null=True, blank=True)
    jobs_last_update = models.DateTimeField(null=True, blank=True)
    last_refresh_request = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    jobs_notify_completed = models.BooleanField(default=True)

    class Meta:
        unique_together = ("user", "character_id")
        default_permissions = ()

    def __str__(self):
        return f"Tracker for {self.user.username}#{self.character_id}"


class BlueprintCopyShareSetting(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="bp_copy_share_setting"
    )
    allow_copy_requests = models.BooleanField(default=False)

    def __str__(self):
        return f"Copy requests allowed: {self.allow_copy_requests} for {self.user.username}"

    class Meta:
        default_permissions = ()


class BlueprintCopyRequest(models.Model):
    # Blueprint identity (anonymized, deduped by type_id, ME, TE)
    type_id = models.IntegerField()
    material_efficiency = models.IntegerField()
    time_efficiency = models.IntegerField()
    requested_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="bp_copy_requests"
    )
    runs_requested = models.IntegerField(default=1)
    copies_requested = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    fulfilled = models.BooleanField(default=False)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    delivered = models.BooleanField(default=False)
    delivered_at = models.DateTimeField(null=True, blank=True)
    # No direct link to owner(s) to preserve anonymity

    class Meta:
        unique_together = (
            "type_id",
            "material_efficiency",
            "time_efficiency",
            "requested_by",
            "fulfilled",
        )
        default_permissions = ()

    def __str__(self):
        return f"Copy request: {self.type_id} ME{self.material_efficiency} TE{self.time_efficiency} by {self.requested_by.username}"


class BlueprintCopyOffer(models.Model):
    request = models.ForeignKey(
        "BlueprintCopyRequest", on_delete=models.CASCADE, related_name="offers"
    )
    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="bp_copy_offers"
    )
    status = models.CharField(
        max_length=16,
        choices=[
            ("accepted", "Accepted"),
            ("conditional", "Conditional"),
            ("rejected", "Rejected"),
        ],
    )
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_by_buyer = models.BooleanField(default=False)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("request", "owner")
        default_permissions = ()

    def __str__(self):
        return f"Offer by {self.owner} for request {self.request_id} ({self.status})"
