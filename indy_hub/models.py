# Standard Library
from datetime import timedelta

# Django
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from .utils.eve import get_blueprint_product_type_id, is_reaction_blueprint


class BlueprintManager(models.Manager):
    """Manager for Blueprint operations (local only)"""

    pass


class IndustryJobManager(models.Manager):
    """Manager for Industry Job operations (local only)"""

    pass


class Blueprint(models.Model):
    class OwnerKind(models.TextChoices):
        CHARACTER = "character", _("Character-owned")
        CORPORATION = "corporation", _("Corporation-owned")

    class BPType(models.TextChoices):
        REACTION = "REACTION", "Reaction"
        ORIGINAL = "ORIGINAL", "Original"
        COPY = "COPY", "Copy"

    owner_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="blueprints"
    )
    character_id = models.BigIntegerField(blank=True, null=True)
    corporation_id = models.BigIntegerField(blank=True, null=True)
    corporation_name = models.CharField(max_length=255, blank=True)
    item_id = models.BigIntegerField(unique=True)
    blueprint_id = models.BigIntegerField(blank=True, null=True)
    type_id = models.IntegerField()
    location_id = models.BigIntegerField()
    location_name = models.CharField(max_length=255, blank=True)
    location_flag = models.CharField(max_length=50)
    quantity = models.IntegerField()
    owner_kind = models.CharField(
        max_length=16,
        choices=OwnerKind.choices,
        default=OwnerKind.CHARACTER,
    )
    bp_type = models.CharField(
        max_length=16,
        choices=BPType.choices,
        default=BPType.ORIGINAL,
    )
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
        db_table = "indy_hub_indyblueprint"
        indexes = [
            models.Index(
                fields=["character_id", "type_id"],
                name="indy_hub_bl_charact_bfe16f_idx",
            ),
            models.Index(
                fields=["owner_user", "last_updated"],
                name="indy_hub_bl_owner_u_47cf92_idx",
            ),
            models.Index(
                fields=["owner_kind", "corporation_id", "type_id"],
                name="indy_hub_bl_corp_scope_idx",
            ),
        ]
        permissions = [
            ("can_access_indy_hub", "Can access Indy Hub module"),
            (
                "can_manage_copy_requests",
                "Can request or share blueprint copies",
            ),
            (
                "can_manage_corporate_assets",
                "Can manage corporation blueprints and jobs",
            ),
            (
                "can_manage_material_exchange",
                "Can manage Material Exchange (approve orders, configure settings)",
            ),
        ]
        default_permissions = ()  # Disable Django's add/change/delete/view permissions

    def __str__(self):
        if self.is_corporate:
            anchor = self.corporation_name or self.corporation_id or "corp"
        else:
            anchor = self.character_name or self.character_id or "character"
        return f"{self.type_name or self.type_id} @ {anchor}"

    @property
    def is_original(self):
        return self.bp_type == self.BPType.ORIGINAL

    @property
    def is_copy(self):
        return self.bp_type in {self.BPType.COPY, "STACK"}

    @property
    def is_reaction(self):
        return self.bp_type == self.BPType.REACTION

    @property
    def is_corporate(self) -> bool:
        return self.owner_kind == self.OwnerKind.CORPORATION

    @property
    def quantity_display(self):
        """Human-readable quantity display"""
        if self.is_reaction:
            return "Reaction"
        if self.is_original:
            return "Original"
        if self.is_copy:
            runs = self.runs or 0
            return f"Copy ({runs} runs)" if runs else "Copy"
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

    @classmethod
    def classify_bp_type(
        cls,
        *,
        quantity: int | None,
        type_name: str | None,
        type_id: int | None,
    ) -> str:
        if type_id and is_reaction_blueprint(type_id):
            return cls.BPType.REACTION

        name = (type_name or "").lower()
        if "formula" in name or "reaction" in name:
            return cls.BPType.REACTION

        if quantity == -2:
            return cls.BPType.COPY
        if quantity == -1:
            return cls.BPType.ORIGINAL
        if quantity and quantity > 0:
            return cls.BPType.COPY

        return cls.BPType.ORIGINAL

    def save(self, *args, **kwargs):
        self.bp_type = self.classify_bp_type(
            quantity=self.quantity,
            type_name=self.type_name,
            type_id=self.type_id,
        )
        if self.bp_type == "STACK":
            self.bp_type = self.BPType.COPY
        super().save(*args, **kwargs)


class IndustryJob(models.Model):
    owner_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="industry_jobs"
    )
    character_id = models.BigIntegerField(blank=True, null=True)
    corporation_id = models.BigIntegerField(blank=True, null=True)
    corporation_name = models.CharField(max_length=255, blank=True)
    owner_kind = models.CharField(
        max_length=16,
        choices=Blueprint.OwnerKind.choices,
        default=Blueprint.OwnerKind.CHARACTER,
    )
    job_id = models.IntegerField(unique=True)
    installer_id = models.IntegerField()
    station_id = models.BigIntegerField(blank=True, null=True)
    location_name = models.CharField(max_length=255, blank=True)
    activity_id = models.IntegerField()
    blueprint_id = models.BigIntegerField()
    blueprint_type_id = models.IntegerField()
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
            models.Index(
                fields=["owner_kind", "corporation_id", "status"],
                name="indy_hub_in_corp_scope_idx",
            ),
        ]
        default_permissions = ()

    def __str__(self):
        if self.owner_kind == Blueprint.OwnerKind.CORPORATION:
            anchor = self.corporation_name or self.corporation_id or "corp"
        else:
            anchor = self.character_name or self.character_id or "character"
        return f"Job {self.job_id} ({self.status}) for {anchor}"

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

        # Copying jobs always show the resulting blueprint copy image when possible
        if self.activity_id == 5:
            copy_type_id = self.product_type_id or self.blueprint_type_id
            return f"https://images.evetech.net/types/{copy_type_id}/bpc?size={size}"

        # When blueprint and product IDs match, prefer the blueprint original artwork
        if self.product_type_id and self.blueprint_type_id == self.product_type_id:
            return f"https://images.evetech.net/types/{self.product_type_id}/bp?size={size}"

        # Otherwise favour the product icon if available
        if self.product_type_id:
            return f"https://images.evetech.net/types/{self.product_type_id}/icon?size={size}"

        # Fallback for missing product IDs – display the blueprint artwork
        return (
            f"https://images.evetech.net/types/{self.blueprint_type_id}/bp?size={size}"
        )

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
        if not self.end_date:
            return ""

        now = timezone.now()
        if self.end_date > now:
            remaining = self.end_date - now
            total_seconds = int(remaining.total_seconds())

            weeks, remainder = divmod(total_seconds, 7 * 24 * 3600)
            days, remainder = divmod(remainder, 24 * 3600)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)

            parts: list[str] = []
            if weeks:
                parts.append(
                    ngettext("%(count)d week", "%(count)d weeks", weeks)
                    % {"count": weeks}
                )
            if days:
                parts.append(
                    ngettext("%(count)d day", "%(count)d days", days) % {"count": days}
                )
            if hours:
                parts.append(
                    ngettext("%(count)d hour", "%(count)d hours", hours)
                    % {"count": hours}
                )
            if minutes:
                parts.append(
                    ngettext(
                        "%(count)d minute",
                        "%(count)d minutes",
                        minutes,
                    )
                    % {"count": minutes}
                )
            if seconds and not (weeks or days or hours):
                parts.append(
                    ngettext(
                        "%(count)d second",
                        "%(count)d seconds",
                        seconds,
                    )
                    % {"count": seconds}
                )

            if not parts:
                parts.append(
                    ngettext(
                        "%(count)d second",
                        "%(count)d seconds",
                        seconds,
                    )
                    % {"count": seconds}
                )

            return ", ".join(parts)

        return _("Completed")


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
        default_permissions = ()
        indexes = [
            models.Index(
                fields=(
                    "type_id",
                    "material_efficiency",
                    "time_efficiency",
                    "fulfilled",
                ),
                name="indy_copy_req_lookup",
            ),
            models.Index(
                fields=("requested_by", "fulfilled"),
                name="indy_copy_req_user_state",
            ),
        ]

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
    accepted_by_seller = models.BooleanField(default=False)

    class Meta:
        unique_together = ("request", "owner")
        default_permissions = ()

    def ensure_chat(self):
        """Return (and create if needed) the chat associated with this offer."""
        chat, created = BlueprintCopyChat.objects.get_or_create(
            offer=self,
            defaults={
                "request": self.request,
                "buyer": self.request.requested_by,
                "seller": self.owner,
            },
        )
        if not created and not chat.is_open:
            chat.reopen()
        return chat


class BlueprintCopyChat(models.Model):
    class CloseReason(models.TextChoices):
        REQUEST_WITHDRAWN = "request_closed", "Request closed"
        OFFER_ACCEPTED = "offer_accepted", "Offer accepted"
        OFFER_REJECTED = "offer_rejected", "Offer rejected"
        EXPIRED = "expired", "Expired"
        MANUAL = "manual", "Closed"
        REOPENED = "reopened", "Reopened"

    request = models.ForeignKey(
        BlueprintCopyRequest,
        on_delete=models.CASCADE,
        related_name="chats",
    )
    offer = models.OneToOneField(
        BlueprintCopyOffer,
        on_delete=models.CASCADE,
        related_name="chat",
    )
    buyer = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="bp_copy_chats_as_buyer",
    )
    seller = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="bp_copy_chats_as_seller",
    )
    is_open = models.BooleanField(default=True)
    closed_reason = models.CharField(max_length=32, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_message_role = models.CharField(max_length=16, blank=True, null=True)
    buyer_last_seen_at = models.DateTimeField(null=True, blank=True)
    seller_last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()
        indexes = [
            models.Index(fields=["request_id", "is_open"], name="bp_copy_chat_state"),
            models.Index(fields=["buyer_id"], name="bp_copy_chat_buyer"),
            models.Index(fields=["seller_id"], name="bp_copy_chat_seller"),
        ]

    def __str__(self):
        return f"Chat for request {self.request_id} / offer {self.offer_id}"

    def role_for(self, user: User) -> str | None:
        if user.id == self.buyer_id:
            return "buyer"
        if user.id == self.seller_id:
            return "seller"
        return None

    def close(self, *, reason: str) -> None:
        if not self.is_open:
            if reason and reason != self.closed_reason:
                self.closed_reason = reason
                self.save(update_fields=["closed_reason", "updated_at"])
            return
        self.is_open = False
        self.closed_reason = reason
        self.closed_at = timezone.now()
        self.save(update_fields=["is_open", "closed_reason", "closed_at", "updated_at"])

    def reopen(self, *, reason: str | None = "") -> None:
        updates = {}
        if not self.is_open:
            self.is_open = True
            updates["is_open"] = True
        if reason is not None:
            self.closed_reason = reason
            updates["closed_reason"] = reason
        self.closed_at = None
        updates["closed_at"] = None
        if updates:
            update_fields = list(updates.keys())
            update_fields.append("updated_at")
            self.save(update_fields=update_fields)

    def register_message(self, *, sender_role: str | None = None) -> None:
        now = timezone.now()
        updates = {"last_message_at": now, "updated_at": now}
        self.last_message_at = now
        self.updated_at = now

        if sender_role:
            updates["last_message_role"] = sender_role
            self.last_message_role = sender_role
            if sender_role == BlueprintCopyMessage.SenderRole.BUYER:
                updates["buyer_last_seen_at"] = now
                self.buyer_last_seen_at = now
            elif sender_role == BlueprintCopyMessage.SenderRole.SELLER:
                updates["seller_last_seen_at"] = now
                self.seller_last_seen_at = now

        self.save(update_fields=list(updates.keys()))

    def mark_seen(self, role: str) -> None:
        if role not in {"buyer", "seller"}:
            return

        field = "buyer_last_seen_at" if role == "buyer" else "seller_last_seen_at"
        last_seen = getattr(self, field)
        target_timestamp = self.last_message_at

        if not target_timestamp:
            if not last_seen:
                now = timezone.now()
                setattr(self, field, now)
                self.save(update_fields=[field, "updated_at"])
            return

        if self.last_message_role in {
            None,
            "",
            role,
            BlueprintCopyMessage.SenderRole.SYSTEM,
        }:
            if not last_seen:
                now = timezone.now()
                setattr(self, field, now)
                self.save(update_fields=[field, "updated_at"])
            return

        if last_seen and last_seen >= target_timestamp:
            return

        now = timezone.now()
        setattr(self, field, now)
        self.save(update_fields=[field, "updated_at"])

    def has_unread_for(self, role: str) -> bool:
        if role not in {"buyer", "seller"}:
            return False

        if not self.last_message_at:
            return False

        if self.last_message_role in {
            None,
            "",
            role,
            BlueprintCopyMessage.SenderRole.SYSTEM,
        }:
            return False

        last_seen = (
            self.buyer_last_seen_at if role == "buyer" else self.seller_last_seen_at
        )
        if not last_seen:
            return True
        return last_seen < self.last_message_at


class BlueprintCopyMessage(models.Model):
    class SenderRole(models.TextChoices):
        BUYER = "buyer", "Buyer"
        SELLER = "seller", "Builder"
        SYSTEM = "system", "System"

    chat = models.ForeignKey(
        BlueprintCopyChat,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="bp_copy_messages",
        null=True,
        blank=True,
    )
    sender_role = models.CharField(
        max_length=16,
        choices=SenderRole.choices,
    )
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["chat_id", "created_at"], name="bp_copy_msg_chat"),
        ]

    def __str__(self):
        return f"ChatMessage {self.id} in chat {self.chat_id}"

    def clean(self):
        super().clean()
        content = (self.content or "").strip()
        if not content:
            raise ValidationError("Message content cannot be empty.")
        if len(content) > 2000:
            raise ValidationError("Message content cannot exceed 2000 characters.")
        self.content = content


class UserOnboardingProgress(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="indy_onboarding",
    )
    dismissed = models.BooleanField(default=False)
    manual_steps = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        verbose_name = _("Onboarding progress")
        verbose_name_plural = _("Onboarding progress")

    def __str__(self):
        return f"Onboarding for {self.user.username}"

    def mark_step(self, key: str, completed: bool) -> None:
        manual = self.manual_steps.copy()
        if completed:
            manual[key] = True
        else:
            manual.pop(key, None)
        self.manual_steps = manual


class CharacterSettings(models.Model):
    """
    Collect user preferences for job notifications and blueprint copy sharing.
    """

    SCOPE_NONE = "none"
    SCOPE_CORPORATION = "corporation"
    SCOPE_ALLIANCE = "alliance"
    SCOPE_EVERYONE = "everyone"
    COPY_SHARING_SCOPE_CHOICES = [
        (SCOPE_NONE, "None"),
        (SCOPE_CORPORATION, "Corporation"),
        (SCOPE_ALLIANCE, "Alliance"),
        (SCOPE_EVERYONE, "Everyone"),
    ]

    NOTIFY_DISABLED = "disabled"
    NOTIFY_IMMEDIATE = "immediate"
    NOTIFY_DAILY = "daily"
    NOTIFY_WEEKLY = "weekly"
    NOTIFY_MONTHLY = "monthly"
    NOTIFY_CUSTOM = "custom"

    JOB_NOTIFICATION_FREQUENCY_CHOICES = [
        (NOTIFY_DISABLED, "Disabled"),
        (NOTIFY_IMMEDIATE, "Immediate"),
        (NOTIFY_DAILY, "Daily digest"),
        (NOTIFY_WEEKLY, "Weekly digest"),
        (NOTIFY_MONTHLY, "Monthly digest"),
        (NOTIFY_CUSTOM, "Custom cadence"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    character_id = models.BigIntegerField()
    jobs_notify_completed = models.BooleanField(default=False)
    jobs_notify_frequency = models.CharField(
        max_length=20,
        choices=JOB_NOTIFICATION_FREQUENCY_CHOICES,
        default=NOTIFY_DISABLED,
    )
    jobs_notify_custom_days = models.PositiveSmallIntegerField(default=3)
    jobs_next_digest_at = models.DateTimeField(null=True, blank=True)
    jobs_last_digest_at = models.DateTimeField(null=True, blank=True)
    allow_copy_requests = models.BooleanField(default=False)
    copy_sharing_scope = models.CharField(
        max_length=20,
        choices=COPY_SHARING_SCOPE_CHOICES,
        default=SCOPE_NONE,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Character setting")
        verbose_name_plural = _("Character settings")
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["user", "character_id"],
                name="charsettings_user_char_uq",
            )
        ]
        indexes = [
            models.Index(
                fields=["user", "character_id"],
                name="charsettings_user_char_idx",
            )
        ]

    def __str__(self):
        return f"Settings for {self.user.username}#{self.character_id}"

    def set_copy_sharing_scope(self, scope):
        if scope not in dict(self.COPY_SHARING_SCOPE_CHOICES):
            raise ValueError(f"Invalid copy sharing scope: {scope}")
        self.copy_sharing_scope = scope
        self.allow_copy_requests = scope != self.SCOPE_NONE

    def set_job_notification_frequency(
        self,
        frequency: str,
        *,
        custom_days: int | None = None,
    ) -> None:
        valid = dict(self.JOB_NOTIFICATION_FREQUENCY_CHOICES)
        if frequency not in valid:
            raise ValueError(f"Invalid job notification frequency: {frequency}")
        self.jobs_notify_frequency = frequency
        if frequency == self.NOTIFY_DISABLED:
            self.jobs_notify_completed = False
            self.jobs_next_digest_at = None
        else:
            self.jobs_notify_completed = True

        if frequency == self.NOTIFY_CUSTOM:
            days_value = (
                custom_days if custom_days is not None else self.jobs_notify_custom_days
            )
            try:
                days_value = int(days_value)
            except (TypeError, ValueError):
                days_value = self.jobs_notify_custom_days or 1
            self.jobs_notify_custom_days = max(1, days_value)

    def compute_next_digest(self, *, reference=None):
        if reference is None:
            reference = timezone.now()

        freq = self.jobs_notify_frequency
        if freq in {self.NOTIFY_DISABLED, self.NOTIFY_IMMEDIATE}:
            return None

        if freq == self.NOTIFY_DAILY:
            delta = timedelta(days=1)
        elif freq == self.NOTIFY_WEEKLY:
            delta = timedelta(days=7)
        elif freq == self.NOTIFY_MONTHLY:
            delta = timedelta(days=30)
        else:
            days = max(1, self.jobs_notify_custom_days or 1)
            delta = timedelta(days=days)

        return reference + delta

    def schedule_next_digest(self, *, reference=None) -> None:
        next_at = self.compute_next_digest(reference=reference)
        self.jobs_next_digest_at = next_at

    def save(self, *args, **kwargs):
        if (
            self.jobs_notify_completed
            and self.jobs_notify_frequency == self.NOTIFY_DISABLED
        ):
            self.jobs_notify_frequency = self.NOTIFY_IMMEDIATE

        if self.jobs_notify_frequency == self.NOTIFY_DISABLED:
            self.jobs_notify_completed = False
            self.jobs_next_digest_at = None
        elif self.jobs_notify_frequency:
            self.jobs_notify_completed = True
            if (
                self.jobs_notify_frequency != self.NOTIFY_IMMEDIATE
                and not self.jobs_next_digest_at
            ):
                self.schedule_next_digest()
        super().save(*args, **kwargs)


class JobNotificationDigestEntry(models.Model):
    """Queued job notifications awaiting digest dispatch."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="indy_job_notification_entries",
    )
    job_id = models.BigIntegerField()
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["user", "job_id"],
                name="job_digest_user_job_uq",
            )
        ]
        indexes = [
            models.Index(
                fields=["user", "sent_at"],
                name="job_digest_user_sent_idx",
            ),
        ]

    def __str__(self):
        status = "sent" if self.sent_at else "pending"
        return f"Job digest entry {self.job_id} for {self.user.username} ({status})"

    @property
    def is_sent(self) -> bool:
        return self.sent_at is not None

    def mark_sent(self) -> None:
        self.sent_at = timezone.now()


class CorporationSharingSetting(models.Model):
    """Stores per-corporation blueprint sharing preferences for a user."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="indy_corporation_sharing",
    )
    corporation_id = models.BigIntegerField()
    corporation_name = models.CharField(max_length=255, blank=True)
    share_scope = models.CharField(
        max_length=20,
        choices=CharacterSettings.COPY_SHARING_SCOPE_CHOICES,
        default=CharacterSettings.SCOPE_NONE,
    )
    allow_copy_requests = models.BooleanField(default=False)
    authorized_characters = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["user", "corporation_id"],
                name="corp_sharing_user_corp_uq",
            )
        ]
        indexes = [
            models.Index(
                fields=["user", "corporation_id"],
                name="corp_sharing_user_corp_idx",
            ),
            models.Index(
                fields=["user", "share_scope"],
                name="corp_sharing_user_scope_idx",
            ),
        ]

    def __str__(self):
        corp_display = self.corporation_name or str(self.corporation_id)
        scope_labels = dict(CharacterSettings.COPY_SHARING_SCOPE_CHOICES)
        scope_display = scope_labels.get(self.share_scope, self.share_scope)
        return f"{self.user.username} -> {corp_display} [{scope_display}]"

    def set_share_scope(self, scope: str) -> None:
        if scope not in dict(CharacterSettings.COPY_SHARING_SCOPE_CHOICES):
            raise ValueError(f"Invalid copy sharing scope: {scope}")
        self.share_scope = scope
        self.allow_copy_requests = scope != CharacterSettings.SCOPE_NONE

    def set_authorized_characters(self, character_ids) -> None:
        """Replace the manual authorization list with the provided character IDs."""

        normalized: list[int] = []
        for value in character_ids or []:
            try:
                normalized.append(int(value))
            except (TypeError, ValueError):
                continue
        unique_sorted = sorted(set(normalized))
        self.authorized_characters = unique_sorted

    def _normalized_authorized_characters(self) -> list[int]:
        normalized: list[int] = []
        for value in self.authorized_characters or []:
            try:
                normalized.append(int(value))
            except (TypeError, ValueError):
                continue
        return sorted(set(normalized))

    @property
    def authorized_character_ids(self) -> list[int]:
        return self._normalized_authorized_characters()

    @property
    def restricts_characters(self) -> bool:
        return bool(self._normalized_authorized_characters())

    def is_character_authorized(self, character_id: int | None) -> bool:
        allowed = self._normalized_authorized_characters()
        if not allowed:
            return True
        try:
            normalized = int(character_id)
        except (TypeError, ValueError):
            return False
        return normalized in allowed


class ProductionConfig(models.Model):
    PRODUCTION_CHOICES = [
        ("prod", "Produce"),
        ("buy", "Buy"),
        ("useless", "Useless"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    simulation = models.ForeignKey(
        "ProductionSimulation",
        on_delete=models.CASCADE,
        related_name="production_configs",
        null=True,
        blank=True,
    )
    blueprint_type_id = models.BigIntegerField()  # Type ID of the main blueprint
    item_type_id = models.BigIntegerField()  # Type ID of the item within the tree
    production_mode = models.CharField(
        max_length=10,
        choices=PRODUCTION_CHOICES,
        default="prod",
    )
    quantity_needed = models.BigIntegerField(default=0)
    runs = models.IntegerField(default=1)  # Number of runs for the main blueprint
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("simulation", "item_type_id")
        default_permissions = ()
        indexes = [
            models.Index(fields=["user", "blueprint_type_id", "runs"]),
            models.Index(fields=["user", "item_type_id"]),
            models.Index(fields=["simulation", "item_type_id"]),
        ]

    def __str__(self):
        return (
            f"{self.user.username} - BP:{self.blueprint_type_id} - "
            f"Item:{self.item_type_id} - {self.production_mode}"
        )


class BlueprintEfficiency(models.Model):
    """
    Store the user-defined ME/TE values for each blueprint.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    blueprint_type_id = models.BigIntegerField()
    simulation = models.ForeignKey(
        "ProductionSimulation",
        on_delete=models.CASCADE,
        related_name="blueprint_efficiencies",
    )
    material_efficiency = models.IntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(10)]
    )
    time_efficiency = models.IntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(20)]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("simulation", "blueprint_type_id")
        default_permissions = ()
        indexes = [
            models.Index(fields=["user", "blueprint_type_id"]),
            models.Index(fields=["simulation", "blueprint_type_id"]),
        ]

    def __str__(self):
        return (
            f"{self.user.username} - BP:{self.blueprint_type_id} - "
            f"ME:{self.material_efficiency} TE:{self.time_efficiency}"
        )


class CustomPrice(models.Model):
    """
    Store the user-defined manual prices for each item in the Financial tab.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    item_type_id = models.BigIntegerField()
    simulation = models.ForeignKey(
        "ProductionSimulation", on_delete=models.CASCADE, related_name="custom_prices"
    )
    unit_price = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    is_sale_price = models.BooleanField(
        default=False
    )  # True when this is the sale price of the final product
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("simulation", "item_type_id")
        default_permissions = ()
        indexes = [
            models.Index(fields=["user", "item_type_id"]),
            models.Index(fields=["simulation", "item_type_id"]),
        ]

    def __str__(self):
        price_type = "Sale" if self.is_sale_price else "Cost"
        return f"{self.user.username} - Item:{self.item_type_id} - {price_type}: {self.unit_price}"


class ProductionSimulation(models.Model):
    """
    Metadata for saved production simulations per user.
    Each simulation stores every configuration: toggles, ME/TE, manual prices, and more.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    blueprint_type_id = models.BigIntegerField()
    blueprint_name = models.CharField(max_length=255)
    runs = models.IntegerField(default=1)
    simulation_name = models.CharField(max_length=255, blank=True)

    # Summary metadata
    total_items = models.IntegerField(default=0)
    total_buy_items = models.IntegerField(default=0)
    total_prod_items = models.IntegerField(default=0)
    estimated_cost = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    estimated_revenue = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    estimated_profit = models.DecimalField(max_digits=20, decimal_places=2, default=0)

    # Overall simulation configuration
    active_tab = models.CharField(max_length=50, default="materials")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "blueprint_type_id", "runs")
        default_permissions = ()
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "-updated_at"]),
            models.Index(fields=["user", "blueprint_type_id"]),
        ]

    def __str__(self):
        name = self.simulation_name or f"{self.blueprint_name} x{self.runs}"
        return f"{self.user.username} - {name}"

    @property
    def display_name(self):
        if self.simulation_name:
            return f"{self.simulation_name} ({self.blueprint_name} x{self.runs})"
        return f"{self.blueprint_name} x{self.runs}"

    def get_production_configs(self):
        """Return every Prod/Buy/Useless configuration for this simulation."""
        return self.production_configs.all()

    @property
    def productionconfig_set(self):
        """Legacy compatibility for the old Django relation name."""
        return self.production_configs

    def get_blueprint_efficiencies(self):
        """Return every ME/TE configuration for this simulation."""
        return self.blueprint_efficiencies.all()

    def get_custom_prices(self):
        """Return every manual price for this simulation."""
        return self.custom_prices.all()

    @property
    def product_type_id(self) -> int | None:
        """Return the likely manufactured item type id for icon display."""
        product_id = get_blueprint_product_type_id(self.blueprint_type_id)
        if product_id:
            return product_id
        if self.blueprint_type_id:
            try:
                return int(self.blueprint_type_id)
            except (TypeError, ValueError):
                return None
        return None

    @property
    def product_icon_url(self) -> str | None:
        """Return the product render URL if available (matching blueprint listing)."""
        type_id = self.product_type_id
        if not type_id:
            return None
        return f"https://images.evetech.net/types/{type_id}/render?size=32"

    @property
    def blueprint_icon_url(self) -> str | None:
        """Return the blueprint icon URL for fallback display."""
        if not self.blueprint_type_id:
            return None
        return (
            f"https://images.evetech.net/types/{int(self.blueprint_type_id)}/bp?size=32"
        )

    @property
    def profit_margin(self):
        """Calculate the profit margin percentage."""
        if self.estimated_revenue > 0:
            return float((self.estimated_profit / self.estimated_revenue) * 100)
        return 0.0


# ============================================================================
# Material Exchange (Corp Supply Hub) Models
# ============================================================================


class MaterialExchangeConfig(models.Model):
    """
    Global configuration for the Material Exchange hub.
    Stores structure location, hangar division, and pricing rules.
    """

    # ESI targeting
    corporation_id = models.BigIntegerField(
        help_text=_("Corporation ID owning the hub hangar")
    )
    structure_id = models.BigIntegerField(
        help_text=_("Structure or station ID where the hub is located")
    )
    structure_name = models.CharField(max_length=255, blank=True)
    hangar_division = models.IntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(7)],
        help_text=_("Corp hangar division (1-7)"),
    )

    # Pricing rules
    PRICE_BASE_CHOICES = [
        ("buy", _("Jita Buy Price")),
        ("sell", _("Jita Sell Price")),
    ]

    sell_markup_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text=_("Markup % applied when members sell to hub"),
    )
    sell_markup_base = models.CharField(
        max_length=10,
        choices=PRICE_BASE_CHOICES,
        default="buy",
        help_text=_("Base price to apply sell markup on (Jita Buy or Jita Sell)"),
    )

    buy_markup_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=5,
        help_text=_("Markup % applied when members buy from hub"),
    )
    buy_markup_base = models.CharField(
        max_length=10,
        choices=PRICE_BASE_CHOICES,
        default="buy",
        help_text=_("Base price to apply buy markup on (Jita Buy or Jita Sell)"),
    )

    # Stock sync
    last_stock_sync = models.DateTimeField(blank=True, null=True)
    last_price_sync = models.DateTimeField(blank=True, null=True)

    # Status
    is_active = models.BooleanField(
        default=True, help_text=_("Enable/disable the Material Exchange")
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Material Exchange Configuration")
        verbose_name_plural = _("Material Exchange Configurations")

    def __str__(self):
        return f"Material Exchange Config (Corp {self.corporation_id})"


class MaterialExchangeStock(models.Model):
    """
    Cached stock levels from corptools corp assets.
    Refreshed periodically via Celery task.
    Single source of truth for Material Exchange inventory.
    """

    config = models.ForeignKey(
        MaterialExchangeConfig, on_delete=models.CASCADE, related_name="stock_items"
    )
    type_id = models.IntegerField(help_text=_("EVE item type ID"))
    type_name = models.CharField(max_length=255, blank=True, db_index=True)
    quantity = models.BigIntegerField(default=0)

    # Pricing cache (from Jita via Fuzzwork)
    jita_buy_price = models.DecimalField(
        max_digits=20, decimal_places=2, default=0, blank=True
    )
    jita_sell_price = models.DecimalField(
        max_digits=20, decimal_places=2, default=0, blank=True
    )
    last_price_update = models.DateTimeField(blank=True, null=True)

    # Audit trail
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    last_stock_sync = models.DateTimeField(
        blank=True, null=True, help_text=_("When this item's quantity was last synced")
    )

    class Meta:
        verbose_name = _("Material Exchange Stock")
        verbose_name_plural = _("Material Exchange Stock")
        unique_together = ("config", "type_id")
        indexes = [
            models.Index(fields=["type_id"]),
            models.Index(fields=["config", "type_id"]),
            models.Index(fields=["config", "quantity"], name="mes_config_qty_idx"),
            models.Index(fields=["config", "updated_at"], name="mes_config_upd_idx"),
            models.Index(fields=["type_name"], name="mes_typename_idx"),
        ]

    def __str__(self):
        return f"{self.type_name or self.type_id} x{self.quantity}"

    @property
    def sell_price_to_member(self):
        """Price when member buys FROM hub (base price + buy_markup)."""
        if not self.config:
            return 0
        # Choose base price according to config
        if self.config.buy_markup_base == "sell":
            base = self.jita_sell_price or 0
        else:
            base = self.jita_buy_price or 0
        markup = self.config.buy_markup_percent / 100
        return base * (1 + markup)

    @property
    def buy_price_from_member(self):
        """Price when member sells TO hub (base price + sell_markup)."""
        if not self.config:
            return 0
        # Choose base price according to config
        if self.config.sell_markup_base == "sell":
            base = self.jita_sell_price or 0
        else:
            base = self.jita_buy_price or 0
        markup = self.config.sell_markup_percent / 100
        return base * (1 + markup)


class MaterialExchangeSellOrder(models.Model):
    """
    A member wants to sell materials TO the corp hub.
    One order can contain multiple items (materials).
    Flow: request → admin approval → payment verification → complete.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending Approval")
        APPROVED = "approved", _("Approved - Awaiting Payment")
        PAID = "paid", _("Payment Verified")
        COMPLETED = "completed", _("Completed")
        REJECTED = "rejected", _("Rejected")
        CANCELLED = "cancelled", _("Cancelled")

    config = models.ForeignKey(
        MaterialExchangeConfig,
        on_delete=models.CASCADE,
        related_name="sell_orders",
    )
    seller = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="material_sell_orders"
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )

    # Approval/processing
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_sell_orders",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    payment_verified_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_sell_payments",
    )
    payment_verified_at = models.DateTimeField(null=True, blank=True)
    payment_journal_ref = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("ESI wallet journal ref ID if verified"),
    )

    order_reference = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        db_index=True,
        help_text=_("Unique order reference (INDY-{id}) for contract matching"),
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Material Exchange Sell Order")
        verbose_name_plural = _("Material Exchange Sell Orders")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["seller", "-created_at"]),
        ]

    def __str__(self):
        return f"Sell #{self.id}: {self.seller.username} ({self.items.count()} items)"

    def save(self, *args, **kwargs):
        """Auto-generate order reference if not set."""
        if not self.order_reference:
            # Save first to get the ID
            super().save(*args, **kwargs)
            # Then set the reference and save again
            self.order_reference = f"INDY-{self.id}"
            super().save(update_fields=["order_reference"])
        else:
            super().save(*args, **kwargs)

    @property
    def total_price(self):
        """Sum of all item prices"""
        return sum(item.total_price for item in self.items.all())

    @property
    def total_quantity(self):
        """Total quantity across all items"""
        return sum(item.quantity for item in self.items.all())

    @property
    def esi_contract_validated(self):
        """All items in order are validated"""
        items = self.items.all()
        if not items.exists():
            return False
        return all(item.esi_contract_validated for item in items)

    @property
    def esi_validation_checked_at(self):
        """Most recent validation check time"""
        latest = self.items.all().order_by("-esi_validation_checked_at").first()
        return latest.esi_validation_checked_at if latest else None


class MaterialExchangeSellOrderItem(models.Model):
    """
    Individual item in a sell order.
    """

    order = models.ForeignKey(
        MaterialExchangeSellOrder,
        on_delete=models.CASCADE,
        related_name="items",
    )
    type_id = models.IntegerField(help_text=_("Material type ID"))
    type_name = models.CharField(max_length=255, blank=True)
    quantity = models.BigIntegerField(validators=[MinValueValidator(1)])

    # Pricing snapshot at order creation
    unit_price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        help_text=_("Price per unit corp pays to member (Jita Buy + markup)"),
    )
    total_price = models.DecimalField(max_digits=20, decimal_places=2)

    # ESI contract validation
    esi_contract_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text=_("ESI contract ID for this item"),
    )
    esi_contract_validated = models.BooleanField(
        default=False,
        help_text=_("Whether ESI validation confirmed this item in contract"),
    )
    esi_validation_checked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Last time ESI contract was validated for this item"),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Material Exchange Sell Order Item")
        verbose_name_plural = _("Material Exchange Sell Order Items")
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["type_id"]),
            models.Index(fields=["order"]),
        ]

    def __str__(self):
        return f"SellItem #{self.id}: {self.type_name} x{self.quantity}"


class MaterialExchangeBuyOrder(models.Model):
    """
    A member wants to buy materials FROM the corp hub.
    One order can contain multiple items (materials).
    Flow: request → stock check → admin approval → contract/delivery → complete.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending Approval")
        APPROVED = "approved", _("Approved - Awaiting Delivery")
        DELIVERED = "delivered", _("Delivered")
        COMPLETED = "completed", _("Completed")
        REJECTED = "rejected", _("Rejected")
        CANCELLED = "cancelled", _("Cancelled")

    config = models.ForeignKey(
        MaterialExchangeConfig,
        on_delete=models.CASCADE,
        related_name="buy_orders",
    )
    buyer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="material_buy_orders"
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )

    # Approval/processing
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_buy_orders",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    delivered_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivered_buy_orders",
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_method = models.CharField(
        max_length=50,
        blank=True,
        choices=[
            ("contract", _("Contract")),
            ("trade", _("Direct Trade")),
            ("hangar", _("Corp Hangar Access")),
        ],
    )

    order_reference = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        db_index=True,
        help_text=_("Unique order reference (INDY-{id}) for contract matching"),
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Material Exchange Buy Order")
        verbose_name_plural = _("Material Exchange Buy Orders")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["buyer", "-created_at"]),
        ]

    def __str__(self):
        return f"Buy #{self.id}: {self.buyer.username} ({self.items.count()} items)"

    def save(self, *args, **kwargs):
        """Auto-generate order reference if not set."""
        if not self.order_reference:
            # Save first to get the ID
            super().save(*args, **kwargs)
            # Then set the reference and save again
            self.order_reference = f"INDY-{self.id}"
            super().save(update_fields=["order_reference"])
        else:
            super().save(*args, **kwargs)

    @property
    def total_price(self):
        """Sum of all item prices"""
        return sum(item.total_price for item in self.items.all())

    @property
    def total_quantity(self):
        """Total quantity across all items"""
        return sum(item.quantity for item in self.items.all())

    @property
    def esi_contract_validated(self):
        """All items in order are validated"""
        items = self.items.all()
        if not items.exists():
            return False
        return all(item.esi_contract_validated for item in items)

    @property
    def esi_validation_checked_at(self):
        """Most recent validation check time"""
        latest = self.items.all().order_by("-esi_validation_checked_at").first()
        return latest.esi_validation_checked_at if latest else None


class MaterialExchangeBuyOrderItem(models.Model):
    """
    Individual item in a buy order.
    """

    order = models.ForeignKey(
        MaterialExchangeBuyOrder,
        on_delete=models.CASCADE,
        related_name="items",
    )
    type_id = models.IntegerField(help_text=_("Material type ID"))
    type_name = models.CharField(max_length=255, blank=True)
    quantity = models.BigIntegerField(validators=[MinValueValidator(1)])

    # Pricing snapshot at order creation
    unit_price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        help_text=_("Price per unit member pays to corp (Jita Sell + markup)"),
    )
    total_price = models.DecimalField(max_digits=20, decimal_places=2)

    # Stock check at creation
    stock_available_at_creation = models.BigIntegerField(default=0)

    # ESI contract validation
    esi_contract_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text=_("ESI contract ID for this item"),
    )
    esi_contract_validated = models.BooleanField(
        default=False,
        help_text=_("Whether ESI validation confirmed this item in contract"),
    )
    esi_validation_checked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Last time ESI contract was validated for this item"),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Material Exchange Buy Order Item")
        verbose_name_plural = _("Material Exchange Buy Order Items")
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["type_id"]),
            models.Index(fields=["order"]),
        ]

    def __str__(self):
        return f"BuyItem #{self.id}: {self.type_name} x{self.quantity}"


class MaterialExchangeTransaction(models.Model):
    """
    Complete transaction log for finance reporting.
    Created when sell or buy order is completed.
    """

    class TransactionType(models.TextChoices):
        SELL = "sell", _("Member Sold to Hub")
        BUY = "buy", _("Member Bought from Hub")

    config = models.ForeignKey(
        MaterialExchangeConfig,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    transaction_type = models.CharField(max_length=10, choices=TransactionType.choices)

    # Link to original order
    sell_order = models.OneToOneField(
        MaterialExchangeSellOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transaction",
    )
    buy_order = models.OneToOneField(
        MaterialExchangeBuyOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transaction",
    )

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="material_transactions"
    )
    type_id = models.IntegerField()
    type_name = models.CharField(max_length=255)
    quantity = models.BigIntegerField()
    unit_price = models.DecimalField(max_digits=20, decimal_places=2)
    total_price = models.DecimalField(max_digits=20, decimal_places=2)

    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Material Exchange Transaction")
        verbose_name_plural = _("Material Exchange Transactions")
        ordering = ["-completed_at"]
        indexes = [
            models.Index(fields=["transaction_type", "-completed_at"]),
            models.Index(fields=["user", "-completed_at"]),
            models.Index(fields=["type_id", "-completed_at"]),
        ]

    def __str__(self):
        return f"{self.get_transaction_type_display()} #{self.id}: {self.user.username} - {self.type_name} x{self.quantity}"
