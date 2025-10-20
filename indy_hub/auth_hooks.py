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
            from .models import Blueprint, BlueprintCopyRequest

            bps = Blueprint.objects.filter(
                owner_user=request.user,
                owner_kind=Blueprint.OwnerKind.CHARACTER,
                quantity=-1,
            )
            count = (
                BlueprintCopyRequest.objects.filter(
                    type_id__in=bps.values_list("type_id", flat=True),
                    material_efficiency__in=bps.values_list(
                        "material_efficiency", flat=True
                    ),
                    time_efficiency__in=bps.values_list("time_efficiency", flat=True),
                    fulfilled=False,
                )
                .exclude(requested_by=request.user)
                .count()
            )
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
