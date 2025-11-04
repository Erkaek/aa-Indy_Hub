# Django
from django.db.models import F, Q

# Alliance Auth
from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from . import urls


class IndyHubMenu(MenuItemHook):
    """
    Adds a menu item for Indy Hub in Alliance Auth navigation.
    """

    def __init__(self):
        super().__init__(
            "Indy Hub",
            "fas fa-industry fa-fw",
            "indy_hub:index",
            navactive=[
                "indy_hub:index",
                "indy_hub:blueprints_list",
                "indy_hub:jobs_list",
                "indy_hub:token_management",
            ],
        )

    def render(self, request):
        # Only show to authenticated users with the correct permission
        if not request.user.is_authenticated:
            return ""
        if not request.user.has_perm("indy_hub.can_access_indy_hub"):
            return ""
        # Calculate pending copy requests count
        try:
            from .models import (
                Blueprint,
                BlueprintCopyChat,
                BlueprintCopyRequest,
            )

            pending_request_ids: set[int] = set()

            blueprint_specs = list(
                Blueprint.objects.filter(owner_user=request.user)
                .filter(
                    bp_type__in=[
                        Blueprint.BPType.ORIGINAL,
                        Blueprint.BPType.REACTION,
                    ]
                )
                .values("type_id", "material_efficiency", "time_efficiency")
            )

            fulfill_filters = Q()
            for spec in blueprint_specs:
                fulfill_filters |= Q(
                    type_id=spec["type_id"],
                    material_efficiency=spec["material_efficiency"],
                    time_efficiency=spec["time_efficiency"],
                )

            if fulfill_filters:
                fulfill_qs = (
                    BlueprintCopyRequest.objects.filter(fulfill_filters)
                    .filter(
                        Q(fulfilled=False)
                        | Q(
                            fulfilled=True,
                            delivered=False,
                            offers__owner=request.user,
                        )
                    )
                    .exclude(requested_by=request.user)
                    .exclude(
                        offers__owner=request.user,
                        offers__status="rejected",
                    )
                )
                pending_request_ids.update(fulfill_qs.values_list("id", flat=True))

            unread_chat_qs = BlueprintCopyChat.objects.filter(
                is_open=True,
                last_message_at__isnull=False,
            ).filter(
                (
                    Q(buyer=request.user, last_message_role="seller")
                    & (
                        Q(buyer_last_seen_at__isnull=True)
                        | Q(buyer_last_seen_at__lt=F("last_message_at"))
                    )
                )
                | (
                    Q(seller=request.user, last_message_role="buyer")
                    & (
                        Q(seller_last_seen_at__isnull=True)
                        | Q(seller_last_seen_at__lt=F("last_message_at"))
                    )
                )
            )

            pending_request_ids.update(
                unread_chat_qs.values_list("request_id", flat=True)
            )

            count = len(pending_request_ids)
            self.count = count if count > 0 else None
        except Exception:
            self.count = None
        # Delegate rendering to base class
        return super().render(request)


@hooks.register("menu_item_hook")
def register_menu():
    """
    Register the IndyHub menu item.
    """
    return IndyHubMenu()


@hooks.register("url_hook")
def register_urls():
    """
    Register IndyHub URL patterns.
    """
    return UrlHook(urls, "indy_hub", r"^indy_hub/")
