"""Stage 5F security: no GT path access."""

from __future__ import annotations

from pathlib import Path

import pytest

from halyk_agent.app.transactions import TransactionServiceError, assert_no_gt_access


def test_gt_path_rejected() -> None:
    with pytest.raises(TransactionServiceError) as exc:
        assert_no_gt_access(Path("ground_truth.json"))
    assert exc.value.code == "GT_FORBIDDEN"


def test_answer_key_rejected() -> None:
    with pytest.raises(TransactionServiceError):
        assert_no_gt_access(Path("something_answer_key.json"))
