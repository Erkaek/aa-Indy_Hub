# Django
from django.test import TestCase

# AA Example App
from indy_hub.models import (
    CachedCorporationAsset,
    CachedCorporationDivision,
    CachedStructureName,
)
from indy_hub.services.asset_cache import (
    asset_chain_has_context,
    build_asset_index_by_item_id,
    get_office_folder_item_id_from_assets,
    make_managed_hangar_location_id,
    resolve_asset_root_location_id,
    resolve_structure_names,
)
from indy_hub.services.esi_client import ESIForbiddenError


class TestMaterialExchangeLocations(TestCase):
    def test_container_asset_resolves_to_parent_structure(self):
        # Item A is inside container C, which is inside structure B.
        item_a = {
            "item_id": 1040503713425,
            "location_id": 1045691258254,
            "location_flag": "Unlocked",
            "type_id": 34,
            "quantity": 10,
            "is_singleton": False,
            "is_blueprint": False,
        }
        container_c = {
            "item_id": 1045691258254,
            "location_id": 1045667241057,
            "location_flag": "Hangar",
            "type_id": 23,
            "quantity": 1,
            "is_singleton": True,
            "is_blueprint": False,
        }

        assets = [item_a, container_c]
        index_by_item_id = build_asset_index_by_item_id(assets)

        assert resolve_asset_root_location_id(item_a, index_by_item_id) == 1045667241057

    def test_container_asset_inherits_location_context_from_parent(self):
        # Container C carries the real hub hangar context; item A should match it.
        effective_location_id = 1045722708748
        target_flag = "CorpSAG7"

        item_a = {
            "item_id": 1040503713425,
            "location_id": 5555550001,  # container item id
            "location_flag": "Unlocked",
            "type_id": 34,
            "quantity": 10,
            "is_singleton": False,
            "is_blueprint": False,
        }
        container_c = {
            "item_id": 5555550001,
            "location_id": effective_location_id,
            "location_flag": target_flag,
            "type_id": 23,
            "quantity": 1,
            "is_singleton": True,
            "is_blueprint": False,
        }

        assets = [item_a, container_c]
        index_by_item_id = build_asset_index_by_item_id(assets)

        assert asset_chain_has_context(
            item_a,
            index_by_item_id,
            location_id=effective_location_id,
            location_flag=target_flag,
        )

    def test_office_folder_item_id_extraction(self):
        corp_assets = [
            {
                "item_id": 1045722708748,
                "location_id": 1045667241057,
                "location_flag": "OfficeFolder",
                "type_id": 27,
                "quantity": 1,
                "is_singleton": True,
                "is_blueprint": False,
            },
            {
                "item_id": 999,
                "location_id": 1045722708748,
                "location_flag": "CorpSAG7",
                "type_id": 34,
                "quantity": 10,
                "is_singleton": False,
                "is_blueprint": False,
            },
        ]

        assert (
            get_office_folder_item_id_from_assets(
                corp_assets, structure_id=1045667241057
            )
            == 1045722708748
        )

    def test_managed_hangar_location_id(self):
        assert make_managed_hangar_location_id(1045722708748, 7) == -10457227087487

    def test_structure_name_cache_not_overwritten_on_403(self):
        # AA Example App
        from indy_hub.services import asset_cache

        structure_id = 1045667241057

        # First call: 200 OK, cache is populated
        def _ok(structure_id_in, character_id_in):
            assert int(structure_id_in) == structure_id
            assert int(character_id_in) == 1
            return "C-N4OD - Fountain of Life"

        # Second call: 403 Forbidden, must NOT overwrite existing cached name
        def _forbidden(structure_id_in, character_id_in):
            raise ESIForbiddenError(
                "Structure lookup forbidden",
                character_id=int(character_id_in),
                structure_id=int(structure_id_in),
            )

        original = asset_cache.shared_client.fetch_structure_name
        try:
            asset_cache.shared_client.fetch_structure_name = _ok
            names = resolve_structure_names([structure_id], character_id=1)
            assert names[structure_id] == "C-N4OD - Fountain of Life"

            asset_cache.shared_client.fetch_structure_name = _forbidden
            names2 = resolve_structure_names([structure_id], character_id=1)
            assert names2[structure_id] == "C-N4OD - Fountain of Life"

            cached = CachedStructureName.objects.get(structure_id=structure_id)
            assert cached.name == "C-N4OD - Fountain of Life"
        finally:
            asset_cache.shared_client.fetch_structure_name = original

    def test_resolve_managed_hangar_name_from_cache(self):
        corp_id = 123
        structure_id = 1045667241057
        office_folder_item_id = 1045722708748
        division = 7
        managed_id = make_managed_hangar_location_id(office_folder_item_id, division)

        CachedCorporationDivision.objects.create(
            corporation_id=corp_id,
            division=division,
            name="Division 7",
        )
        CachedStructureName.objects.create(
            structure_id=structure_id,
            name="C-N4OD - Fountain of Life",
        )
        CachedCorporationAsset.objects.create(
            corporation_id=corp_id,
            item_id=office_folder_item_id,
            location_id=structure_id,
            location_flag="OfficeFolder",
            type_id=27,
            quantity=1,
            is_singleton=True,
            is_blueprint=False,
        )

        names = resolve_structure_names([managed_id], corporation_id=corp_id)
        assert names[managed_id] == "C-N4OD - Fountain of Life > Division 7"
