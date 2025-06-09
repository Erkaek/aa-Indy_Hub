# Django
from django import template

register = template.Library()


@register.filter
def dict_get(d, key):
    try:
        return d.get(key)
    except Exception:
        return None
