# User-related views
# Standard Library
import logging
import secrets
from urllib.parse import urlencode

# Django
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from esi.models import CallbackRedirect, Token

# AA Example App
from indy_hub.models import CharacterSettings

from ..decorators import indy_hub_access_required
from ..models import Blueprint, IndustryJob, ProductionConfig, ProductionSimulation
from ..tasks.industry import update_blueprints_for_user, update_industry_jobs_for_user
from ..utils.eve import get_character_name

logger = logging.getLogger(__name__)


# --- User views (token management, sync, etc.) ---
@indy_hub_access_required
@login_required
def index(request):
    """
    Home page for Indy Hub module.
    """
    blueprint_tokens = None
    jobs_tokens = None
    if Token:
        try:
            blueprint_tokens = Token.objects.filter(user=request.user).require_scopes(
                ["esi-characters.read_blueprints.v1"]
            )
            jobs_tokens = Token.objects.filter(user=request.user).require_scopes(
                ["esi-industry.read_character_jobs.v1"]
            )
            # Deduplicate by character_id
            blueprint_char_ids = (
                list(blueprint_tokens.values_list("character_id", flat=True).distinct())
                if blueprint_tokens
                else []
            )
            jobs_char_ids = (
                list(jobs_tokens.values_list("character_id", flat=True).distinct())
                if jobs_tokens
                else []
            )
        except Exception:
            blueprint_tokens = jobs_tokens = None
            blueprint_char_ids = jobs_char_ids = []
    user_chars = []
    ownerships = CharacterOwnership.objects.filter(user=request.user)
    for ownership in ownerships:
        cid = ownership.character.character_id
        user_chars.append(
            {
                "character_id": cid,
                "name": get_character_name(cid),
                "bp_enabled": (
                    blueprint_tokens.filter(character_id=cid).exists()
                    if blueprint_tokens
                    else False
                ),
                "jobs_enabled": (
                    jobs_tokens.filter(character_id=cid).exists()
                    if jobs_tokens
                    else False
                ),
            }
        )
    # Blueprints stats
    blueprints_qs = Blueprint.objects.filter(owner_user=request.user)
    blueprint_count = blueprints_qs.count()
    original_blueprints = blueprints_qs.filter(quantity=-1).count()
    copy_blueprints = blueprints_qs.filter(quantity=-2).count()
    stack_blueprints = blueprints_qs.filter(quantity__gt=0).count()
    # Jobs stats
    jobs_qs = IndustryJob.objects.filter(owner_user=request.user)

    now = timezone.now()
    today = now.date()
    active_jobs_count = jobs_qs.filter(status="active", end_date__gt=now).count()
    completed_jobs_count = jobs_qs.filter(end_date__lte=now).count()
    completed_jobs_today = jobs_qs.filter(
        end_date__date=today, end_date__lte=now
    ).count()
    # Récupère ou crée les préférences utilisateur
    settings, _ = CharacterSettings.objects.get_or_create(
        user=request.user, character_id=0
    )
    jobs_notify_completed = settings.jobs_notify_completed
    allow_copy_requests = settings.allow_copy_requests
    context = {
        "has_blueprint_tokens": bool(blueprint_char_ids),
        "has_jobs_tokens": bool(jobs_char_ids),
        "blueprint_token_count": len(blueprint_char_ids),
        "jobs_token_count": len(jobs_char_ids),
        "characters": user_chars,
        "blueprint_count": blueprint_count,
        "original_blueprints": original_blueprints,
        "copy_blueprints": copy_blueprints,
        "stack_blueprints": stack_blueprints,
        "active_jobs_count": active_jobs_count,
        "completed_jobs_count": completed_jobs_count,
        "completed_jobs_today": completed_jobs_today,
        "jobs_notify_completed": jobs_notify_completed,
        "allow_copy_requests": allow_copy_requests,
    }
    return render(request, "indy_hub/index.html", context)


@indy_hub_access_required
@login_required
def token_management(request):
    blueprint_tokens = None
    jobs_tokens = None
    if Token:
        try:
            blueprint_tokens = Token.objects.filter(user=request.user).require_scopes(
                ["esi-characters.read_blueprints.v1"]
            )
            jobs_tokens = Token.objects.filter(user=request.user).require_scopes(
                ["esi-industry.read_character_jobs.v1"]
            )
            # Deduplicate by character_id
            blueprint_char_ids = (
                list(blueprint_tokens.values_list("character_id", flat=True).distinct())
                if blueprint_tokens
                else []
            )
            jobs_char_ids = (
                list(jobs_tokens.values_list("character_id", flat=True).distinct())
                if jobs_tokens
                else []
            )
        except Exception:
            blueprint_tokens = jobs_tokens = None
            blueprint_char_ids = jobs_char_ids = []
    blueprint_auth_url = (
        reverse("indy_hub:authorize_blueprints") if CallbackRedirect else None
    )
    jobs_auth_url = reverse("indy_hub:authorize_jobs") if CallbackRedirect else None
    user_chars = []
    ownerships = CharacterOwnership.objects.filter(user=request.user)
    for ownership in ownerships:
        cid = ownership.character.character_id
        user_chars.append(
            {
                "character_id": cid,
                "name": get_character_name(cid),
                "bp_enabled": (
                    blueprint_tokens.filter(character_id=cid).exists()
                    if blueprint_tokens
                    else False
                ),
                "jobs_enabled": (
                    jobs_tokens.filter(character_id=cid).exists()
                    if jobs_tokens
                    else False
                ),
            }
        )
    context = {
        "has_blueprint_tokens": bool(blueprint_char_ids),
        "has_jobs_tokens": bool(jobs_char_ids),
        "blueprint_token_count": len(blueprint_char_ids),
        "jobs_token_count": len(jobs_char_ids),
        "blueprint_auth_url": blueprint_auth_url,
        "jobs_auth_url": jobs_auth_url,
        "characters": user_chars,
    }
    return render(request, "indy_hub/token_management.html", context)


@indy_hub_access_required
@login_required
def authorize_blueprints(request):
    # Only skip if ALL characters are already authorized for blueprint scope
    all_chars = CharacterOwnership.objects.filter(user=request.user).values_list(
        "character__character_id", flat=True
    )
    authorized = (
        Token.objects.filter(user=request.user)
        .require_scopes(["esi-characters.read_blueprints.v1"])
        .values_list("character_id", flat=True)
    )
    missing = set(all_chars) - set(authorized)
    if not missing:
        messages.info(request, "All characters already have blueprint access.")
        return redirect("indy_hub:token_management")
    if not CallbackRedirect:
        messages.error(request, "ESI module not available")
        return redirect("indy_hub:token_management")
    try:
        if not request.session.session_key:
            request.session.create()
        CallbackRedirect.objects.filter(
            session_key=request.session.session_key
        ).delete()
        blueprint_state = f"indy_hub_blueprints_{secrets.token_urlsafe(8)}"
        CallbackRedirect.objects.create(
            session_key=request.session.session_key,
            url=reverse("indy_hub:token_management"),
            state=blueprint_state,
        )
        callback_url = getattr(
            settings, "ESI_SSO_CALLBACK_URL", "http://localhost:8000/sso/callback/"
        )
        client_id = getattr(settings, "ESI_SSO_CLIENT_ID", "")
        blueprint_params = {
            "response_type": "code",
            "redirect_uri": callback_url,
            "client_id": client_id,
            "scope": "esi-characters.read_blueprints.v1",
            "state": blueprint_state,
        }
        blueprint_auth_url = f"https://login.eveonline.com/v2/oauth/authorize/?{urlencode(blueprint_params)}"
        return redirect(blueprint_auth_url)
    except Exception as e:
        logger.error(f"Error creating blueprint authorization: {e}")
        messages.error(request, f"Error setting up ESI authorization: {e}")
        return redirect("indy_hub:token_management")


@indy_hub_access_required
@login_required
def authorize_jobs(request):
    # Only skip if ALL characters have jobs access
    all_chars = CharacterOwnership.objects.filter(user=request.user).values_list(
        "character__character_id", flat=True
    )
    authorized = (
        Token.objects.filter(user=request.user)
        .require_scopes(["esi-industry.read_character_jobs.v1"])
        .values_list("character_id", flat=True)
    )
    missing = set(all_chars) - set(authorized)
    if not missing:
        messages.info(request, "All characters already have jobs access.")
        return redirect("indy_hub:token_management")
    if not CallbackRedirect:
        messages.error(request, "ESI module not available")
        return redirect("indy_hub:token_management")
    try:
        if not request.session.session_key:
            request.session.create()
        CallbackRedirect.objects.filter(
            session_key=request.session.session_key
        ).delete()
        jobs_state = f"indy_hub_jobs_{secrets.token_urlsafe(8)}"
        CallbackRedirect.objects.create(
            session_key=request.session.session_key,
            url=reverse("indy_hub:token_management"),
            state=jobs_state,
        )
        callback_url = getattr(
            settings, "ESI_SSO_CALLBACK_URL", "http://localhost:8000/sso/callback/"
        )
        client_id = getattr(settings, "ESI_SSO_CLIENT_ID", "")
        jobs_params = {
            "response_type": "code",
            "redirect_uri": callback_url,
            "client_id": client_id,
            "scope": "esi-industry.read_character_jobs.v1",
            "state": jobs_state,
        }
        jobs_auth_url = (
            f"https://login.eveonline.com/v2/oauth/authorize/?{urlencode(jobs_params)}"
        )
        return redirect(jobs_auth_url)
    except Exception as e:
        logger.error(f"Error creating jobs authorization: {e}")
        messages.error(request, f"Error setting up ESI authorization: {e}")
        return redirect("indy_hub:token_management")


@indy_hub_access_required
@login_required
def authorize_all(request):
    # Only skip if ALL characters have both blueprint and jobs access
    all_chars = CharacterOwnership.objects.filter(user=request.user).values_list(
        "character__character_id", flat=True
    )
    blueprint_auth = (
        Token.objects.filter(user=request.user)
        .require_scopes(["esi-characters.read_blueprints.v1"])
        .values_list("character_id", flat=True)
    )
    jobs_auth = (
        Token.objects.filter(user=request.user)
        .require_scopes(["esi-industry.read_character_jobs.v1"])
        .values_list("character_id", flat=True)
    )
    missing = set(all_chars) - (set(blueprint_auth) & set(jobs_auth))
    if not missing:
        messages.info(request, "All characters already authorized for all scopes.")
        return redirect("indy_hub:token_management")
    if not CallbackRedirect:
        messages.error(request, "ESI module not available")
        return redirect("indy_hub:token_management")
    try:
        if not request.session.session_key:
            request.session.create()
        CallbackRedirect.objects.filter(
            session_key=request.session.session_key
        ).delete()
        state = f"indy_hub_all_{secrets.token_urlsafe(8)}"
        CallbackRedirect.objects.create(
            session_key=request.session.session_key,
            url=reverse("indy_hub:token_management"),
            state=state,
        )
        callback_url = getattr(
            settings, "ESI_SSO_CALLBACK_URL", "http://localhost:8000/sso/callback/"
        )
        client_id = getattr(settings, "ESI_SSO_CLIENT_ID", "")
        params = {
            "response_type": "code",
            "redirect_uri": callback_url,
            "client_id": client_id,
            "scope": "esi-characters.read_blueprints.v1 esi-industry.read_character_jobs.v1",
            "state": state,
        }
        auth_url = (
            f"https://login.eveonline.com/v2/oauth/authorize/?{urlencode(params)}"
        )
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"Error creating combined authorization: {e}")
        messages.error(request, f"Error setting up ESI authorization: {e}")
        return redirect("indy_hub:token_management")


@indy_hub_access_required
@login_required
def sync_all_tokens(request):
    if Token:
        try:
            blueprint_tokens = Token.objects.filter(user=request.user).require_scopes(
                ["esi-characters.read_blueprints.v1"]
            )
            jobs_tokens = Token.objects.filter(user=request.user).require_scopes(
                ["esi-industry.read_character_jobs.v1"]
            )
            if blueprint_tokens.exists():
                update_blueprints_for_user.delay(request.user.id)
            if jobs_tokens.exists():
                update_industry_jobs_for_user.delay(request.user.id)
            messages.success(request, "Synchronization started for all characters.")
        except Exception as e:
            logger.error(f"Error triggering sync_all: {e}")
            messages.error(request, "Error starting synchronization.")
    else:
        messages.error(request, "ESI module not available.")
    return redirect("indy_hub:token_management")


@indy_hub_access_required
@login_required
def sync_blueprints(request):
    if Token:
        try:
            blueprint_tokens = Token.objects.filter(user=request.user).require_scopes(
                ["esi-characters.read_blueprints.v1"]
            )
            if blueprint_tokens.exists():
                update_blueprints_for_user.delay(request.user.id)
                messages.success(request, "Blueprint synchronization started.")
            else:
                messages.warning(
                    request, "No blueprint tokens available for synchronization."
                )
        except Exception as e:
            logger.error(f"Error triggering sync_blueprints: {e}")
            messages.error(request, "Error starting blueprint synchronization.")
    else:
        messages.error(request, "ESI module not available.")
    return redirect("indy_hub:token_management")


@indy_hub_access_required
@login_required
def sync_jobs(request):
    if Token:
        try:
            jobs_tokens = Token.objects.filter(user=request.user).require_scopes(
                ["esi-industry.read_character_jobs.v1"]
            )
            if jobs_tokens.exists():
                update_industry_jobs_for_user.delay(request.user.id)
                messages.success(request, "Jobs synchronization started.")
            else:
                messages.warning(
                    request, "No jobs tokens available for synchronization."
                )
        except Exception as e:
            logger.error(f"Error triggering sync_jobs: {e}")
            messages.error(request, "Error starting jobs synchronization.")
    else:
        messages.error(request, "ESI module not available.")
    return redirect("indy_hub:token_management")


# Toggle notification des travaux
@indy_hub_access_required
@login_required
@require_POST
def toggle_job_notifications(request):
    # Basculer la préférence de notification
    settings, _ = CharacterSettings.objects.get_or_create(
        user=request.user, character_id=0
    )
    settings.jobs_notify_completed = not settings.jobs_notify_completed
    settings.save(update_fields=["jobs_notify_completed"])
    return JsonResponse({"enabled": settings.jobs_notify_completed})


# Toggle pooling de partage de copies
@indy_hub_access_required
@login_required
@require_POST
def toggle_copy_sharing(request):
    settings, _ = CharacterSettings.objects.get_or_create(
        user=request.user, character_id=0
    )
    settings.allow_copy_requests = not settings.allow_copy_requests
    settings.save(update_fields=["allow_copy_requests"])
    return JsonResponse({"enabled": settings.allow_copy_requests})


# --- Production Simulations Management ---
@indy_hub_access_required
@login_required
def production_simulations(request):
    """
    Page de gestion des simulations de production sauvegardées.
    """
    simulations = ProductionSimulation.objects.filter(user=request.user)

    context = {
        "simulations": simulations,
        "total_simulations": simulations.count(),
    }

    return render(request, "indy_hub/production_simulations.html", context)


@indy_hub_access_required
@login_required
@require_POST
def delete_production_simulation(request, simulation_id):
    """
    Supprimer une simulation de production.
    """
    simulation = get_object_or_404(
        ProductionSimulation, id=simulation_id, user=request.user
    )

    # Supprimer aussi toutes les configurations associées
    ProductionConfig.objects.filter(
        user=request.user,
        blueprint_type_id=simulation.blueprint_type_id,
        runs=simulation.runs,
    ).delete()

    simulation_name = simulation.display_name
    simulation.delete()

    messages.success(request, f'Simulation "{simulation_name}" supprimée avec succès.')
    return redirect("indy_hub:production_simulations")


@indy_hub_access_required
@login_required
def rename_production_simulation(request, simulation_id):
    """
    Renommer une simulation de production.
    """
    simulation = get_object_or_404(
        ProductionSimulation, id=simulation_id, user=request.user
    )

    if request.method == "POST":
        new_name = request.POST.get("simulation_name", "").strip()
        simulation.simulation_name = new_name
        simulation.save(update_fields=["simulation_name"])

        messages.success(
            request, f'Simulation renommée en "{simulation.display_name}".'
        )
        return redirect("indy_hub:production_simulations")

    context = {
        "simulation": simulation,
    }

    return render(request, "indy_hub/rename_simulation.html", context)
