"""Contracts for the governed pricing decision room."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from governance.build_pricing_decision_evidence import (
    decision_state,
    required_reviewer,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"


@pytest.mark.parametrize(
    ("action", "change", "expected"),
    [
        ("Fix cost", 0.0, "Pricing Data Steward"),
        ("Increase", 0.04, "Sales Manager"),
        ("Discount", -0.05, "Sales Manager"),
        ("Increase", 0.06, "Commercial Director"),
        ("Increase", 0.08, "Finance Controller"),
        ("Discontinue", 0.0, "Finance Controller"),
    ],
)
def test_the_delegated_reviewer_is_explicit(action, change, expected):
    assert required_reviewer(action, change) == expected


@pytest.mark.parametrize(
    ("action", "confidence", "expected"),
    [
        ("Fix cost", "High", "RETURN_FOR_DATA"),
        ("Maintain", "High", "NO_CHANGE"),
        ("Increase", "High", "READY_FOR_REVIEW"),
        ("Discount", "High", "READY_FOR_REVIEW"),
        ("Increase", "Medium", "REVIEW_REQUIRED"),
        ("Discontinue", "High", "REVIEW_REQUIRED"),
    ],
)
def test_the_request_state_never_implies_approval(action, confidence, expected):
    assert decision_state(action, confidence) == expected


@pytest.fixture(scope="module")
def built_summary():
    return json.loads((OUT / "pricing_decision_summary.json").read_text())


@pytest.mark.parametrize(
    "name",
    [
        "pricing_decision_register.csv",
        "price_realization_monitor.csv",
        "pricing_release_gates.csv",
        "pricing_decision_summary.json",
        "pricing_decision_manifest.json",
        "pricing_decision_packet.md",
    ],
)
def test_the_release_artifact_exists_after_the_build(built_summary, name):
    assert (OUT / name).is_file()


def test_the_release_is_honestly_review_required(built_summary):
    assert built_summary["release_posture"] == "REVIEW REQUIRED"
    assert built_summary["gates"] == {"PASS": 6, "REVIEW": 2, "BLOCK": 0}


def test_the_register_has_one_request_per_product(built_summary):
    register = pd.read_csv(OUT / "pricing_decision_register.csv")
    source = pd.read_csv(OUT / "recommendations.csv")
    assert len(register) == len(source) == built_summary["proposal_count"]
    assert register["request_id"].is_unique
    assert register["product_id"].is_unique


def test_the_public_register_records_no_material_approval(built_summary):
    register = pd.read_csv(OUT / "pricing_decision_register.csv")
    material = register[register["action"] != "Maintain"]
    assert set(material["human_decision"]) == {"OPEN"}
    assert not register["decision_state"].str.contains("APPROVED").any()


def test_every_material_request_has_execution_controls(built_summary):
    register = pd.read_csv(OUT / "pricing_decision_register.csv")
    material = register[register["action"] != "Maintain"]
    for column in (
        "required_reviewer", "effective_period", "monitoring_window",
        "rollback_trigger", "evidence_refs", "rationale",
    ):
        assert material[column].fillna("").str.strip().ne("").all(), column


def test_missing_costs_are_returned_before_pricing(built_summary):
    register = pd.read_csv(OUT / "pricing_decision_register.csv")
    returned = register[register["action"] == "Fix cost"]
    assert len(returned) > 0
    assert set(returned["decision_state"]) == {"RETURN_FOR_DATA"}
    assert set(returned["required_reviewer"]) == {"Pricing Data Steward"}


def test_realization_monitor_has_fixed_pre_and_post_windows(built_summary):
    monitor = pd.read_csv(OUT / "price_realization_monitor.csv")
    assert len(monitor) == built_summary["historical_changes_monitored"]
    assert monitor["pre_months"].between(0, 3).all()
    assert monitor["post_months"].between(0, 3).all()
    assert set(monitor["realization_state"]) <= {"PASS", "REVIEW"}


def test_realization_is_not_presented_as_causal(built_summary):
    packet = (OUT / "pricing_decision_packet.md").read_text(encoding="utf-8").lower()
    governance = (ROOT / "governance" / "PRICING_DECISION_GOVERNANCE.md").read_text(
        encoding="utf-8"
    ).lower()
    assert "observational" in packet
    assert "not causal" in packet
    assert "observational" in governance
    assert "does not attribute a causal effect" in governance


def test_the_gate_docket_has_unique_controls(built_summary):
    gates = pd.read_csv(OUT / "pricing_release_gates.csv")
    assert len(gates) == 8
    assert gates["control_id"].is_unique
    assert set(gates["state"]) == {"PASS", "REVIEW"}


def test_the_manifest_verifies_every_listed_artifact(built_summary):
    manifest = json.loads((OUT / "pricing_decision_manifest.json").read_text())
    assert len(manifest["artifacts"]) == 5
    for artifact in manifest["artifacts"]:
        path = ROOT / artifact["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]


def test_the_packet_states_the_non_approval_boundary(built_summary):
    packet = (OUT / "pricing_decision_packet.md").read_text(encoding="utf-8")
    assert "zero approvals are recorded" in packet.lower()
    assert "cannot approve or publish a customer price" in packet


def test_the_live_view_reads_only_governed_aggregate_outputs():
    source = (ROOT / "app" / "views" / "decision_room.py").read_text(encoding="utf-8")
    assert "pricing_decision_register" in source
    assert "price_realization_monitor" in source
    assert "customer_id" not in source
    assert "fact_sales" not in source
