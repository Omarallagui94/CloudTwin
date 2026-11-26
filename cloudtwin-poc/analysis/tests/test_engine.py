"""Tests du moteur : déterminisme, validation du contrat, résumé."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from analysis.engine import SchemaVersionError, load_context, run
from analysis.rules import ALL_RULES

FIX = Path(__file__).parent / "fixtures"


def test_rule_ids_are_unique():
    ids = [r.rule_id for r in ALL_RULES]
    assert len(ids) == len(set(ids))


def test_same_input_gives_identical_output():
    ctx = load_context(FIX / "res001/positive_3_to_1.json")
    assert run(ctx).model_dump_json() == run(ctx).model_dump_json()


def test_summary_counts():
    result = run(load_context(FIX / "res001/positive_3_to_1.json"))
    assert result.summary["fail"] == 1
    assert result.summary["fail_high"] == 1
    assert result.simulation_id == "sim-res001-pos"


def test_wrong_schema_version_is_rejected(tmp_path):
    data = json.loads((FIX / "res001/positive_3_to_1.json").read_text())
    data["schema_version"] = "0.9"
    p = tmp_path / "ctx.json"
    p.write_text(json.dumps(data))
    with pytest.raises(SchemaVersionError):
        load_context(p)


def test_invalid_operation_is_rejected(tmp_path):
    data = json.loads((FIX / "res001/positive_3_to_1.json").read_text())
    data["changes"][0]["operation"] = "patch"
    p = tmp_path / "ctx.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ValidationError):
        load_context(p)
