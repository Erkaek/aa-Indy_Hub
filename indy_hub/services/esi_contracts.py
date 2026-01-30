"""ESI contract validation helpers for Material Exchange."""

# Standard Library
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

# Alliance Auth
from allianceauth.services.hooks import get_extension_logger

# Local
from .esi_client import ESIClientError, shared_client

logger = get_extension_logger(__name__)


@dataclass
class ContractDetails:
    """Details of an ESI contract."""

    contract_id: int
    issuer_id: int
    assignee_id: int
    acceptor_id: int | None
    type: str
    status: str
    title: str
    date_issued: datetime
    date_expired: datetime
    date_accepted: datetime | None
    date_completed: datetime | None
    price: float
    reward: float
    collateral: float
    volume: float


@dataclass
class ContractItem:
    """Item in an ESI contract."""

    record_id: int
    type_id: int
    quantity: int
    is_included: bool  # True if included in contract, False if requested


class ESIContractValidator:
    """Validates Material Exchange orders against ESI contracts."""

    def __init__(self):
        self.client = shared_client

    @staticmethod
    def _normalize_price(value: float | int | Decimal | None) -> Decimal:
        """Normalize price values to Decimal(0.01) precision."""
        try:
            return Decimal(str(value or 0)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError(f"Invalid price value: {value}") from exc

    def fetch_character_contracts(
        self, character_id: int, *, include_items: bool = False
    ) -> list[dict]:
        """
        Fetch contracts for a character.
        Returns list of contract dicts from ESI.
        """
        endpoint = f"/characters/{character_id}/contracts/"
        contracts = self.client._fetch_paginated(
            character_id=character_id,
            scope="esi-contracts.read_character_contracts.v1",
            endpoint=endpoint,
        )

        if include_items:
            for contract in contracts:
                contract_id = contract.get("contract_id")
                if contract_id:
                    try:
                        items = self.fetch_contract_items(character_id, contract_id)
                        contract["items"] = items
                    except ESIClientError as exc:
                        logger.warning(
                            f"Failed to fetch items for contract {contract_id}: {exc}"
                        )
                        contract["items"] = []

        return contracts

    def fetch_corporation_contracts(
        self, corporation_id: int, *, character_id: int, include_items: bool = False
    ) -> list[dict]:
        """
        Fetch contracts for a corporation.
        character_id must have esi-contracts.read_corporation_contracts.v1 scope.
        """
        endpoint = f"/corporations/{corporation_id}/contracts/"
        contracts = self.client._fetch_paginated(
            character_id=character_id,
            scope="esi-contracts.read_corporation_contracts.v1",
            endpoint=endpoint,
        )

        if include_items:
            for contract in contracts:
                contract_id = contract.get("contract_id")
                if contract_id:
                    try:
                        items = self.fetch_corporation_contract_items(
                            corporation_id, contract_id, character_id=character_id
                        )
                        contract["items"] = items
                    except ESIClientError as exc:
                        logger.warning(
                            f"Failed to fetch items for corp contract {contract_id}: {exc}"
                        )
                        contract["items"] = []

        return contracts

    def fetch_contract_items(self, character_id: int, contract_id: int) -> list[dict]:
        """Fetch items for a specific character contract."""
        access_token = self.client._get_access_token(
            character_id, "esi-contracts.read_character_contracts.v1"
        )
        url = f"{self.client.base_url}/characters/{character_id}/contracts/{contract_id}/items/"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"datasource": "tranquility"}

        response = self.client._request("GET", url, headers=headers, params=params)
        items = response.json()

        if not isinstance(items, list):
            raise ESIClientError(
                f"ESI contract items endpoint returned non-list: {type(items)}"
            )

        return items

    def fetch_corporation_contract_items(
        self, corporation_id: int, contract_id: int, *, character_id: int
    ) -> list[dict]:
        """Fetch items for a specific corporation contract."""
        access_token = self.client._get_access_token(
            character_id, "esi-contracts.read_corporation_contracts.v1"
        )
        url = f"{self.client.base_url}/corporations/{corporation_id}/contracts/{contract_id}/items/"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"datasource": "tranquility"}

        response = self.client._request("GET", url, headers=headers, params=params)
        items = response.json()

        if not isinstance(items, list):
            raise ESIClientError(
                f"ESI corp contract items endpoint returned non-list: {type(items)}"
            )

        return items

    def parse_contract_details(self, raw_contract: dict) -> ContractDetails | None:
        """Parse raw ESI contract dict into ContractDetails dataclass."""
        try:
            return ContractDetails(
                contract_id=raw_contract["contract_id"],
                issuer_id=raw_contract["issuer_id"],
                assignee_id=raw_contract.get("assignee_id", 0),
                acceptor_id=raw_contract.get("acceptor_id"),
                type=raw_contract["type"],
                status=raw_contract["status"],
                title=raw_contract.get("title", ""),
                date_issued=datetime.fromisoformat(
                    raw_contract["date_issued"].replace("Z", "+00:00")
                ),
                date_expired=datetime.fromisoformat(
                    raw_contract["date_expired"].replace("Z", "+00:00")
                ),
                date_accepted=(
                    datetime.fromisoformat(
                        raw_contract["date_accepted"].replace("Z", "+00:00")
                    )
                    if raw_contract.get("date_accepted")
                    else None
                ),
                date_completed=(
                    datetime.fromisoformat(
                        raw_contract["date_completed"].replace("Z", "+00:00")
                    )
                    if raw_contract.get("date_completed")
                    else None
                ),
                price=raw_contract.get("price", 0.0),
                reward=raw_contract.get("reward", 0.0),
                collateral=raw_contract.get("collateral", 0.0),
                volume=raw_contract.get("volume", 0.0),
            )
        except (KeyError, ValueError) as exc:
            logger.warning(f"Failed to parse contract details: {exc}")
            return None

    def parse_contract_items(self, raw_items: list[dict]) -> list[ContractItem]:
        """Parse raw ESI contract items into ContractItem dataclass list."""
        items: list[ContractItem] = []
        for raw_item in raw_items:
            try:
                items.append(
                    ContractItem(
                        record_id=raw_item["record_id"],
                        type_id=raw_item["type_id"],
                        quantity=raw_item["quantity"],
                        is_included=raw_item["is_included"],
                    )
                )
            except (KeyError, ValueError) as exc:
                logger.warning(f"Failed to parse contract item: {exc}")
                continue
        return items

    def validate_sell_order_contract(
        self,
        contract_id: int,
        character_id: int,
        expected_items: list[dict],
        expected_price: Decimal,
        *,
        hub_character_id: int | None = None,
        order_ref: str | None = None,
    ) -> dict:
        """
        Validate that a contract matches a sell order (member → hub).

        Args:
            contract_id: ESI contract ID
            character_id: Member's character ID (issuer)
            expected_items: List of dicts with 'type_id' and 'quantity'
            expected_price: Total ISK expected on the contract
            hub_character_id: Hub character ID (acceptor, optional)
            order_ref: Reference that should appear in the contract title (optional)

        Returns:
            dict with validation results:
            {
                'valid': bool,
                'status': str,  # 'outstanding', 'in_progress', 'finished', 'rejected', etc.
                'accepted': bool,
                'completed': bool,
                'items_match': bool,
                'price_match': bool,
                'title_ref_match': bool,
                'missing_items': list,
                'extra_items': list,
                'message': str
            }
        """
        result = {
            "valid": False,
            "status": "unknown",
            "accepted": False,
            "completed": False,
            "items_match": False,
            "price_match": False,
            "title_ref_match": True,
            "missing_items": [],
            "extra_items": [],
            "message": "",
        }

        try:
            try:
                expected_price_dec = self._normalize_price(expected_price)
            except ValueError as exc:
                result["message"] = str(exc)
                return result

            # Fetch contract details
            contracts = self.fetch_character_contracts(character_id, include_items=True)
            contract = next(
                (c for c in contracts if c.get("contract_id") == contract_id), None
            )

            if not contract:
                result["message"] = f"Contract {contract_id} not found"
                return result

            details = self.parse_contract_details(contract)
            if not details:
                result["message"] = "Failed to parse contract details"
                return result

            result["status"] = details.status
            result["accepted"] = details.status in [
                "in_progress",
                "finished_issuer",
                "finished_contractor",
                "finished",
            ]
            result["completed"] = details.status in [
                "finished_issuer",
                "finished_contractor",
                "finished",
            ]

            try:
                contract_price = self._normalize_price(details.price)
            except ValueError as exc:
                result["message"] = str(exc)
                return result

            result["price_match"] = contract_price == expected_price_dec

            if order_ref:
                result["title_ref_match"] = (
                    order_ref.lower() in (details.title or "").lower()
                )

            # Validate contract type (should be item_exchange for sell orders)
            if details.type not in ["item_exchange", "courier"]:
                result["message"] = (
                    f"Invalid contract type: {details.type} (expected item_exchange)"
                )
                return result

            # Validate items
            contract_items_raw = contract.get("items", [])
            contract_items = self.parse_contract_items(contract_items_raw)

            # Build expected items map
            expected_map = {
                item["type_id"]: item["quantity"] for item in expected_items
            }

            # Build contract included items map (what member is giving)
            contract_map = {
                item.type_id: item.quantity
                for item in contract_items
                if item.is_included
            }

            # Check for missing items
            for type_id, expected_qty in expected_map.items():
                contract_qty = contract_map.get(type_id, 0)
                if contract_qty < expected_qty:
                    result["missing_items"].append(
                        {
                            "type_id": type_id,
                            "expected": expected_qty,
                            "found": contract_qty,
                        }
                    )

            # Check for extra items
            for type_id, contract_qty in contract_map.items():
                if type_id not in expected_map:
                    result["extra_items"].append(
                        {"type_id": type_id, "quantity": contract_qty}
                    )

            result["items_match"] = (
                not result["missing_items"] and not result["extra_items"]
            )
            result["valid"] = result["items_match"] and result["price_match"]

            reasons: list[str] = []

            if not result["items_match"]:
                if result["missing_items"]:
                    reasons.append(f"Missing items: {result['missing_items']}")
                if result["extra_items"]:
                    reasons.append(f"Unexpected items: {result['extra_items']}")

            if not result["price_match"]:
                reasons.append(
                    f"Price mismatch: contract {contract_price:,.2f} ISK vs expected {expected_price_dec:,.2f} ISK"
                )

            if order_ref and not result["title_ref_match"]:
                reasons.append(f"Contract title missing reference {order_ref}")

            if not reasons:
                result["message"] = (
                    f"Contract validated successfully (status: {result['status']})"
                )
            else:
                result["message"] = "; ".join(reasons)

        except ESIClientError as exc:
            result["message"] = f"ESI error: {exc}"
            logger.error(f"ESI contract validation failed: {exc}")
        except Exception as exc:
            result["message"] = f"Unexpected error: {exc}"
            logger.error(f"Contract validation error: {exc}", exc_info=True)

        return result

    def validate_buy_order_contract(
        self,
        contract_id: int,
        corporation_id: int,
        character_id: int,
        expected_items: list[dict],
        *,
        expected_price: Decimal,
        member_character_id: int | None = None,
        order_ref: str | None = None,
    ) -> dict:
        """
        Validate that a contract matches a buy order (hub → member).

        Args:
            contract_id: ESI contract ID
            corporation_id: Hub corporation ID (issuer)
            character_id: Character with corporation contract access
            expected_items: List of dicts with 'type_id' and 'quantity'
            expected_price: Total ISK expected on the contract
            member_character_id: Member's character ID (acceptor, optional)
            order_ref: Reference that should appear in the contract title (optional)

        Returns:
            Same format as validate_sell_order_contract
        """
        result = {
            "valid": False,
            "status": "unknown",
            "accepted": False,
            "completed": False,
            "items_match": False,
            "price_match": False,
            "title_ref_match": True,
            "missing_items": [],
            "extra_items": [],
            "message": "",
        }

        try:
            try:
                expected_price_dec = self._normalize_price(expected_price)
            except ValueError as exc:
                result["message"] = str(exc)
                return result

            # Fetch corporation contracts
            contracts = self.fetch_corporation_contracts(
                corporation_id, character_id=character_id, include_items=True
            )
            contract = next(
                (c for c in contracts if c.get("contract_id") == contract_id), None
            )

            if not contract:
                result["message"] = f"Contract {contract_id} not found in corporation"
                return result

            details = self.parse_contract_details(contract)
            if not details:
                result["message"] = "Failed to parse contract details"
                return result

            result["status"] = details.status
            result["accepted"] = details.status in [
                "in_progress",
                "finished_issuer",
                "finished_contractor",
                "finished",
            ]
            result["completed"] = details.status in [
                "finished_issuer",
                "finished_contractor",
                "finished",
            ]

            try:
                contract_price = self._normalize_price(details.price)
            except ValueError as exc:
                result["message"] = str(exc)
                return result

            result["price_match"] = contract_price == expected_price_dec

            if order_ref:
                result["title_ref_match"] = (
                    order_ref.lower() in (details.title or "").lower()
                )

            # Validate items
            contract_items_raw = contract.get("items", [])
            contract_items = self.parse_contract_items(contract_items_raw)

            expected_map = {
                item["type_id"]: item["quantity"] for item in expected_items
            }
            contract_map = {
                item.type_id: item.quantity
                for item in contract_items
                if item.is_included
            }

            for type_id, expected_qty in expected_map.items():
                contract_qty = contract_map.get(type_id, 0)
                if contract_qty < expected_qty:
                    result["missing_items"].append(
                        {
                            "type_id": type_id,
                            "expected": expected_qty,
                            "found": contract_qty,
                        }
                    )

            for type_id, contract_qty in contract_map.items():
                if type_id not in expected_map:
                    result["extra_items"].append(
                        {"type_id": type_id, "quantity": contract_qty}
                    )

            result["items_match"] = (
                not result["missing_items"] and not result["extra_items"]
            )
            result["valid"] = result["items_match"] and result["price_match"]

            reasons: list[str] = []

            if not result["items_match"]:
                if result["missing_items"]:
                    reasons.append(f"Missing items: {result['missing_items']}")
                if result["extra_items"]:
                    reasons.append(f"Unexpected items: {result['extra_items']}")

            if not result["price_match"]:
                reasons.append(
                    f"Price mismatch: contract {contract_price:,.2f} ISK vs expected {expected_price_dec:,.2f} ISK"
                )

            if order_ref and not result["title_ref_match"]:
                reasons.append(f"Contract title missing reference {order_ref}")

            if not reasons:
                result["message"] = (
                    f"Contract validated successfully (status: {result['status']})"
                )
            else:
                result["message"] = "; ".join(reasons)

        except ESIClientError as exc:
            result["message"] = f"ESI error: {exc}"
            logger.error(f"ESI buy contract validation failed: {exc}")
        except Exception as exc:
            result["message"] = f"Unexpected error: {exc}"
            logger.error(f"Buy contract validation error: {exc}", exc_info=True)

        return result


# Singleton instance
contract_validator = ESIContractValidator()
