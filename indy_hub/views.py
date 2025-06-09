from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.contrib import messages
from django.urls import reverse
from .decorators import blueprints_token_required, industry_jobs_token_required
from .models import Blueprint, IndustryJob, CharacterUpdateTracker, get_type_name, get_character_name
from .tasks import update_blueprints_for_user, update_industry_jobs_for_user
import logging
import secrets
from urllib.parse import urlencode
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db.models import Q

logger = logging.getLogger(__name__)

# Import ESI Token only when needed to avoid import issues
try:
    from esi.models import Token, CallbackRedirect
except ImportError:
    Token = None
    CallbackRedirect = None

@login_required
def index(request):
    """
    Home page for Indy Hub module.
    """
    # Check token status for dashboard
    blueprint_tokens = None
    jobs_tokens = None
    if Token:
        try:
            blueprint_tokens = Token.objects.filter(user=request.user).require_scopes(['esi-characters.read_blueprints.v1'])
            jobs_tokens = Token.objects.filter(user=request.user).require_scopes(['esi-industry.read_character_jobs.v1'])
            # Schedule background sync via Celery tasks
            if blueprint_tokens.exists():
                update_blueprints_for_user.delay(request.user.id)
            if jobs_tokens.exists():
                update_industry_jobs_for_user.delay(request.user.id)
        except Exception:
            pass
    # Calculate dashboard statistics
    blueprint_count = Blueprint.objects.filter(owner_user=request.user).count()
    original_blueprints = Blueprint.objects.filter(owner_user=request.user, quantity=-1).count()
    copy_blueprints = Blueprint.objects.filter(owner_user=request.user, quantity=-2).count()
    stack_blueprints = Blueprint.objects.filter(owner_user=request.user, quantity__gt=0).count()
    active_jobs = IndustryJob.objects.filter(
        owner_user=request.user,
        status__in=['active', 'paused', 'ready']
    )
    active_jobs_count = active_jobs.count()
    from django.utils import timezone
    today = timezone.now().date()
    completed_jobs_today = IndustryJob.objects.filter(
        owner_user=request.user,
        status='delivered',
        end_date__date=today
    ).count()
    context = {
        'has_blueprint_tokens': blueprint_tokens.exists() if blueprint_tokens else False,
        'has_jobs_tokens': jobs_tokens.exists() if jobs_tokens else False,
        'blueprint_token_count': blueprint_tokens.count() if blueprint_tokens else 0,
        'jobs_token_count': jobs_tokens.count() if jobs_tokens else 0,
        'blueprint_count': blueprint_count,
        'original_blueprints': original_blueprints,
        'copy_blueprints': copy_blueprints,
        'stack_blueprints': stack_blueprints,
        'active_jobs_count': active_jobs_count,
        'completed_jobs_today': completed_jobs_today,
    }
    return render(request, 'indy_hub/index.html', context)

@login_required
def token_management(request):
    """
    Token management page with authorization buttons.
    """
    blueprint_tokens = None
    jobs_tokens = None
    if Token:
        try:
            blueprint_tokens = Token.objects.filter(user=request.user).require_scopes(['esi-characters.read_blueprints.v1'])
            jobs_tokens = Token.objects.filter(user=request.user).require_scopes(['esi-industry.read_character_jobs.v1'])
        except Exception:
            pass
    # Create authorization URLs
    blueprint_auth_url = reverse('indy_hub:authorize_blueprints') if CallbackRedirect else None
    jobs_auth_url = reverse('indy_hub:authorize_jobs') if CallbackRedirect else None
    context = {
        'has_blueprint_tokens': blueprint_tokens.exists() if blueprint_tokens else False,
        'has_jobs_tokens': jobs_tokens.exists() if jobs_tokens else False,
        'blueprint_token_count': blueprint_tokens.count() if blueprint_tokens else 0,
        'jobs_token_count': jobs_tokens.count() if jobs_tokens else 0,
        'blueprint_auth_url': blueprint_auth_url,
        'jobs_auth_url': jobs_auth_url,
    }
    return render(request, 'indy_hub/token_management.html', context)


@blueprints_token_required
def blueprints_list(request):
    """
    Display user's blueprints from database with pagination and filtering.
    """
    try:
        # Check if we need to sync data
        force_update = request.GET.get('refresh') == '1'
        
        # Trigger background sync if user requested refresh
        if force_update:
            logger.info(f"User {request.user.username} requested blueprint refresh; enqueuing Celery task")
            from django.utils import timezone
            CharacterUpdateTracker.objects.filter(user=request.user).update(last_refresh_request=timezone.now())
            from .tasks import update_blueprints_for_user
            update_blueprints_for_user.delay(request.user.id)
        
        # Get filter parameters
        search = request.GET.get('search', '')
        efficiency_filter = request.GET.get('efficiency', '')
        type_filter = request.GET.get('type', '')
        character_filter = request.GET.get('character', '')
        sort_by = request.GET.get('sort', 'type_name')
        sort_order = request.GET.get('order', 'asc')
        page = int(request.GET.get('page', 1))
        # Items per page (default 50) for pagination
        per_page = int(request.GET.get('per_page', 50))
        
        # Get blueprints from database
        blueprints_qs = Blueprint.objects.filter(owner_user=request.user)
        
        # Apply filters
        if search:
            blueprints_qs = blueprints_qs.filter(
                Q(type_name__icontains=search) | Q(type_id__icontains=search)
            )
        
        if efficiency_filter == 'perfect':
            blueprints_qs = blueprints_qs.filter(material_efficiency__gte=10, time_efficiency__gte=20)
        elif efficiency_filter == 'researched':
            blueprints_qs = blueprints_qs.filter(
                Q(material_efficiency__gt=0) | Q(time_efficiency__gt=0)
            )
        elif efficiency_filter == 'unresearched':
            blueprints_qs = blueprints_qs.filter(material_efficiency=0, time_efficiency=0)
        
        if type_filter == 'original':
            blueprints_qs = blueprints_qs.filter(quantity=-1)
        elif type_filter == 'copy':
            blueprints_qs = blueprints_qs.filter(quantity=-2)
        elif type_filter == 'stack':
            blueprints_qs = blueprints_qs.filter(quantity__gt=0)
        
        if character_filter:
            blueprints_qs = blueprints_qs.filter(character_id=character_filter)
        
        # Apply sorting (always alphabetical by type_name asc)
        blueprints_qs = blueprints_qs.order_by('type_name')
        
        # Pagination
        paginator = Paginator(blueprints_qs, per_page)
        blueprints_page = paginator.get_page(page)
        
        # Get statistics
        total_blueprints = blueprints_qs.count()
        originals_count = blueprints_qs.filter(quantity=-1).count()
        copies_count = blueprints_qs.filter(quantity=-2).count()
        stacks_count = blueprints_qs.filter(quantity__gt=0).count()
        statistics = {
            'total': total_blueprints,
            'originals': originals_count,
            'copies': copies_count,
            'stacks': stacks_count,
        }
        
        # Get character list for filter
        character_ids = Blueprint.objects.filter(owner_user=request.user).values_list('character_id', flat=True).distinct()
        character_map = {cid: get_character_name(cid) for cid in character_ids}

        # Check if data needs updating
        update_status = CharacterUpdateTracker.objects.filter(user=request.user).first()
        
        context = {
            'blueprints': blueprints_page,
            'statistics': {
                'total_count': total_blueprints,
                'original_count': originals_count,
                'copy_count': copies_count,
                'stack_blueprints': stacks_count,
                'perfect_me_count': blueprints_qs.filter(material_efficiency__gte=10).count(),
                'perfect_te_count': blueprints_qs.filter(time_efficiency__gte=20).count(),
                'character_count': len(character_ids),
                'character_ids': character_ids,
            },
            'character_ids': character_ids,
            'character_map': character_map,
            'current_filters': {
                'search': search,
                'efficiency': efficiency_filter,
                'type': type_filter,
                'character': character_filter,
                'sort': request.GET.get('sort', 'type_name'),
                'order': sort_order,
                'per_page': per_page,
            },
            'per_page_options': [10, 25, 50, 100, 200],
            'update_status': update_status,
        }
        return render(request, 'indy_hub/blueprints_list.html', context)
    except Exception as e:
        logger.error(f"Error displaying blueprints: {e}")
        messages.error(request, f'Error displaying blueprints: {e}')
        return redirect('indy_hub:index')


@industry_jobs_token_required
def jobs_list(request):
    """
    Display user's industry jobs from database with pagination and filtering.
    """
    try:
        # Check if we need to sync data
        force_update = request.GET.get('refresh') == '1'
        
        # Trigger background sync if user requested refresh
        if force_update:
            logger.info(f"User {request.user.username} requested jobs refresh; enqueuing Celery task")
            from django.utils import timezone
            CharacterUpdateTracker.objects.filter(user=request.user).update(last_refresh_request=timezone.now())
            from .tasks import update_industry_jobs_for_user
            update_industry_jobs_for_user.delay(request.user.id)
        
        # Get filter parameters
        search = request.GET.get('search', '')
        status_filter = request.GET.get('status', '')
        activity_filter = request.GET.get('activity', '')
        character_filter = request.GET.get('character', '')
        sort_by = request.GET.get('sort', 'start_date')
        sort_order = request.GET.get('order', 'desc')
        page = int(request.GET.get('page', 1))
        per_page = request.GET.get('per_page')
        if per_page:
            per_page = int(per_page)
        else:
            per_page = IndustryJob.objects.filter(owner_user=request.user).count()

        # Get jobs from database
        jobs_qs = IndustryJob.objects.filter(owner_user=request.user)

        # Prepare character_map for search (id -> name)
        all_character_ids = list(jobs_qs.values_list('character_id', flat=True).distinct())
        character_map = {cid: get_character_name(cid) for cid in all_character_ids} if all_character_ids else {}

        # Apply filters
        if search:
            # Try to match job_id, blueprint_type_name, product_type_name, activity_name, character name
            job_id_q = Q(job_id__icontains=search) if search.isdigit() else Q()
            char_name_ids = [cid for cid, name in character_map.items() if name and search.lower() in name.lower()]
            char_name_q = Q(character_id__in=char_name_ids) if char_name_ids else Q()
            jobs_qs = jobs_qs.filter(
                Q(blueprint_type_name__icontains=search) |
                Q(product_type_name__icontains=search) |
                Q(activity_name__icontains=search) |
                job_id_q |
                char_name_q
            )

        # --- DEBUG: Log filter values ---
        logger.debug(f"[JOBS FILTER] search='{search}' status='{status_filter}' activity='{activity_filter}' character='{character_filter}'")

        if status_filter:
            jobs_qs = jobs_qs.filter(status=status_filter.strip())

        if activity_filter:
            try:
                activity_filter_int = int(activity_filter.strip())
                jobs_qs = jobs_qs.filter(activity_id=activity_filter_int)
            except (ValueError, TypeError):
                logger.warning(f"[JOBS FILTER] Invalid activity_filter value: '{activity_filter}'")
                pass

        if character_filter:
            try:
                character_filter_int = int(character_filter.strip())
                jobs_qs = jobs_qs.filter(character_id=character_filter_int)
            except (ValueError, TypeError):
                logger.warning(f"[JOBS FILTER] Invalid character_filter value: '{character_filter}'")
                pass

        # Apply sorting
        if sort_order == 'desc':
            sort_by = f'-{sort_by}'
        jobs_qs = jobs_qs.order_by(sort_by)
        
        # Pagination
        paginator = Paginator(jobs_qs, per_page)
        jobs_page = paginator.get_page(page)
        
        # Get statistics
        total_jobs = jobs_qs.count()
        active_jobs = jobs_qs.filter(status='active').count()
        # Un job est "completed" s'il est livré (delivered), prêt (ready), ou terminé (end_date passée)
        from django.utils import timezone
        now = timezone.now()
        completed_jobs = jobs_qs.filter(
            (
                Q(status__in=['delivered', 'ready']) |
                (Q(end_date__isnull=False) & Q(end_date__lte=now))
            )
        ).count()
        statistics = {
            'total': total_jobs,
            'active': active_jobs,
            'completed': completed_jobs,
        }
        
        # Get character list for filter
        character_ids = IndustryJob.objects.filter(owner_user=request.user).values_list('character_id', flat=True).distinct()

        # Prepare distinct status list for filter (from all jobs for user, not filtered qs)
        statuses = IndustryJob.objects.filter(owner_user=request.user).values_list('status', flat=True).distinct()

        # Check if data needs updating
        update_status = CharacterUpdateTracker.objects.filter(user=request.user).first()
        
        # Build character_map for dropdown (id -> name)
        character_map = {cid: get_character_name(cid) for cid in character_ids}

        # Build character_map for jobs in table (id -> name)
        if jobs_page:
            job_character_ids = [job.character_id for job in jobs_page if job.character_id]
            if job_character_ids:
                character_map = {cid: get_character_name(cid) for cid in job_character_ids}
        
        context = {
            'jobs': jobs_page,
            'statistics': statistics,
            'character_ids': character_ids,
            'statuses': statuses,
            'activities': [
                ('1', 'Manufacturing'),
                ('3', 'TE Research'),
                ('4', 'ME Research'),
                ('5', 'Copying'),
                ('8', 'Invention'),
                ('9', 'Reaction'),
            ],
            'current_filters': {
                'search': search,
                'status': status_filter,
                'activity': activity_filter,
                'character': character_filter,
                'sort': request.GET.get('sort', 'start_date'),
                'order': sort_order,
                'per_page': per_page,
            },
            'update_status': update_status,
            'character_map': character_map,
        }
        return render(request, 'indy_hub/jobs_list.html', context)
    except Exception as e:
        logger.error(f"Error displaying industry jobs: {e}")
        messages.error(request, f'Error displaying industry jobs: {e}')
        return redirect('indy_hub:index')


@login_required
def authorize_blueprints(request):
    """
    Redirect to ESI authorization for blueprints scope.
    """
    if not CallbackRedirect:
        messages.error(request, 'ESI module not available')
        return redirect('indy_hub:token_management')
    
    try:
        # Ensure session exists
        if not request.session.session_key:
            request.session.create()
        
        # Clean up any existing CallbackRedirect objects for this session
        CallbackRedirect.objects.filter(session_key=request.session.session_key).delete()
        
        # Create state and CallbackRedirect object
        blueprint_state = f"indy_hub_blueprints_{secrets.token_urlsafe(8)}"
        CallbackRedirect.objects.create(
            session_key=request.session.session_key,
            url=reverse('indy_hub:token_management'),
            state=blueprint_state
        )
        
        # Create ESI authorization URL
        callback_url = getattr(settings, 'ESI_SSO_CALLBACK_URL', 'http://localhost:8000/sso/callback')
        client_id = getattr(settings, 'ESI_SSO_CLIENT_ID', '')
        
        blueprint_params = {
            'response_type': 'code',
            'redirect_uri': callback_url,
            'client_id': client_id,
            'scope': 'esi-characters.read_blueprints.v1',
            'state': blueprint_state
        }
        blueprint_auth_url = f"https://login.eveonline.com/v2/oauth/authorize/?{urlencode(blueprint_params)}"
        
        return redirect(blueprint_auth_url)
        
    except Exception as e:
        logger.error(f"Error creating blueprint authorization: {e}")
        messages.error(request, f'Error setting up ESI authorization: {e}')
        return redirect('indy_hub:token_management')


@login_required  
def authorize_jobs(request):
    """
    Redirect to ESI authorization for industry jobs scope.
    """
    if not CallbackRedirect:
        messages.error(request, 'ESI module not available')
        return redirect('indy_hub:token_management')
    
    try:
        # Ensure session exists
        if not request.session.session_key:
            request.session.create()
        
        # Clean up any existing CallbackRedirect objects for this session
        CallbackRedirect.objects.filter(session_key=request.session.session_key).delete()
        
        # Create state and CallbackRedirect object
        jobs_state = f"indy_hub_jobs_{secrets.token_urlsafe(8)}"
        CallbackRedirect.objects.create(
            session_key=request.session.session_key,
            url=reverse('indy_hub:token_management'),
            state=jobs_state
        )
        
        # Create ESI authorization URL
        callback_url = getattr(settings, 'ESI_SSO_CALLBACK_URL', 'http://localhost:8000/sso/callback')
        client_id = getattr(settings, 'ESI_SSO_CLIENT_ID', '')
        
        jobs_params = {
            'response_type': 'code',
            'redirect_uri': callback_url,
            'client_id': client_id,
            'scope': 'esi-industry.read_character_jobs.v1',
            'state': jobs_state
        }
        jobs_auth_url = f"https://login.eveonline.com/v2/oauth/authorize/?{urlencode(jobs_params)}"
        
        return redirect(jobs_auth_url)
        
    except Exception as e:
        logger.error(f"Error creating jobs authorization: {e}")
        messages.error(request, f'Error setting up ESI authorization: {e}')
        return redirect('indy_hub:token_management')
