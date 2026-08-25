from decimal import Decimal
from datetime import date

from acca_app.models import ScoreOrigin, SettlementStatus
from acca_app.parser import (
    ExtractedAccumulator,
    ScoreLookupBatch,
    apply_score_lookups,
)


def _patterns(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "pattern":
                yield child
            yield from _patterns(child)
    elif isinstance(value, list):
        for child in value:
            yield from _patterns(child)


def extraction_payload():
    return {
        "legs": [
            {"selection": "A & Yes", "market": "Result/BTTS", "event": "A v B",
             "selected_outcome": "home_win", "home_team": "A", "away_team": "B",
             "fixture_date": "2026-08-08", "odds": 3.25,
             "home_score": 2, "away_score": 1, "settlement_status": "won"},
            {"selection": "Draw & Yes", "market": "Result/BTTS", "event": "C v D",
             "selected_outcome": "draw", "home_team": "C", "away_team": "D", "odds": 4.0,
             "home_score": 0, "away_score": 0, "settlement_status": "lost"},
        ],
        "stake": 6, "return": 0, "settlement_status": "lost", "confidence": 0.9,
    }


def test_openai_schema_has_no_regex_patterns_or_combined_odds():
    schema = ExtractedAccumulator.model_json_schema()
    assert list(_patterns(schema)) == []
    assert "combined_odds" not in str(schema)
    assert list(_patterns(ScoreLookupBatch.model_json_schema())) == []


def test_extraction_converts_numbers_to_decimal_domain_values():
    parsed = ExtractedAccumulator.model_validate(extraction_payload()).to_domain()
    assert parsed.stake == Decimal("6.0")
    assert parsed.legs[0].odds == Decimal("3.25")
    assert parsed.legs[0].fixture_date == date(2026, 8, 8)
    assert parsed.legs[0].score_origin == ScoreOrigin.RECEIPT


def test_verified_web_lookup_updates_missing_score_and_result():
    payload = extraction_payload()
    payload["legs"][0]["home_score"] = None
    payload["legs"][0]["away_score"] = None
    payload["legs"][0]["settlement_status"] = "pending"
    parsed = ExtractedAccumulator.model_validate(payload).to_domain()
    batch = ScoreLookupBatch.model_validate({"results": [{
        "leg_index": 0, "home_team": "A", "away_team": "B", "home_score": 2,
        "away_score": 1, "match_date": "2026-08-24", "confidence": 0.95,
        "source_title": "Match report", "source_url": "https://example.com/result",
        "evidence": "A beat B 2-1.",
    }]})
    apply_score_lookups(parsed, batch, {"https://example.com/result": "Match report"})
    assert (parsed.legs[0].home_score, parsed.legs[0].away_score) == (2, 1)
    assert parsed.legs[0].settlement_status == SettlementStatus.WON
    assert parsed.legs[0].score_origin == ScoreOrigin.WEB


def test_unverified_web_source_does_not_update_score():
    payload = extraction_payload()
    payload["legs"][0]["home_score"] = None
    payload["legs"][0]["away_score"] = None
    parsed = ExtractedAccumulator.model_validate(payload).to_domain()
    batch = ScoreLookupBatch.model_validate({"results": [{
        "leg_index": 0, "home_team": "A", "away_team": "B", "home_score": 2,
        "away_score": 1, "match_date": None, "confidence": 0.95,
        "source_title": "Unknown", "source_url": "https://unverified.example/result",
        "evidence": "Unverified.",
    }]})
    apply_score_lookups(parsed, batch, {})
    assert parsed.legs[0].home_score is None
    assert parsed.warnings
