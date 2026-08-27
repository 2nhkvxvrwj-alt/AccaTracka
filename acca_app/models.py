from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SettlementStatus(StrEnum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    VOID = "void"


class SelectedOutcome(StrEnum):
    HOME_WIN = "home_win"
    AWAY_WIN = "away_win"
    DRAW = "draw"


class ScoreOrigin(StrEnum):
    UNKNOWN = "unknown"
    RECEIPT = "receipt"
    WEB = "web"
    MANUAL = "manual"


def settlement_from_score(
    outcome: SelectedOutcome,
    home_score: int | None,
    away_score: int | None,
) -> SettlementStatus | None:
    if home_score is None or away_score is None:
        return None
    both_scored = home_score > 0 and away_score > 0
    if outcome == SelectedOutcome.DRAW:
        selected_result = home_score == away_score
    elif outcome == SelectedOutcome.HOME_WIN:
        selected_result = home_score > away_score
    else:
        selected_result = away_score > home_score
    return SettlementStatus.WON if selected_result and both_scored else SettlementStatus.LOST


def accumulator_status(statuses: list[SettlementStatus]) -> SettlementStatus:
    if any(status == SettlementStatus.LOST for status in statuses):
        return SettlementStatus.LOST
    if any(status == SettlementStatus.PENDING for status in statuses):
        return SettlementStatus.PENDING
    if any(status == SettlementStatus.WON for status in statuses):
        return SettlementStatus.WON
    return SettlementStatus.VOID


def calculate_goal_shortfall(outcome: SelectedOutcome, home_score: int | None, away_score: int | None) -> int | None:
    if home_score is None or away_score is None:
        return None
    if home_score < 0 or away_score < 0:
        raise ValueError("Scores cannot be negative")
    if outcome == SelectedOutcome.DRAW:
        if home_score == away_score and home_score >= 1:
            return 0
        adjusted_home, adjusted_away = max(home_score, 1), max(away_score, 1)
        additions = adjusted_home - home_score + adjusted_away - away_score
        return -(additions + abs(adjusted_home - adjusted_away))
    selected = home_score if outcome == SelectedOutcome.HOME_WIN else away_score
    opponent = away_score if outcome == SelectedOutcome.HOME_WIN else home_score
    if selected > opponent and opponent >= 1:
        return 0
    opponent_additions = max(0, 1 - opponent)
    selected_additions = max(0, opponent + opponent_additions + 1 - selected)
    return -(opponent_additions + selected_additions)


class ParsedLeg(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    selection: str = Field(min_length=1)
    market: str = Field(min_length=1)
    event: str = Field(min_length=1)
    selected_outcome: SelectedOutcome
    home_team: str = Field(min_length=1)
    away_team: str = Field(min_length=1)
    fixture_date: date | None = None
    odds: Decimal = Field(gt=1)
    home_score: int | None = Field(default=None, ge=0)
    away_score: int | None = Field(default=None, ge=0)
    settlement_status: SettlementStatus
    score_origin: ScoreOrigin = ScoreOrigin.UNKNOWN
    score_source_title: str | None = None
    score_source_url: str | None = None
    manual_override: bool = False

    @property
    def goal_shortfall(self) -> int | None:
        return calculate_goal_shortfall(self.selected_outcome, self.home_score, self.away_score)


class ParsedAccumulator(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    legs: list[ParsedLeg] = Field(min_length=2)
    stake: Decimal | None = Field(default=None, gt=0)
    return_amount: Decimal | None = Field(default=None, alias="return", ge=0)
    settlement_status: SettlementStatus
    placed_at: datetime | None = None
    bookmaker: str | None = None
    reference: str | None = None
    raw_text: str = ""
    confidence: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_settlement(self) -> "ParsedAccumulator":
        if self.settlement_status == SettlementStatus.LOST and self.return_amount not in (None, 0):
            raise ValueError("A lost accumulator must have a zero return")
        if self.settlement_status == SettlementStatus.WON and self.return_amount is not None and self.return_amount <= 0:
            raise ValueError("A won accumulator must have a positive return")
        return self

    def validate_ready_to_save(self) -> None:
        missing = []
        if self.stake is None:
            missing.append("stake")
        if self.return_amount is None:
            missing.append("return")
        if missing:
            raise ValueError(f"Receipt is missing required financial fields: {', '.join(missing)}")
        if self.settlement_status == SettlementStatus.LOST and self.return_amount != 0:
            raise ValueError("A lost accumulator must have a zero return")
        if self.settlement_status == SettlementStatus.WON and self.return_amount <= 0:
            raise ValueError("Enter the actual return for a winning accumulator")


class LegRecord(BaseModel):
    id: int
    member: str
    selection: str
    market: str
    event: str
    selected_outcome: SelectedOutcome
    home_team: str
    away_team: str
    fixture_date: date | None = None
    odds: Decimal
    home_score: int | None = None
    away_score: int | None = None
    settlement_status: SettlementStatus
    goal_shortfall: int | None = None
    manual_override: bool = False
    score_origin: ScoreOrigin = ScoreOrigin.UNKNOWN
    score_source_title: str | None = None
    score_source_url: str | None = None

    @property
    def unit_profit(self) -> Decimal | None:
        if self.settlement_status == SettlementStatus.PENDING:
            return None
        if self.settlement_status == SettlementStatus.WON:
            return self.odds - Decimal("1")
        if self.settlement_status == SettlementStatus.LOST:
            return Decimal("-1")
        return Decimal("0")


class BetRecord(BaseModel):
    id: int
    stake: Decimal
    return_amount: Decimal
    settlement_status: SettlementStatus
    excluded: bool
    timestamp: datetime
    created_at: datetime
    updated_at: datetime
    legs: list[LegRecord]
    bookmaker: str | None = None
    reference: str | None = None
    confidence: float | None = None
    source_hash: str | None = None

    @property
    def fixture_date(self) -> date:
        """Earliest leg fixture date, falling back to the receipt timestamp."""
        dates = [leg.fixture_date for leg in self.legs if leg.fixture_date is not None]
        return min(dates) if dates else self.timestamp.date()


class ReceiptRecord(BaseModel):
    id: int
    bet_id: int
    filename: str
    mime_type: str
    data: bytes


class AuditRecord(BaseModel):
    id: int
    bet_id: int
    changed_at: datetime
    action: str
    before_json: str | None = None
    after_json: str
    note: str | None = None
