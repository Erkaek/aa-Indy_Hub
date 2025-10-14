# Django
from django.urls import path

from .views.api import (
    fuzzwork_price,
    load_production_config,
    save_production_config,
)
from .views.industry import (
    all_bp_list,
    bp_accept_copy_request,
    bp_buyer_accept_offer,
    bp_cancel_copy_request,
    bp_cond_copy_request,
    bp_copy_fulfill_requests,
    bp_copy_my_requests,
    bp_copy_request_page,
    bp_mark_copy_delivered,
    bp_offer_copy_request,
    bp_reject_copy_request,
    bp_update_copy_request,
    craft_bp,
)
from .views.industry import (
    delete_production_simulation as delete_production_simulation_view,
)
from .views.industry import (
    edit_simulation_name,
    personnal_bp_list,
    personnal_job_list,
    production_simulations_list,
)
from .views.user import (
    authorize_all,
    authorize_blueprints,
    authorize_jobs,
    index,
    onboarding_set_visibility,
    onboarding_toggle_task,
    production_simulations,
    rename_production_simulation,
    sync_all_tokens,
    sync_blueprints,
    sync_jobs,
    toggle_copy_sharing,
    toggle_job_notifications,
    token_management,
)

app_name = "indy_hub"
urlpatterns = [
    path("", index, name="index"),
    path("personnal-bp/", personnal_bp_list, name="personnal_bp_list"),
    path("all-bp/", all_bp_list, name="all_bp_list"),
    path("personnal-jobs/", personnal_job_list, name="personnal_job_list"),
    path("tokens/", token_management, name="token_management"),
    path("tokens/sync-blueprints/", sync_blueprints, name="sync_blueprints"),
    path("tokens/sync-jobs/", sync_jobs, name="sync_jobs"),
    path("tokens/sync-all/", sync_all_tokens, name="sync_all_tokens"),
    path("authorize/blueprints/", authorize_blueprints, name="authorize_blueprints"),
    path("authorize/jobs/", authorize_jobs, name="authorize_jobs"),
    path("authorize/all/", authorize_all, name="authorize_all"),
    path("craft/<int:type_id>/", craft_bp, name="craft_bp"),
    path("api/fuzzwork-price/", fuzzwork_price, name="fuzzwork_price"),
    path(
        "api/production-config/save/",
        save_production_config,
        name="save_production_config",
    ),
    path(
        "api/production-config/load/",
        load_production_config,
        name="load_production_config",
    ),
    path(
        "simulations/", production_simulations_list, name="production_simulations_list"
    ),
    path(
        "simulations/<int:simulation_id>/delete/",
        delete_production_simulation_view,
        name="delete_production_simulation",
    ),
    path(
        "simulations/<int:simulation_id>/edit-name/",
        edit_simulation_name,
        name="edit_simulation_name",
    ),
    path(
        "simulations/legacy/",
        production_simulations,
        name="production_simulations",
    ),
    path(
        "simulations/<int:simulation_id>/rename/",
        rename_production_simulation,
        name="rename_production_simulation",
    ),
    path("bp-copy/request/", bp_copy_request_page, name="bp_copy_request_page"),
    path("bp-copy/fulfill/", bp_copy_fulfill_requests, name="bp_copy_fulfill_requests"),
    path(
        "bp-copy/my-requests/", bp_copy_my_requests, name="bp_copy_my_requests"
    ),  # my requests
    path(
        "bp-copy/my-requests/<int:request_id>/update/",
        bp_update_copy_request,
        name="bp_update_copy_request",
    ),
    path(
        "bp-copy/offer/<int:request_id>/",
        bp_offer_copy_request,
        name="bp_offer_copy_request",
    ),
    path(
        "bp-copy/accept-offer/<int:offer_id>/",
        bp_buyer_accept_offer,
        name="bp_buyer_accept_offer",
    ),
    path(
        "bp-copy/accept/<int:request_id>/",
        bp_accept_copy_request,
        name="bp_accept_copy_request",
    ),
    path(
        "bp-copy/condition/<int:request_id>/",
        bp_cond_copy_request,
        name="bp_cond_copy_request",
    ),
    path(
        "bp-copy/reject/<int:request_id>/",
        bp_reject_copy_request,
        name="bp_reject_copy_request",
    ),
    path(
        "bp-copy/cancel/<int:request_id>/",
        bp_cancel_copy_request,
        name="bp_cancel_copy_request",
    ),
    path(
        "bp-copy/delivered/<int:request_id>/",
        bp_mark_copy_delivered,
        name="bp_mark_copy_delivered",
    ),
    path(
        "toggle-job-notifications/",
        toggle_job_notifications,
        name="toggle_job_notifications",
    ),
    path("toggle-copy-sharing/", toggle_copy_sharing, name="toggle_copy_sharing"),
    path(
        "onboarding/toggle-task/",
        onboarding_toggle_task,
        name="onboarding_toggle_task",
    ),
    path(
        "onboarding/visibility/",
        onboarding_set_visibility,
        name="onboarding_set_visibility",
    ),
]
