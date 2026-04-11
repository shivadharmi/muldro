"""Tests for trust API, policy absorption, and dead code deletion."""

import importlib


def test_approval_policy_engine_deleted():
    """ApprovalPolicyEngine must not be importable."""
    with __import__("pytest").raises(ModuleNotFoundError):
        importlib.import_module("src.services.approval_policy_engine")


def test_trust_score_model_deleted():
    """TrustScore model must not be importable from models."""
    with __import__("pytest").raises(ImportError):
        from src.models.trust_score import TrustScore  # noqa: F401


def test_approval_policy_model_deleted():
    """ApprovalPolicy model must not be importable from models."""
    with __import__("pytest").raises(ImportError):
        from src.models.approval_policy import ApprovalPolicy  # noqa: F401
