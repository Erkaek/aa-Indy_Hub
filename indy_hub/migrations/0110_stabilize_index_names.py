# Django
from django.db import migrations


def _table_constraints(schema_editor, table_name: str) -> set[str]:
    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(
            cursor, table_name
        )
    return set(constraints.keys())


def _rename_index_if_needed(
    schema_editor,
    *,
    table_name: str,
    old_name: str,
    new_name: str,
) -> None:
    connection = schema_editor.connection
    existing = _table_constraints(schema_editor, table_name)
    if new_name in existing or old_name not in existing:
        return

    quoted_table = schema_editor.quote_name(table_name)
    quoted_old = schema_editor.quote_name(old_name)
    quoted_new = schema_editor.quote_name(new_name)
    vendor = connection.vendor

    if vendor == "mysql":
        schema_editor.execute(
            f"ALTER TABLE {quoted_table} RENAME INDEX {quoted_old} TO {quoted_new}"
        )
        return

    if vendor == "postgresql":
        schema_editor.execute(f"ALTER INDEX {quoted_old} RENAME TO {quoted_new}")
        return

    # SQLite (and backends without native rename-index support): drop and recreate.
    with connection.cursor() as cursor:
        constraint_map = connection.introspection.get_constraints(cursor, table_name)
    old_info = constraint_map.get(old_name, {})
    columns = old_info.get("columns") or []
    unique = bool(old_info.get("unique"))
    if not columns:
        return

    quoted_cols = ", ".join(schema_editor.quote_name(column) for column in columns)
    unique_sql = "UNIQUE " if unique else ""
    schema_editor.execute(f"DROP INDEX {quoted_old}")
    schema_editor.execute(
        f"CREATE {unique_sql}INDEX {quoted_new} ON {quoted_table} ({quoted_cols})"
    )


def _forwards(apps, schema_editor):
    # Each tuple is: (table_name, new_index_name, candidate_old_names).
    rename_specs = [
        (
            "indy_hub_industrystructure",
            "indy_hub_in_struct_type_idx",
            ["indy_hub_in_structu_490efb_idx"],
        ),
        (
            "indy_hub_industrystructure",
            "indy_hub_in_owner_sync_idx",
            ["indy_hub_in_owner_c_2a0df3_idx"],
        ),
        (
            "indy_hub_industrystructure",
            "indy_hub_in_external_idx",
            ["indy_hub_in_externa_65b7c7_idx"],
        ),
        (
            "indy_hub_industrystructure",
            "indy_hub_in_constel_idx",
            ["indy_hub_in_constel_22f76f_idx", "indy_hub_in_constel_30b149_idx"],
        ),
        (
            "indy_hub_industrystructure",
            "indy_hub_in_region_idx",
            ["indy_hub_in_region__8e4ef4_idx", "indy_hub_in_region__db733f_idx"],
        ),
        (
            "indy_hub_industrystructure",
            "indy_hub_in_vis_owner_idx",
            ["indy_hub_in_visibili_215887_idx", "indy_hub_in_visibil_c32ce1_idx"],
        ),
        (
            "indy_hub_industrystructure",
            "indy_hub_in_source_idx",
            ["indy_hub_in_source__2f4ab3_idx", "indy_hub_in_source__b435e9_idx"],
        ),
        (
            "indy_hub_esicontract",
            "esi_ct_corp_state_type_idx",
            ["indy_hub_es_corpora_e83e14_idx"],
        ),
        (
            "indy_hub_esicontract",
            "esi_ct_issuer_state_idx",
            ["indy_hub_es_issuer__abacd7_idx"],
        ),
        (
            "indy_hub_esicontract",
            "esi_ct_acceptor_type_idx",
            ["indy_hub_es_accepto_e51feb_idx"],
        ),
        (
            "indy_hub_esicontract",
            "esi_ct_issued_desc_idx",
            ["indy_hub_es_date_is_a0f169_idx"],
        ),
        (
            "indy_hub_esicontractitem",
            "esi_ct_item_contract_type_idx",
            ["indy_hub_es_contrac_c6f466_idx"],
        ),
        (
            "indy_hub_esicontractitem",
            "esi_ct_item_type_included_idx",
            ["indy_hub_es_type_id_6fcbb3_idx"],
        ),
        (
            "indy_hub_industrystructurerig",
            "ind_struct_rig_slot_idx",
            ["indy_hub_in_structu_6d7958_idx"],
        ),
        (
            "indy_hub_industrystructurerig",
            "ind_struct_rig_type_idx",
            ["indy_hub_in_rig_typ_7b4661_idx"],
        ),
        (
            "indy_hub_industrysystemcostindex",
            "ind_sci_system_activity_idx",
            ["indy_hub_in_solar_s_042d6c_idx"],
        ),
        (
            "indy_hub_industrysystemcostindex",
            "ind_sci_name_activity_idx",
            ["indy_hub_in_solar_s_044129_idx"],
        ),
        (
            "indy_hub_materialexchangebuyorder",
            "mes_buy_state_created_idx",
            ["indy_hub_ma_status_c3e4f4_idx"],
        ),
        (
            "indy_hub_materialexchangebuyorder",
            "mes_buy_buyer_created_idx",
            ["indy_hub_ma_buyer_i_6e10d2_idx"],
        ),
        (
            "indy_hub_materialexchangebuyorder",
            "mes_buy_contract_idx",
            ["indy_hub_ma_esi_con_f75c4b_idx"],
        ),
        (
            "indy_hub_materialexchangebuyorderitem",
            "mes_buy_item_type_idx",
            ["indy_hub_ma_type_id_5e57fb_idx"],
        ),
        (
            "indy_hub_materialexchangebuyorderitem",
            "mes_buy_item_order_idx",
            ["indy_hub_ma_order_i_5ccb01_idx"],
        ),
        (
            "indy_hub_materialexchangesellorder",
            "mes_sell_state_created_idx",
            ["indy_hub_ma_status_cfa62a_idx"],
        ),
        (
            "indy_hub_materialexchangesellorder",
            "mes_sell_seller_created_idx",
            ["indy_hub_ma_seller__c5b878_idx"],
        ),
        (
            "indy_hub_materialexchangesellorder",
            "mes_sell_contract_idx",
            ["indy_hub_ma_esi_con_05a3e3_idx"],
        ),
        (
            "indy_hub_materialexchangesellorderitem",
            "mes_sell_item_type_idx",
            ["indy_hub_ma_type_id_9b6b82_idx"],
        ),
        (
            "indy_hub_materialexchangesellorderitem",
            "mes_sell_item_order_idx",
            ["indy_hub_ma_order_i_158d3b_idx"],
        ),
        (
            "indy_hub_materialexchangestock",
            "mes_type_idx",
            ["indy_hub_ma_type_id_7b1dab_idx"],
        ),
        (
            "indy_hub_materialexchangestock",
            "mes_cfg_type_idx",
            ["indy_hub_ma_config__05780a_idx"],
        ),
        (
            "indy_hub_materialexchangetransaction",
            "mes_tx_type_completed_idx",
            ["indy_hub_ma_transac_1e3571_idx"],
        ),
        (
            "indy_hub_materialexchangetransaction",
            "mes_tx_user_completed_idx",
            ["indy_hub_ma_user_id_c1d3f5_idx"],
        ),
        (
            "indy_hub_materialexchangetransaction",
            "mes_tx_item_completed_idx",
            ["indy_hub_ma_type_id_731c3d_idx"],
        ),
        (
            "indy_hub_productionproject",
            "ind_prj_user_state_upd_idx",
            ["indy_hub_pr_user_id_a57633_idx"],
        ),
        (
            "indy_hub_productionproject",
            "ind_prj_user_source_upd_idx",
            ["indy_hub_pr_user_id_a7a32a_idx"],
        ),
        (
            "indy_hub_productionprojectitem",
            "ind_prj_item_proj_sel_idx",
            ["indy_hub_pr_project_a2d58a_idx"],
        ),
        (
            "indy_hub_productionprojectitem",
            "ind_prj_item_proj_mode_idx",
            ["indy_hub_pr_project_97f750_idx"],
        ),
        (
            "indy_hub_productionprojectitem",
            "ind_prj_item_proj_type_idx",
            ["indy_hub_pr_project_f01f60_idx"],
        ),
    ]

    for table_name, new_name, candidates in rename_specs:
        for old_name in candidates:
            _rename_index_if_needed(
                schema_editor,
                table_name=table_name,
                old_name=old_name,
                new_name=new_name,
            )


def _backwards(apps, schema_editor):
    # Reverse DB names back to the migration-state names expected before 0110.
    reverse_specs = [
        (
            "indy_hub_industrystructure",
            "indy_hub_in_struct_type_idx",
            "indy_hub_in_structu_490efb_idx",
        ),
        (
            "indy_hub_industrystructure",
            "indy_hub_in_owner_sync_idx",
            "indy_hub_in_owner_c_2a0df3_idx",
        ),
        (
            "indy_hub_industrystructure",
            "indy_hub_in_external_idx",
            "indy_hub_in_externa_65b7c7_idx",
        ),
        (
            "indy_hub_industrystructure",
            "indy_hub_in_constel_idx",
            "indy_hub_in_constel_22f76f_idx",
        ),
        (
            "indy_hub_industrystructure",
            "indy_hub_in_region_idx",
            "indy_hub_in_region__8e4ef4_idx",
        ),
        (
            "indy_hub_industrystructure",
            "indy_hub_in_vis_owner_idx",
            "indy_hub_in_visibili_215887_idx",
        ),
        (
            "indy_hub_industrystructure",
            "indy_hub_in_source_idx",
            "indy_hub_in_source__2f4ab3_idx",
        ),
        (
            "indy_hub_esicontract",
            "esi_ct_corp_state_type_idx",
            "indy_hub_es_corpora_e83e14_idx",
        ),
        (
            "indy_hub_esicontract",
            "esi_ct_issuer_state_idx",
            "indy_hub_es_issuer__abacd7_idx",
        ),
        (
            "indy_hub_esicontract",
            "esi_ct_acceptor_type_idx",
            "indy_hub_es_accepto_e51feb_idx",
        ),
        (
            "indy_hub_esicontract",
            "esi_ct_issued_desc_idx",
            "indy_hub_es_date_is_a0f169_idx",
        ),
        (
            "indy_hub_esicontractitem",
            "esi_ct_item_contract_type_idx",
            "indy_hub_es_contrac_c6f466_idx",
        ),
        (
            "indy_hub_esicontractitem",
            "esi_ct_item_type_included_idx",
            "indy_hub_es_type_id_6fcbb3_idx",
        ),
        (
            "indy_hub_industrystructurerig",
            "ind_struct_rig_slot_idx",
            "indy_hub_in_structu_6d7958_idx",
        ),
        (
            "indy_hub_industrystructurerig",
            "ind_struct_rig_type_idx",
            "indy_hub_in_rig_typ_7b4661_idx",
        ),
        (
            "indy_hub_industrysystemcostindex",
            "ind_sci_system_activity_idx",
            "indy_hub_in_solar_s_042d6c_idx",
        ),
        (
            "indy_hub_industrysystemcostindex",
            "ind_sci_name_activity_idx",
            "indy_hub_in_solar_s_044129_idx",
        ),
        (
            "indy_hub_materialexchangebuyorder",
            "mes_buy_state_created_idx",
            "indy_hub_ma_status_c3e4f4_idx",
        ),
        (
            "indy_hub_materialexchangebuyorder",
            "mes_buy_buyer_created_idx",
            "indy_hub_ma_buyer_i_6e10d2_idx",
        ),
        (
            "indy_hub_materialexchangebuyorder",
            "mes_buy_contract_idx",
            "indy_hub_ma_esi_con_f75c4b_idx",
        ),
        (
            "indy_hub_materialexchangebuyorderitem",
            "mes_buy_item_type_idx",
            "indy_hub_ma_type_id_5e57fb_idx",
        ),
        (
            "indy_hub_materialexchangebuyorderitem",
            "mes_buy_item_order_idx",
            "indy_hub_ma_order_i_5ccb01_idx",
        ),
        (
            "indy_hub_materialexchangesellorder",
            "mes_sell_state_created_idx",
            "indy_hub_ma_status_cfa62a_idx",
        ),
        (
            "indy_hub_materialexchangesellorder",
            "mes_sell_seller_created_idx",
            "indy_hub_ma_seller__c5b878_idx",
        ),
        (
            "indy_hub_materialexchangesellorder",
            "mes_sell_contract_idx",
            "indy_hub_ma_esi_con_05a3e3_idx",
        ),
        (
            "indy_hub_materialexchangesellorderitem",
            "mes_sell_item_type_idx",
            "indy_hub_ma_type_id_9b6b82_idx",
        ),
        (
            "indy_hub_materialexchangesellorderitem",
            "mes_sell_item_order_idx",
            "indy_hub_ma_order_i_158d3b_idx",
        ),
        (
            "indy_hub_materialexchangestock",
            "mes_type_idx",
            "indy_hub_ma_type_id_7b1dab_idx",
        ),
        (
            "indy_hub_materialexchangestock",
            "mes_cfg_type_idx",
            "indy_hub_ma_config__05780a_idx",
        ),
        (
            "indy_hub_materialexchangetransaction",
            "mes_tx_type_completed_idx",
            "indy_hub_ma_transac_1e3571_idx",
        ),
        (
            "indy_hub_materialexchangetransaction",
            "mes_tx_user_completed_idx",
            "indy_hub_ma_user_id_c1d3f5_idx",
        ),
        (
            "indy_hub_materialexchangetransaction",
            "mes_tx_item_completed_idx",
            "indy_hub_ma_type_id_731c3d_idx",
        ),
        (
            "indy_hub_productionproject",
            "ind_prj_user_state_upd_idx",
            "indy_hub_pr_user_id_a57633_idx",
        ),
        (
            "indy_hub_productionproject",
            "ind_prj_user_source_upd_idx",
            "indy_hub_pr_user_id_a7a32a_idx",
        ),
        (
            "indy_hub_productionprojectitem",
            "ind_prj_item_proj_sel_idx",
            "indy_hub_pr_project_a2d58a_idx",
        ),
        (
            "indy_hub_productionprojectitem",
            "ind_prj_item_proj_mode_idx",
            "indy_hub_pr_project_97f750_idx",
        ),
        (
            "indy_hub_productionprojectitem",
            "ind_prj_item_proj_type_idx",
            "indy_hub_pr_project_f01f60_idx",
        ),
    ]

    for table_name, current_name, previous_name in reverse_specs:
        _rename_index_if_needed(
            schema_editor,
            table_name=table_name,
            old_name=current_name,
            new_name=previous_name,
        )


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("indy_hub", "0109_optimize_query_indexes"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(_forwards, _backwards),
            ],
            state_operations=[
                migrations.RenameIndex(
                    model_name="industrystructure",
                    old_name="indy_hub_in_structu_490efb_idx",
                    new_name="indy_hub_in_struct_type_idx",
                ),
                migrations.RenameIndex(
                    model_name="industrystructure",
                    old_name="indy_hub_in_owner_c_2a0df3_idx",
                    new_name="indy_hub_in_owner_sync_idx",
                ),
                migrations.RenameIndex(
                    model_name="industrystructure",
                    old_name="indy_hub_in_externa_65b7c7_idx",
                    new_name="indy_hub_in_external_idx",
                ),
                migrations.RenameIndex(
                    model_name="industrystructure",
                    old_name="indy_hub_in_constel_22f76f_idx",
                    new_name="indy_hub_in_constel_idx",
                ),
                migrations.RenameIndex(
                    model_name="industrystructure",
                    old_name="indy_hub_in_region__8e4ef4_idx",
                    new_name="indy_hub_in_region_idx",
                ),
                migrations.RenameIndex(
                    model_name="industrystructure",
                    old_name="indy_hub_in_visibili_215887_idx",
                    new_name="indy_hub_in_vis_owner_idx",
                ),
                migrations.RenameIndex(
                    model_name="industrystructure",
                    old_name="indy_hub_in_source__2f4ab3_idx",
                    new_name="indy_hub_in_source_idx",
                ),
                migrations.RenameIndex(
                    model_name="esicontract",
                    old_name="indy_hub_es_corpora_e83e14_idx",
                    new_name="esi_ct_corp_state_type_idx",
                ),
                migrations.RenameIndex(
                    model_name="esicontract",
                    old_name="indy_hub_es_issuer__abacd7_idx",
                    new_name="esi_ct_issuer_state_idx",
                ),
                migrations.RenameIndex(
                    model_name="esicontract",
                    old_name="indy_hub_es_accepto_e51feb_idx",
                    new_name="esi_ct_acceptor_type_idx",
                ),
                migrations.RenameIndex(
                    model_name="esicontract",
                    old_name="indy_hub_es_date_is_a0f169_idx",
                    new_name="esi_ct_issued_desc_idx",
                ),
                migrations.RenameIndex(
                    model_name="esicontractitem",
                    old_name="indy_hub_es_contrac_c6f466_idx",
                    new_name="esi_ct_item_contract_type_idx",
                ),
                migrations.RenameIndex(
                    model_name="esicontractitem",
                    old_name="indy_hub_es_type_id_6fcbb3_idx",
                    new_name="esi_ct_item_type_included_idx",
                ),
                migrations.RenameIndex(
                    model_name="industrystructurerig",
                    old_name="indy_hub_in_structu_6d7958_idx",
                    new_name="ind_struct_rig_slot_idx",
                ),
                migrations.RenameIndex(
                    model_name="industrystructurerig",
                    old_name="indy_hub_in_rig_typ_7b4661_idx",
                    new_name="ind_struct_rig_type_idx",
                ),
                migrations.RenameIndex(
                    model_name="industrysystemcostindex",
                    old_name="indy_hub_in_solar_s_042d6c_idx",
                    new_name="ind_sci_system_activity_idx",
                ),
                migrations.RenameIndex(
                    model_name="industrysystemcostindex",
                    old_name="indy_hub_in_solar_s_044129_idx",
                    new_name="ind_sci_name_activity_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangebuyorder",
                    old_name="indy_hub_ma_status_c3e4f4_idx",
                    new_name="mes_buy_state_created_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangebuyorder",
                    old_name="indy_hub_ma_buyer_i_6e10d2_idx",
                    new_name="mes_buy_buyer_created_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangebuyorder",
                    old_name="indy_hub_ma_esi_con_f75c4b_idx",
                    new_name="mes_buy_contract_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangebuyorderitem",
                    old_name="indy_hub_ma_type_id_5e57fb_idx",
                    new_name="mes_buy_item_type_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangebuyorderitem",
                    old_name="indy_hub_ma_order_i_5ccb01_idx",
                    new_name="mes_buy_item_order_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangesellorder",
                    old_name="indy_hub_ma_status_cfa62a_idx",
                    new_name="mes_sell_state_created_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangesellorder",
                    old_name="indy_hub_ma_seller__c5b878_idx",
                    new_name="mes_sell_seller_created_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangesellorder",
                    old_name="indy_hub_ma_esi_con_05a3e3_idx",
                    new_name="mes_sell_contract_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangesellorderitem",
                    old_name="indy_hub_ma_type_id_9b6b82_idx",
                    new_name="mes_sell_item_type_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangesellorderitem",
                    old_name="indy_hub_ma_order_i_158d3b_idx",
                    new_name="mes_sell_item_order_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangestock",
                    old_name="indy_hub_ma_type_id_7b1dab_idx",
                    new_name="mes_type_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangestock",
                    old_name="indy_hub_ma_config__05780a_idx",
                    new_name="mes_cfg_type_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangetransaction",
                    old_name="indy_hub_ma_transac_1e3571_idx",
                    new_name="mes_tx_type_completed_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangetransaction",
                    old_name="indy_hub_ma_user_id_c1d3f5_idx",
                    new_name="mes_tx_user_completed_idx",
                ),
                migrations.RenameIndex(
                    model_name="materialexchangetransaction",
                    old_name="indy_hub_ma_type_id_731c3d_idx",
                    new_name="mes_tx_item_completed_idx",
                ),
                migrations.RenameIndex(
                    model_name="productionproject",
                    old_name="indy_hub_pr_user_id_a57633_idx",
                    new_name="ind_prj_user_state_upd_idx",
                ),
                migrations.RenameIndex(
                    model_name="productionproject",
                    old_name="indy_hub_pr_user_id_a7a32a_idx",
                    new_name="ind_prj_user_source_upd_idx",
                ),
                migrations.RenameIndex(
                    model_name="productionprojectitem",
                    old_name="indy_hub_pr_project_a2d58a_idx",
                    new_name="ind_prj_item_proj_sel_idx",
                ),
                migrations.RenameIndex(
                    model_name="productionprojectitem",
                    old_name="indy_hub_pr_project_97f750_idx",
                    new_name="ind_prj_item_proj_mode_idx",
                ),
                migrations.RenameIndex(
                    model_name="productionprojectitem",
                    old_name="indy_hub_pr_project_f01f60_idx",
                    new_name="ind_prj_item_proj_type_idx",
                ),
            ],
        ),
    ]
