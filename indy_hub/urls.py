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
    bp_chat_decide,
    bp_chat_history,
    bp_chat_send,
    bp_cond_copy_request,
    bp_copy_fulfill_requests,
    bp_copy_my_requests,
    bp_copy_request_page,
    bp_discord_action,
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
    personnal_job_notification_test,
    preview_job_notification_digest,
    preview_job_notification_live,
    production_simulations_list,
)
from .views.material_exchange import (
    material_exchange_approve_buy,
    material_exchange_approve_sell,
    material_exchange_assign_contract,
    material_exchange_buy,
    material_exchange_complete_buy,
    material_exchange_complete_sell,
    material_exchange_index,
    material_exchange_mark_delivered_buy,
    material_exchange_reject_buy,
    material_exchange_reject_sell,
    material_exchange_sell,
    material_exchange_sell_assets_refresh_status,
    material_exchange_sync_prices,
    material_exchange_sync_stock,
    material_exchange_transactions,
    material_exchange_verify_payment_sell,
)
from .views.material_exchange_config import (
    material_exchange_config,
    material_exchange_debug_tokens,
    material_exchange_get_structures,
    material_exchange_request_all_scopes,
    material_exchange_request_assets_token,
    material_exchange_request_contracts_scope,
    material_exchange_request_divisions_token,
)
from .views.material_exchange_orders import (
    buy_order_delete,
    buy_order_detail,
    my_orders,
    sell_order_delete,
    sell_order_detail,
)
from .views.user import (
    authorize_all,
    authorize_assets,
    authorize_blueprints,
    authorize_corp_all,
    authorize_corp_blueprints,
    authorize_corp_jobs,
    authorize_jobs,
    authorize_material_exchange,
    corporation_dashboard,
    index,
    onboarding_set_visibility,
    onboarding_toggle_task,
    production_simulations,
    rename_production_simulation,
    sync_all_tokens,
    sync_blueprints,
    sync_jobs,
    toggle_copy_sharing,
    toggle_corporation_copy_sharing,
    toggle_job_notifications,
    token_management,
)

app_name = "indy_hub"
urlpatterns = [
    path("", index, name="index"),
    path("corporation/", corporation_dashboard, name="corporation_dashboard"),
    path("personnal-bp/", personnal_bp_list, name="personnal_bp_list"),
    path(
        "corporation-bp/",
        personnal_bp_list,
        {"scope": "corporation"},
        name="corporation_bp_list",
    ),
    path("all-bp/", all_bp_list, name="all_bp_list"),
    path("personnal-jobs/", personnal_job_list, name="personnal_job_list"),
    path(
        "personnal-jobs/notification_test/",
        personnal_job_notification_test,
        name="personnal_job_notification_test",
    ),
    path(
        "personnal-jobs/notifications/preview/live/",
        preview_job_notification_live,
        name="job_notification_preview_live",
    ),
    path(
        "personnal-jobs/notifications/preview/digest/",
        preview_job_notification_digest,
        name="job_notification_preview_digest",
    ),
    path(
        "corporation-jobs/",
        personnal_job_list,
        {"scope": "corporation"},
        name="corporation_job_list",
    ),
    path("tokens/", token_management, name="token_management"),
    path("tokens/sync-blueprints/", sync_blueprints, name="sync_blueprints"),
    path("tokens/sync-jobs/", sync_jobs, name="sync_jobs"),
    path("tokens/sync-all/", sync_all_tokens, name="sync_all_tokens"),
    path("authorize/blueprints/", authorize_blueprints, name="authorize_blueprints"),
    path("authorize/jobs/", authorize_jobs, name="authorize_jobs"),
    path("authorize/assets/", authorize_assets, name="authorize_assets"),
    path("authorize/all/", authorize_all, name="authorize_all"),
    path(
        "authorize/corporation/blueprints/",
        authorize_corp_blueprints,
        name="authorize_corp_blueprints",
    ),
    path(
        "authorize/corporation/jobs/",
        authorize_corp_jobs,
        name="authorize_corp_jobs",
    ),
    path("authorize/corporation/all/", authorize_corp_all, name="authorize_corp_all"),
    path(
        "authorize/material-exchange/",
        authorize_material_exchange,
        name="authorize_material_exchange",
    ),
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
    path("bp-copy/action/", bp_discord_action, name="bp_discord_action"),
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
        "bp-copy/chat/<int:chat_id>/",
        bp_chat_history,
        name="bp_chat_history",
    ),
    path(
        "bp-copy/chat/<int:chat_id>/send/",
        bp_chat_send,
        name="bp_chat_send",
    ),
    path(
        "bp-copy/chat/<int:chat_id>/decision/",
        bp_chat_decide,
        name="bp_chat_decide",
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
    path(
        "toggle-corporation-copy-sharing/",
        toggle_corporation_copy_sharing,
        name="toggle_corporation_copy_sharing",
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
    # Material Exchange
    path(
        "material-exchange/",
        material_exchange_index,
        name="material_exchange_index",
    ),
    path(
        "material-exchange/config/",
        material_exchange_config,
        name="material_exchange_config",
    ),
    path(
        "material-exchange/config/request-assets-token/",
        material_exchange_request_assets_token,
        name="material_exchange_request_assets_token",
    ),
    path(
        "material-exchange/config/request-divisions-token/",
        material_exchange_request_divisions_token,
        name="material_exchange_request_divisions_token",
    ),
    path(
        "material-exchange/config/request-contracts-scope/",
        material_exchange_request_contracts_scope,
        name="material_exchange_request_contracts_scope",
    ),
    path(
        "material-exchange/config/request-all-scopes/",
        material_exchange_request_all_scopes,
        name="material_exchange_request_all_scopes",
    ),
    path(
        "material-exchange/api/structures/<int:corp_id>/",
        material_exchange_get_structures,
        name="material_exchange_get_structures",
    ),
    path(
        "material-exchange/api/debug-tokens/<int:corp_id>/",
        material_exchange_debug_tokens,
        name="material_exchange_debug_tokens",
    ),
    path(
        "material-exchange/sell/",
        material_exchange_sell,
        name="material_exchange_sell",
    ),
    path(
        "material-exchange/api/sell-assets-refresh-status/",
        material_exchange_sell_assets_refresh_status,
        name="material_exchange_sell_assets_refresh_status",
    ),
    path(
        "material-exchange/buy/",
        material_exchange_buy,
        name="material_exchange_buy",
    ),
    # User Order Management
    path(
        "material-exchange/my-orders/",
        my_orders,
        name="my_orders",
    ),
    path(
        "material-exchange/my-orders/sell/<int:order_id>/",
        sell_order_detail,
        name="sell_order_detail",
    ),
    path(
        "material-exchange/my-orders/sell/<int:order_id>/delete/",
        sell_order_delete,
        name="sell_order_delete",
    ),
    path(
        "material-exchange/my-orders/buy/<int:order_id>/",
        buy_order_detail,
        name="buy_order_detail",
    ),
    path(
        "material-exchange/my-orders/buy/<int:order_id>/delete/",
        buy_order_delete,
        name="buy_order_delete",
    ),
    # Stock & Prices
    path(
        "material-exchange/sync-stock/",
        material_exchange_sync_stock,
        name="material_exchange_sync_stock",
    ),
    path(
        "material-exchange/sync-prices/",
        material_exchange_sync_prices,
        name="material_exchange_sync_prices",
    ),
    path(
        "material-exchange/transactions/",
        material_exchange_transactions,
        name="material_exchange_transactions",
    ),
    path(
        "material-exchange/sell/<int:order_id>/approve/",
        material_exchange_approve_sell,
        name="material_exchange_approve_sell",
    ),
    path(
        "material-exchange/sell/<int:order_id>/reject/",
        material_exchange_reject_sell,
        name="material_exchange_reject_sell",
    ),
    path(
        "material-exchange/sell/<int:order_id>/verify-payment/",
        material_exchange_verify_payment_sell,
        name="material_exchange_verify_payment_sell",
    ),
    path(
        "material-exchange/sell/<int:order_id>/complete/",
        material_exchange_complete_sell,
        name="material_exchange_complete_sell",
    ),
    path(
        "material-exchange/buy/<int:order_id>/approve/",
        material_exchange_approve_buy,
        name="material_exchange_approve_buy",
    ),
    path(
        "material-exchange/buy/<int:order_id>/reject/",
        material_exchange_reject_buy,
        name="material_exchange_reject_buy",
    ),
    path(
        "material-exchange/buy/<int:order_id>/delivered/",
        material_exchange_mark_delivered_buy,
        name="material_exchange_mark_delivered_buy",
    ),
    path(
        "material-exchange/buy/<int:order_id>/complete/",
        material_exchange_complete_buy,
        name="material_exchange_complete_buy",
    ),
    path(
        "material-exchange/order/<int:order_id>/assign-contract/",
        material_exchange_assign_contract,
        name="material_exchange_assign_contract",
    ),
]
