"""Check operation filtering with django-esi without contacting ESI."""

# Third Party
from aiopenapi3.plugin import Document

# Django
from django.test import SimpleTestCase

# Alliance Auth
from esi.aiopenapi3.plugins import MinifySpec

# AA Example App
from indy_hub.services.providers import esi_provider


class EsiProviderDivisionsTests(SimpleTestCase):
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
