# Django
from django.core.cache import cache

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
            1100,
            navactive=[
                "indy_hub:",  # any view inside the Indy Hub namespace
                "indy_hub:index",
                "indy_hub:blueprints_list",
                "indy_hub:jobs_list",
                "indy_hub:token_management",
            ],
        )
        self.template = "indy_hub/includes/menuitem_live_badge.html"

    def render(self, request):
        # Only show to authenticated users with the correct permission
        if not request.user.is_authenticated:
            return ""
        if not request.user.has_perm("indy_hub.can_access_indy_hub"):
            return ""

        cache_key = f"indy_hub:menu_badge_count:{request.user.id}"
        cached_count = cache.get(cache_key)
        if cached_count is not None:
            self.count = cached_count if cached_count > 0 else None
            return super().render(request)

        # Render immediately when cache is cold; badge appears once cache is warmed.
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
