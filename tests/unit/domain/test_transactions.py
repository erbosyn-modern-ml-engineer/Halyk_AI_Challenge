"""Transaction and currency invariant tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from halyk_agent.domain.common import CurrencyCode
from halyk_agent.domain.transactions import Transaction, TransactionStatus


def _txn(**overrides: object) -> Transaction:
    payload: dict[str, object] = {
        "id": "txn-1",
        "amount": Decimal("10.50"),
        "currency": "kzt",
        "occurred_at": datetime(2024, 6, 1, tzinfo=UTC),
        "status": TransactionStatus.POSTED,
    }
    payload.update(overrides)
    return Transaction.model_validate(payload)


def test_transaction_rejects_float_amount() -> None:
    with pytest.raises((ValidationError, TypeError)):
        _txn(amount=10.5)


def test_transaction_accepts_exact_decimal_amount() -> None:
    txn = _txn(amount=Decimal("10.50"))
    assert txn.amount == Decimal("10.50")


def test_transaction_accepts_string_and_int_amounts() -> None:
    assert _txn(amount="10.50").amount == Decimal("10.50")
    assert _txn(amount=10).amount == Decimal("10")


def test_transaction_json_does_not_expose_binary_float_artifact() -> None:
    txn = _txn(amount=Decimal("0.1"))
    payload = json.loads(txn.model_dump_json())
    assert payload["amount"] == "0.1"
    assert isinstance(payload["amount"], str)
    assert payload["amount"] != 0.1


def test_currency_normalizes_to_uppercase() -> None:
    adapter = TypeAdapter(CurrencyCode)
    assert adapter.validate_python("kzt") == "KZT"
    assert _txn(currency="usd").currency == "USD"


def test_invalid_currency_is_rejected() -> None:
    adapter = TypeAdapter(CurrencyCode)
    with pytest.raises(ValidationError):
        adapter.validate_python("KZ")
    with pytest.raises(ValidationError):
        adapter.validate_python("KZ1")
