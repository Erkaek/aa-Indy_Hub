# Django
from django.urls import include, path

# Alliance Auth
from allianceauth import urls

urlpatterns = [
    # Alliance Auth URLs
    path("", include(urls)),
]
