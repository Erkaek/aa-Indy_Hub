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
            navactive=["indy_hub:index", "indy_hub:blueprints_list", "indy_hub:jobs_list", "indy_hub:token_management"],
        )

    def render(self, request):
        # Only show to authenticated users
        if request.user.is_authenticated:
            return super().render(request)
        return ""

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
