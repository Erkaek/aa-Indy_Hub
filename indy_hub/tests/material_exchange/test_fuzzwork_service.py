"""Tests for the Fuzzwork market API helper."""

# Standard Library
from unittest.mock import MagicMock, patch

# Django
from django.test import SimpleTestCase

# AA Example App
from indy_hub.services.fuzzwork import (
    FUZZWORK_TYPES_PER_REQUEST,
    fetch_fuzzwork_aggregates,
)


class FuzzworkServiceTests(SimpleTestCase):
    @patch("indy_hub.services.fuzzwork.requests.get")
    def test_fetch_aggregates_chunks_large_type_lists(self, mock_get) -> None:
        type_ids = list(range(1, FUZZWORK_TYPES_PER_REQUEST * 2 + 6))

        def response_for_url(url, **_kwargs):
            chunk_ids = url.rsplit("types=", 1)[1].split(",")
            response = MagicMock()
            response.json.return_value = {
                type_id: {"buy": {"max": type_id}, "sell": {"min": type_id}}
                for type_id in chunk_ids
            }
            return response

        mock_get.side_effect = response_for_url

        aggregates = fetch_fuzzwork_aggregates(type_ids, timeout=7)

        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(set(aggregates), {str(type_id) for type_id in type_ids})
        for call in mock_get.call_args_list:
            requested_ids = call.args[0].rsplit("types=", 1)[1].split(",")
            self.assertLessEqual(len(requested_ids), FUZZWORK_TYPES_PER_REQUEST)
            self.assertEqual(call.kwargs["timeout"], 7)
