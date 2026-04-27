"""Tests for production project import parsing and resolution."""

# Standard Library
from unittest import TestCase
from unittest.mock import patch

# AA Example App
from indy_hub.models import generate_production_project_ref
from indy_hub.services.production_projects import (
    _build_project_blueprint_configs_grouped,
    _payload_uses_unpublished_blueprints,
    _resolve_blueprints_for_products,
    _resolve_preferred_blueprint_for_product,
    aggregate_project_import_entries,
    build_project_import_preview,
    normalize_production_project_ref,
    parse_project_import_text,
    resolve_project_import_entries,
)


class _PreviewCursorStub:
    def __init__(self):
        self._result = []
        self.last_query = ""
        self.last_params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        query = " ".join(str(sql).split())
        self.last_query = query
        self.last_params = params
        if "FROM eve_sde_itemtype" in query:
            self._result = [
                (1001, "Vedmak", "Cruiser"),
                (1002, "Thermal Armor Hardener II", "Armor Hardener"),
                (1003, "Warp Disruptor II", "Warp Disruptor"),
            ]
            return
        if "SELECT p.product_eve_type_id, p.eve_type_id, p.activity_id" in query:
            self._result = [
                (1001, 5001, 1),
                (1002, 5002, 1),
            ]
            return
        self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


class _FetchoneCursorStub:
    def __init__(self, fetchone_result=None):
        self._fetchone_result = fetchone_result
        self.last_query = ""
        self.last_params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.last_query = " ".join(str(sql).split())
        self.last_params = params

    def fetchone(self):
        return self._fetchone_result


class ProductionProjectImportTests(TestCase):
    @patch(
        "indy_hub.models.secrets.choice",
        side_effect=list("a1b2c3d4e5"),
    )
    def test_generate_project_ref_returns_random_base36_token(
        self, mocked_choice
    ) -> None:
        project_ref = generate_production_project_ref()

        self.assertEqual(project_ref, "a1b2c3d4e5")
        self.assertEqual(len(project_ref), 10)
        self.assertEqual(mocked_choice.call_count, 10)

    def test_project_ref_rejects_invalid_tokens(self) -> None:
        with self.assertRaises(ValueError):
            normalize_production_project_ref("123")

        with self.assertRaises(ValueError):
            normalize_production_project_ref("00000000**")

    def test_project_ref_normalize_lowercases_valid_tokens(self) -> None:
        self.assertEqual(
            normalize_production_project_ref("ABC123DEF4"),
            "abc123def4",
        )

    @patch("indy_hub.services.production_projects.connection.cursor")
    def test_resolve_preferred_blueprint_for_product_prefers_published_types(
        self, mock_cursor
    ) -> None:
        cursor = _FetchoneCursorStub(fetchone_result=(46207,))
        mock_cursor.return_value = cursor

        resolved = _resolve_preferred_blueprint_for_product(16672)

        self.assertEqual(resolved, 46207)
        self.assertEqual(cursor.last_params, [16672])
        self.assertIn(
            "JOIN eve_sde_itemtype t ON t.id = p.eve_type_id", cursor.last_query
        )
        self.assertIn("COALESCE(t.published, 0) = 1", cursor.last_query)
        self.assertIn("COALESCE(product_t.published, 0) = 1", cursor.last_query)

    @patch(
        "indy_hub.services.production_projects.connection.cursor",
        return_value=_PreviewCursorStub(),
    )
    def test_resolve_blueprints_for_products_skips_unpublished_blueprints(
        self, _mock_cursor
    ) -> None:
        resolved = _resolve_blueprints_for_products([1001, 1002, 1003])

        self.assertEqual(resolved, {1001: 5001, 1002: 5002})

    @patch("indy_hub.services.production_projects.connection.cursor")
    def test_payload_uses_unpublished_blueprints_detects_cached_test_blueprints(
        self, mock_cursor
    ) -> None:
        cursor = _FetchoneCursorStub(fetchone_result=(45732,))
        mock_cursor.return_value = cursor

        payload = {
            "materials_tree": [
                {
                    "type_id": 16672,
                    "blueprint_type_id": 45732,
                    "sub_materials": [],
                }
            ]
        }

        self.assertTrue(_payload_uses_unpublished_blueprints(payload))
        self.assertEqual(cursor.last_params, [45732])
        self.assertIn("COALESCE(published, 0) = 0", cursor.last_query)

    def test_parse_eft_import_keeps_categories_and_quantities(self) -> None:
        payload = parse_project_import_text(
            """
[Vedmak, PVE - Vedmak Stronghold]

Thermal Armor Hardener II
Thermal Armor Hardener II

Warp Disruptor II x2
""".strip()
        )

        self.assertEqual(payload["source_kind"], "eft")
        self.assertEqual(payload["source_name"], "Vedmak // PVE - Vedmak Stronghold")
        self.assertEqual(payload["entries"][0]["type_name"], "Vedmak")
        self.assertEqual(payload["entries"][0]["category_key"], "hull")
        self.assertEqual(payload["entries"][1]["category_key"], "low_slots")
        self.assertEqual(payload["entries"][3]["category_key"], "mid_slots")
        self.assertEqual(payload["entries"][3]["quantity"], 2)

    def test_parse_eft_import_supports_brackets_in_fit_name(self) -> None:
        payload = parse_project_import_text(
            """
[Retribution,   [PRIME] SD 01]

Heat Sink II
""".strip()
        )

        self.assertEqual(payload["source_kind"], "eft")
        self.assertEqual(payload["source_name"], "Retribution // [PRIME] SD 01")
        self.assertEqual(payload["entries"][0]["type_name"], "Retribution")
        self.assertEqual(payload["entries"][0]["category_key"], "hull")

    def test_parse_manual_import_supports_prefix_and_suffix_quantities(self) -> None:
        payload = parse_project_import_text(
            """
3x Nanite Repair Paste
Vedmak x2
Auto Targeting System I
""".strip(),
            preferred_kind="manual",
        )

        self.assertEqual(payload["source_kind"], "manual")
        self.assertEqual(
            [entry["quantity"] for entry in payload["entries"]],
            [3, 2, 1],
        )
        self.assertTrue(
            all(entry["category_key"] == "manual" for entry in payload["entries"])
        )

    def test_aggregate_import_entries_merges_duplicates(self) -> None:
        aggregated = aggregate_project_import_entries(
            [
                {
                    "type_name": "Thermal Armor Hardener II",
                    "quantity": 1,
                    "category_key": "low_slots",
                    "category_label": "Low slots",
                    "category_order": 10,
                    "source_line": "Thermal Armor Hardener II",
                },
                {
                    "type_name": "Thermal Armor Hardener II",
                    "quantity": 2,
                    "category_key": "low_slots",
                    "category_label": "Low slots",
                    "category_order": 10,
                    "source_line": "Thermal Armor Hardener II x2",
                },
            ]
        )

        self.assertEqual(len(aggregated), 1)
        self.assertEqual(aggregated[0]["quantity"], 3)
        self.assertEqual(len(aggregated[0]["source_lines"]), 2)

    @patch(
        "indy_hub.services.production_projects.connection.cursor",
        return_value=_PreviewCursorStub(),
    )
    def test_resolve_entries_marks_craftable_and_non_craftable(
        self, _mock_cursor
    ) -> None:
        resolved = resolve_project_import_entries(
            [
                {
                    "type_name": "Vedmak",
                    "quantity": 1,
                    "category_key": "hull",
                    "category_label": "Hull",
                    "category_order": 0,
                    "source_line": "Vedmak",
                },
                {
                    "type_name": "Warp Disruptor II",
                    "quantity": 1,
                    "category_key": "mid_slots",
                    "category_label": "Mid slots",
                    "category_order": 20,
                    "source_line": "Warp Disruptor II",
                },
                {
                    "type_name": "Unknown Module",
                    "quantity": 1,
                    "category_key": "mid_slots",
                    "category_label": "Mid slots",
                    "category_order": 20,
                    "source_line": "Unknown Module",
                },
            ]
        )

        by_name = {entry["type_name"]: entry for entry in resolved}
        self.assertTrue(by_name["Vedmak"]["is_craftable"])
        self.assertEqual(by_name["Vedmak"]["blueprint_type_id"], 5001)
        self.assertFalse(by_name["Warp Disruptor II"]["is_craftable"])
        self.assertEqual(
            by_name["Warp Disruptor II"]["not_craftable_reason"], "no_blueprint"
        )
        self.assertFalse(by_name["Unknown Module"]["resolved"])
        self.assertEqual(
            by_name["Unknown Module"]["not_craftable_reason"], "unknown_item"
        )

    @patch(
        "indy_hub.services.production_projects.connection.cursor",
        return_value=_PreviewCursorStub(),
    )
    def test_build_preview_returns_grouped_summary(self, _mock_cursor) -> None:
        preview = build_project_import_preview(
            """
[Vedmak, Fleet]

Thermal Armor Hardener II
Warp Disruptor II
""".strip()
        )

        self.assertEqual(preview["summary"]["total_unique_items"], 3)
        self.assertEqual(preview["summary"]["craftable_items"], 2)
        self.assertEqual(preview["summary"]["non_craftable_items"], 1)
        self.assertEqual(preview["groups"][0]["key"], "hull")

    @patch("indy_hub.services.production_projects._resolve_blueprints_for_products")
    @patch("indy_hub.services.production_projects._resolve_item_types_by_name")
    def test_build_preview_reclassifies_drone_and_cargo_sections_without_subsystems(
        self,
        mock_resolve_items,
        mock_resolve_blueprints,
    ) -> None:
        mock_resolve_items.return_value = {
            "vedmak": {"type_id": 1, "type_name": "Vedmak", "group_name": "Cruiser"},
            "federation navy hammerhead": {
                "type_id": 2,
                "type_name": "Federation Navy Hammerhead",
                "group_name": "Medium Drone",
            },
            "tracking speed script": {
                "type_id": 3,
                "type_name": "Tracking Speed Script",
                "group_name": "Tracking Script",
            },
            "optimal range script": {
                "type_id": 4,
                "type_name": "Optimal Range Script",
                "group_name": "Tracking Script",
            },
            "tetryon exotic plasma m": {
                "type_id": 5,
                "type_name": "Tetryon Exotic Plasma M",
                "group_name": "Exotic Plasma Charge",
            },
            "meson exotic plasma m": {
                "type_id": 6,
                "type_name": "Meson Exotic Plasma M",
                "group_name": "Exotic Plasma Charge",
            },
            "warp disruptor ii": {
                "type_id": 7,
                "type_name": "Warp Disruptor II",
                "group_name": "Warp Scrambler",
            },
        }
        mock_resolve_blueprints.return_value = {
            1: 101,
            2: 102,
            3: 103,
            4: 104,
            5: 105,
            6: 106,
            7: 107,
        }

        preview = build_project_import_preview(
            """
[Vedmak,   PVE - Vedmak Stronghold ]

Thermal Armor Hardener II
EM Armor Hardener II
Entropic Radiation Sink II
Entropic Radiation Sink II
Entropic Radiation Sink II
Reactive Armor Hardener

F-12 Enduring Tracking Computer
Large Compact Pb-Acid Cap Battery
Large Compact Pb-Acid Cap Battery
10MN Afterburner II

Heavy Compact Entropic Disintegrator
Medium Coaxial Compact Remote Armor Repairer
Medium Coaxial Compact Remote Armor Repairer
Auto Targeting System I

Medium Ancillary Current Router I
Medium Capacitor Control Circuit I
Medium Polycarbon Engine Housing I




Federation Navy Hammerhead x7


Tracking Speed Script x1
Optimal Range Script x1
Tetryon Exotic Plasma M x10000
Meson Exotic Plasma M x2000
Warp Disruptor II x1
""".strip()
        )

        groups = {group["key"]: group for group in preview["groups"]}
        self.assertIn("drone_bay", groups)
        self.assertIn("cargo", groups)

        drone_items = {item["type_name"] for item in groups["drone_bay"]["items"]}
        cargo_items = {item["type_name"] for item in groups["cargo"]["items"]}

        self.assertEqual(drone_items, {"Federation Navy Hammerhead"})
        self.assertSetEqual(
            cargo_items,
            {
                "Tracking Speed Script",
                "Optimal Range Script",
                "Tetryon Exotic Plasma M",
                "Meson Exotic Plasma M",
                "Warp Disruptor II",
            },
        )

    @patch("indy_hub.services.production_projects._resolve_blueprints_for_products")
    @patch("indy_hub.services.production_projects._resolve_item_types_by_name")
    def test_build_preview_keeps_t3_subsystems_and_cargo_separate(
        self,
        mock_resolve_items,
        mock_resolve_blueprints,
    ) -> None:
        mock_resolve_items.return_value = {
            "legion": {
                "type_id": 10,
                "type_name": "Legion",
                "group_name": "Strategic Cruiser",
            },
            "legion defensive - augmented plating": {
                "type_id": 11,
                "type_name": "Legion Defensive - Augmented Plating",
                "group_name": "Defensive Systems",
            },
            "legion core - energy parasitic complex": {
                "type_id": 12,
                "type_name": "Legion Core - Energy Parasitic Complex",
                "group_name": "Core Systems",
            },
            "legion offensive - liquid crystal magnifiers": {
                "type_id": 13,
                "type_name": "Legion Offensive - Liquid Crystal Magnifiers",
                "group_name": "Offensive Systems",
            },
            "legion propulsion - intercalated nanofibers": {
                "type_id": 14,
                "type_name": "Legion Propulsion - Intercalated Nanofibers",
                "group_name": "Propulsion Systems",
            },
            "nanite repair paste": {
                "type_id": 15,
                "type_name": "Nanite Repair Paste",
                "group_name": "Nanite Repair Paste",
            },
            "scorch m": {
                "type_id": 16,
                "type_name": "Scorch M",
                "group_name": "Frequency Crystal",
            },
        }
        mock_resolve_blueprints.return_value = {
            10: 110,
            11: 111,
            12: 112,
            13: 113,
            14: 114,
            15: 115,
            16: 116,
        }

        preview = build_project_import_preview(
            """
[Legion, T3 test]

Thermal Armor Hardener II

50MN Microwarpdrive II

Heavy Pulse Laser II

Medium Auxiliary Nano Pump I

Legion Defensive - Augmented Plating
Legion Core - Energy Parasitic Complex
Legion Offensive - Liquid Crystal Magnifiers
Legion Propulsion - Intercalated Nanofibers

Nanite Repair Paste x200
Scorch M x1000
""".strip()
        )

        groups = {group["key"]: group for group in preview["groups"]}
        self.assertIn("subsystems", groups)
        self.assertIn("cargo", groups)

        subsystem_items = {item["type_name"] for item in groups["subsystems"]["items"]}
        cargo_items = {item["type_name"] for item in groups["cargo"]["items"]}

        self.assertSetEqual(
            subsystem_items,
            {
                "Legion Defensive - Augmented Plating",
                "Legion Core - Energy Parasitic Complex",
                "Legion Offensive - Liquid Crystal Magnifiers",
                "Legion Propulsion - Intercalated Nanofibers",
            },
        )
        self.assertSetEqual(cargo_items, {"Nanite Repair Paste", "Scorch M"})

    @patch("indy_hub.services.production_projects._resolve_blueprints_for_products")
    @patch("indy_hub.services.production_projects._resolve_item_types_by_name")
    def test_build_preview_keeps_tengu_fit_subsystems_and_spare_subsystem_separate(
        self,
        mock_resolve_items,
        mock_resolve_blueprints,
    ) -> None:
        mock_resolve_items.return_value = {
            "tengu": {
                "type_id": 20,
                "type_name": "Tengu",
                "group_name": "Strategic Cruiser",
            },
            "tengu core - augmented graviton reactor": {
                "type_id": 21,
                "type_name": "Tengu Core - Augmented Graviton Reactor",
                "group_name": "Core Systems",
            },
            "tengu defensive - covert reconfiguration": {
                "type_id": 22,
                "type_name": "Tengu Defensive - Covert Reconfiguration",
                "group_name": "Defensive Systems",
            },
            "tengu offensive - support processor": {
                "type_id": 23,
                "type_name": "Tengu Offensive - Support Processor",
                "group_name": "Offensive Systems",
            },
            "tengu propulsion - chassis optimization": {
                "type_id": 24,
                "type_name": "Tengu Propulsion - Chassis Optimization",
                "group_name": "Propulsion Systems",
            },
            "tengu defensive - supplemental screening": {
                "type_id": 25,
                "type_name": "Tengu Defensive - Supplemental Screening",
                "group_name": "Defensive Systems",
            },
            "warrior ii": {
                "type_id": 26,
                "type_name": "Warrior II",
                "group_name": "Light Scout Drone",
            },
            "acolyte ii": {
                "type_id": 27,
                "type_name": "Acolyte II",
                "group_name": "Light Scout Drone",
            },
        }
        mock_resolve_blueprints.return_value = {
            20: 120,
            21: 121,
            22: 122,
            23: 123,
            24: 124,
            25: 125,
            26: 126,
            27: 127,
        }

        preview = build_project_import_preview(
            """
[Tengu,  i.Dreamcatcher Boost]

Damage Control II
Power Diagnostic System II
Power Diagnostic System II

Multispectrum Shield Hardener II
Large Shield Extender II
50MN Quad LiF Restrained Microwarpdrive
Large Shield Extender II
EM Shield Hardener II
Large Shield Extender II

Covert Ops Cloaking Device II
Medium Asymmetric Enduring Remote Shield Booster
Medium Ancillary Remote Shield Booster
Shield Command Burst II
Shield Command Burst II
Shield Command Burst II
Information Command Burst II

Medium Command Processor I
Medium Command Processor I
Medium Core Defense Field Extender II

Tengu Core - Augmented Graviton Reactor
Tengu Defensive - Covert Reconfiguration
Tengu Offensive - Support Processor
Tengu Propulsion - Chassis Optimization





Warrior II x5
Acolyte II x5


Active Shielding Charge x300
Shield Harmonizing Charge x300
Shield Extension Charge x300
Sensor Optimization Charge x300
Electronic Superiority Charge x300
Electronic Hardening Charge x300
Evasive Maneuvers Charge x300
Interdiction Maneuvers Charge x300
Rapid Deployment Charge x300
Navy Cap Booster 50 x27
Nanite Repair Paste x100
Shield Command Mindlink x1
Information Command Burst II x2
Skirmish Command Burst II x3
Power Diagnostic System II x2
Tengu Defensive - Supplemental Screening x1
Interdiction Nullifier I x1
Mobile Depot x1
""".strip()
        )

        groups = {group["key"]: group for group in preview["groups"]}
        self.assertIn("subsystems", groups)
        self.assertIn("cargo", groups)
        self.assertIn("drone_bay", groups)

        subsystem_items = {item["type_name"] for item in groups["subsystems"]["items"]}
        cargo_items = {item["type_name"] for item in groups["cargo"]["items"]}
        drone_items = {item["type_name"] for item in groups["drone_bay"]["items"]}

        self.assertSetEqual(
            subsystem_items,
            {
                "Tengu Core - Augmented Graviton Reactor",
                "Tengu Defensive - Covert Reconfiguration",
                "Tengu Offensive - Support Processor",
                "Tengu Propulsion - Chassis Optimization",
            },
        )
        self.assertIn("Tengu Defensive - Supplemental Screening", cargo_items)
        self.assertFalse(subsystem_items & {"Tengu Defensive - Supplemental Screening"})
        self.assertSetEqual(drone_items, {"Warrior II", "Acolyte II"})

    @patch("indy_hub.services.production_projects._resolve_user_blueprint_inventory")
    def test_project_blueprint_configs_use_user_owned_efficiencies_and_names(
        self,
        mock_inventory,
    ) -> None:
        mock_inventory.return_value = {
            46165: {
                "original": {"me": 10, "te": 20},
                "best_copy": None,
                "copy_runs_total": 0,
            }
        }

        blueprints, grouped = _build_project_blueprint_configs_grouped(
            user=object(),
            cycle_summary={
                78272: {
                    "type_id": 78272,
                    "type_name": "Cyclone Fleet Issue",
                    "total_needed": 2,
                }
            },
            product_blueprint_cache={78272: 46165},
            overrides={},
            type_name_cache={46165: "Cyclone Fleet Issue Blueprint"},
        )

        self.assertEqual(len(blueprints), 1)
        self.assertEqual(blueprints[0]["type_name"], "Cyclone Fleet Issue Blueprint")
        self.assertTrue(blueprints[0]["user_owns"])
        self.assertEqual(blueprints[0]["material_efficiency"], 10)
        self.assertEqual(blueprints[0]["time_efficiency"], 20)
        self.assertEqual(blueprints[0]["user_material_efficiency"], 10)
        self.assertEqual(blueprints[0]["user_time_efficiency"], 20)
        self.assertEqual(
            grouped[0]["levels"][0]["blueprints"][0]["type_name"],
            "Cyclone Fleet Issue Blueprint",
        )

    @patch("indy_hub.services.production_projects.is_reaction_blueprint")
    @patch("indy_hub.services.production_projects._resolve_user_blueprint_inventory")
    def test_project_blueprint_configs_flag_reaction_blueprints(
        self,
        mock_inventory,
        mock_is_reaction,
    ) -> None:
        mock_inventory.return_value = {}
        mock_is_reaction.return_value = True

        blueprints, _grouped = _build_project_blueprint_configs_grouped(
            user=object(),
            cycle_summary={
                16670: {
                    "type_id": 16670,
                    "type_name": "Carbon Fiber",
                    "total_needed": 5,
                }
            },
            product_blueprint_cache={16670: 17887},
            overrides={},
            type_name_cache={17887: "Carbon Fiber Reaction Formula"},
        )

        self.assertEqual(len(blueprints), 1)
        self.assertTrue(blueprints[0]["is_reaction"])
        mock_is_reaction.assert_called_with(17887)
