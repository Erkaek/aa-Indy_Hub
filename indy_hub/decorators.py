# indy_hub/decorators.py
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages

# Import ESI Token only when needed to avoid import issues
try:
    from esi.models import Token
except ImportError:
    Token = None

def token_required(scopes=None):
    """
    Decorator that checks if the user has valid ESI tokens with required scopes.
    If not, redirects to token authorization page.
    """
    if scopes is None:
        scopes = []
    
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('auth_login_user')
            
            # Check if user has tokens with required scopes
            if Token:
                try:
                    tokens = Token.objects.filter(user=request.user).require_scopes(scopes)
                    if not tokens.exists():
                        # User doesn't have tokens with required scopes
                        scope_names = ', '.join(scopes)
                        messages.warning(
                            request, 
                            f'You need to authorize ESI access with the following scopes: {scope_names}'
                        )
                        return redirect('indy_hub:token_management')
                except Exception as e:
                    messages.error(request, f'Error checking ESI tokens: {e}')
                    return redirect('indy_hub:token_management')
            else:
                messages.error(request, 'ESI module not available')
                return redirect('indy_hub:index')
            
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator

def blueprints_token_required(view_func):
    """Decorator specifically for blueprint views."""
    return token_required(['esi-characters.read_blueprints.v1'])(view_func)

def industry_jobs_token_required(view_func):
    """Decorator specifically for industry jobs views."""
    return token_required(['esi-industry.read_character_jobs.v1'])(view_func)
