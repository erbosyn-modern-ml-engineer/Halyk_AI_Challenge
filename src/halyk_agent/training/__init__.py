"""Training-only package (must not be imported by competition solver)."""

from halyk_agent.training.scorer import score_submission

__all__ = ["score_submission"]
