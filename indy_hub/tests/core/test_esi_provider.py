"""Check operation filtering with django-esi without contacting ESI."""

# Third Party
from aiopenapi3.plugin import Document

# Django
from django.test import SimpleTestCase

# Alliance Auth
from esi.aiopenapi3.plugins import MinifySpec

# AA Example App
from indy_hub.services.providers import esi_provider
from indy_hub.tests.core.esi_contract_fixture import openapi_spec


class EsiProviderDivisionsTests(SimpleTestCase):
    def test_only_public_prices_survive_market_operation_filtering(self):
        spec = openapi_spec()
        spec["paths"]["/markets/{region_id}/orders"] = {
            "get": {
                "tags": ["Market"],
                "operationId": "GetMarketsRegionIdOrders",
                "responses": {},
            }
        }
        filtered = (
            MinifySpec(esi_provider._tags, esi_provider._operations)
            .parsed(
                Document.Context(url="https://esi.invalid/openapi.json", document=spec)
            )
            .document
        )
        self.assertIn("/markets/prices", filtered["paths"])
        self.assertIn("Prices", filtered["components"]["schemas"])
        self.assertNotIn("/markets/{region_id}/orders", filtered["paths"])

    def test_filtered_spec_retains_corporation_divisions_and_response_schema(self):
        path = "/corporations/{corporation_id}/divisions/"
        schema_name = "CorporationsCorporationIdDivisionsGet"
        spec = {
            "paths": {
                path: {
                    "get": {
                        "tags": ["Corporation"],
                        "operationId": "GetCorporationsCorporationIdDivisions",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": f"#/components/schemas/{schema_name}"
                                        }
                                    }
                                }
                            }
                        },
                    }
                },
                "/corporations/{corporation_id}/": {
                    "get": {
                        "tags": ["Corporation"],
                        "operationId": "GetCorporationsCorporationId",
                        "responses": {},
                    }
                },
            },
            "components": {
                "schemas": {
                    schema_name: {"type": "object"},
                    "UnusedSchema": {"type": "object"},
                }
            },
        }
        context = Document.Context(
            url="https://esi.invalid/openapi.json", document=spec
        )

        filtered = (
            MinifySpec(esi_provider._tags, esi_provider._operations)
            .parsed(context)
            .document
        )

        self.assertEqual(set(filtered["paths"]), {path})
        self.assertEqual(set(filtered["components"]["schemas"]), {schema_name})
