from decimal import Decimal

import pytest
from pydantic import ValidationError

from acca_app.models import (
    ParsedAccumulator,
    SelectedOutcome,
    SettlementStatus,
    accumulator_status,
    calculate_goal_shortfall,
    settlement_from_score,
)


def payload(**overrides):
    data = {
        "legs": [
            {"selection": "Arsenal & Yes", "market": "Result/BTTS", "event": "Arsenal v Chelsea",
             "selected_outcome": "home_win", "home_team": "Arsenal", "away_team": "Chelsea",
             "odds": "3.25", "home_score": 2, "away_score": 1, "settlement_status": "won"},
            {"selection": "Draw & Yes", "market": "Result/BTTS", "event": "Liverpool v Everton",
             "selected_outcome": "draw", "home_team": "Liverpool", "away_team": "Everton",
             "odds": "4.0", "home_score": 1, "away_score": 2, "settlement_status": "lost"},
        ],
        "stake": "6", "return": "0", "settlement_status": "lost", "confidence": 0.9,
    }
    data.update(overrides)
    return data


def test_valid_accumulator_and_decimal_values():
    parsed = ParsedAccumulator.model_validate(payload())
    assert parsed.stake == Decimal("6")
    assert parsed.legs[0].odds == Decimal("3.25")


def test_rejects_single_leg():
    with pytest.raises(ValidationError):
        ParsedAccumulator.model_validate(payload(legs=payload()["legs"][:1]))


@pytest.mark.parametrize(("outcome", "home", "away", "expected"), [
    (SelectedOutcome.HOME_WIN, 2, 1, 0), (SelectedOutcome.HOME_WIN, 10, 2, 0),
    (SelectedOutcome.HOME_WIN, 1, 1, -1), (SelectedOutcome.HOME_WIN, 0, 0, -3),
    (SelectedOutcome.HOME_WIN, 2, 0, -1), (SelectedOutcome.HOME_WIN, 0, 2, -3),
    (SelectedOutcome.DRAW, 1, 1, 0), (SelectedOutcome.DRAW, 4, 4, 0),
    (SelectedOutcome.DRAW, 1, 2, -1), (SelectedOutcome.DRAW, 0, 0, -2),
    (SelectedOutcome.DRAW, 3, 1, -2),
])
def test_goal_shortfall(outcome, home, away, expected):
    assert calculate_goal_shortfall(outcome, home, away) == expected


@pytest.mark.parametrize(("outcome", "home", "away", "expected"), [
    (SelectedOutcome.HOME_WIN, 2, 1, SettlementStatus.WON),
    (SelectedOutcome.HOME_WIN, 2, 0, SettlementStatus.LOST),
    (SelectedOutcome.AWAY_WIN, 1, 3, SettlementStatus.WON),
    (SelectedOutcome.DRAW, 2, 2, SettlementStatus.WON),
    (SelectedOutcome.DRAW, 0, 0, SettlementStatus.LOST),
    (SelectedOutcome.DRAW, None, None, None),
])
def test_settlement_from_result_and_btts(outcome, home, away, expected):
    assert settlement_from_score(outcome, home, away) == expected


def test_accumulator_status_uses_all_legs():
    assert accumulator_status([SettlementStatus.WON, SettlementStatus.LOST]) == SettlementStatus.LOST
    assert accumulator_status([SettlementStatus.WON, SettlementStatus.PENDING]) == SettlementStatus.PENDING
    assert accumulator_status([SettlementStatus.WON, SettlementStatus.VOID]) == SettlementStatus.WON
