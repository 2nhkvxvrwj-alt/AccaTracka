from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pandas as pd

from acca_app.models import BetRecord, LegRecord, SettlementStatus


def syndicate_overall(bets: list[BetRecord]) -> dict[str, float | int]:
    included = [bet for bet in bets if not bet.excluded]
    settled = [bet for bet in included if bet.settlement_status != SettlementStatus.PENDING]
    stakes = sum((bet.stake for bet in settled), Decimal("0"))
    returns = sum((bet.return_amount for bet in settled), Decimal("0"))
    profit = returns - stakes
    won = sum(bet.settlement_status == SettlementStatus.WON for bet in settled)
    return {
        "accas": len(included), "settled": len(settled), "won": won,
        "stakes": float(stakes), "returns": float(returns), "profit": float(profit),
        "roi": float(profit / stakes * 100) if stakes else 0.0,
        "strike_rate": won / len(settled) * 100 if settled else 0.0,
    }


def _member_legs(bets: list[BetRecord]) -> dict[str, list[tuple[BetRecord, LegRecord]]]:
    grouped: dict[str, list[tuple[BetRecord, LegRecord]]] = defaultdict(list)
    for bet in sorted(bets, key=lambda item: item.timestamp):
        if not bet.excluded:
            for leg in bet.legs:
                grouped[leg.member].append((bet, leg))
    return grouped


def _streaks(legs: list[LegRecord]) -> tuple[int, int, int]:
    current, longest_win, longest_loss = 0, 0, 0
    for leg in legs:
        if leg.settlement_status == SettlementStatus.WON:
            current = current + 1 if current >= 0 else 1
            longest_win = max(longest_win, current)
        elif leg.settlement_status == SettlementStatus.LOST:
            current = current - 1 if current <= 0 else -1
            longest_loss = max(longest_loss, abs(current))
    return current, longest_win, longest_loss


def member_leaderboard(bets: list[BetRecord], members: list[str] | None = None) -> pd.DataFrame:
    grouped = _member_legs(bets)
    names = sorted(set(members or []) | set(grouped))
    rows = []
    for member in names:
        legs = [leg for _, leg in grouped.get(member, [])]
        settled = [leg for leg in legs if leg.settlement_status != SettlementStatus.PENDING]
        decisions = [leg for leg in settled if leg.settlement_status in (SettlementStatus.WON, SettlementStatus.LOST)]
        wins = [leg for leg in decisions if leg.settlement_status == SettlementStatus.WON]
        losses = [leg for leg in decisions if leg.settlement_status == SettlementStatus.LOST]
        voids = [leg for leg in settled if leg.settlement_status == SettlementStatus.VOID]
        pending = [leg for leg in legs if leg.settlement_status == SettlementStatus.PENDING]
        unit_returns = sum((leg.odds if leg.settlement_status == SettlementStatus.WON else
                            Decimal("1") if leg.settlement_status == SettlementStatus.VOID else Decimal("0")
                            for leg in settled), Decimal("0"))
        unit_profit = unit_returns - Decimal(len(settled))
        shortfalls = [leg.goal_shortfall for leg in decisions if leg.goal_shortfall is not None]
        losing_shortfalls = [leg.goal_shortfall for leg in losses if leg.goal_shortfall is not None]
        current, longest_win, longest_loss = _streaks(decisions)
        rows.append({
            "Member": member, "Selections": len(legs), "Settled": len(settled),
            "Won": len(wins), "Lost": len(losses), "Void": len(voids), "Pending": len(pending),
            "Strike rate %": len(wins) / len(decisions) * 100 if decisions else 0.0,
            "Unit stakes": len(settled), "Unit returns": float(unit_returns),
            "Unit P/L": float(unit_profit),
            "Unit ROI %": float(unit_profit / len(settled) * 100) if settled else 0.0,
            "Average odds": sum((float(leg.odds) for leg in decisions), 0.0) / len(decisions) if decisions else 0.0,
            "Implied wins": sum((1 / float(leg.odds) for leg in decisions), 0.0),
            "Goal shortfall": sum(shortfalls) if shortfalls else 0,
            "Average shortfall": sum(shortfalls) / len(shortfalls) if shortfalls else 0.0,
            "Losing avg shortfall": sum(losing_shortfalls) / len(losing_shortfalls) if losing_shortfalls else 0.0,
            "Current streak": current, "Longest win streak": longest_win, "Longest loss streak": longest_loss,
        })
    columns = ["Member", "Selections", "Settled", "Won", "Lost", "Void", "Pending", "Strike rate %",
               "Unit stakes", "Unit returns", "Unit P/L", "Unit ROI %", "Average odds", "Implied wins",
               "Goal shortfall", "Average shortfall", "Losing avg shortfall", "Current streak",
               "Longest win streak", "Longest loss streak"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(["Unit P/L", "Unit ROI %"], ascending=False).reset_index(drop=True)


def weekly_summary(bets: list[BetRecord]) -> pd.DataFrame:
    rows = []
    for bet in bets:
        if bet.excluded or bet.settlement_status == SettlementStatus.PENDING:
            continue
        lost_legs = sum(leg.settlement_status == SettlementStatus.LOST for leg in bet.legs)
        rows.append({"Week": bet.timestamp.strftime("%G-W%V"), "Stake": float(bet.stake),
                     "Return": float(bet.return_amount), "Won": int(bet.settlement_status == SettlementStatus.WON),
                     "Near miss": int(lost_legs == 1), "Accas": 1})
    if not rows:
        return pd.DataFrame(columns=["Week", "Accas", "Won", "Near misses", "Stakes", "Returns", "P/L", "ROI %"])
    frame = pd.DataFrame(rows).groupby("Week", as_index=False).agg(
        Accas=("Accas", "sum"), Won=("Won", "sum"), Near_misses=("Near miss", "sum"),
        Stakes=("Stake", "sum"), Returns=("Return", "sum"))
    frame = frame.rename(columns={"Near_misses": "Near misses"})
    frame["P/L"] = frame["Returns"] - frame["Stakes"]
    frame["ROI %"] = frame["P/L"].div(frame["Stakes"]).mul(100).fillna(0)
    return frame.sort_values("Week", ascending=False)


def selection_history(bets: list[BetRecord], members: list[str]) -> pd.DataFrame:
    rows = []
    for bet in sorted(bets, key=lambda item: item.timestamp, reverse=True):
        row: dict[str, object] = {"Date": bet.timestamp.strftime("%d %b %Y"), "Acca": f"#{bet.id}"}
        by_member = {leg.member: leg for leg in bet.legs}
        for member in members:
            leg = by_member.get(member)
            if not leg:
                row[member] = "-"
                continue
            profit = leg.unit_profit
            score = "-" if leg.home_score is None else f"{leg.home_score}-{leg.away_score}"
            unit = "pending" if profit is None else f"{float(profit):+.2f}u"
            shortfall = "-" if leg.goal_shortfall is None else str(leg.goal_shortfall)
            row[member] = f"{leg.selection} | {leg.odds} | {score} | {leg.settlement_status.value.title()} | {unit} | GS {shortfall}"
        row["Excluded"] = bet.excluded
        rows.append(row)
    return pd.DataFrame(rows, columns=["Date", "Acca", *members, "Excluded"])


def cumulative_member_profit(bets: list[BetRecord]) -> pd.DataFrame:
    totals: dict[str, float] = defaultdict(float)
    rows = []
    for bet in sorted(bets, key=lambda item: item.timestamp):
        if bet.excluded:
            continue
        for leg in bet.legs:
            if leg.unit_profit is not None:
                totals[leg.member] += float(leg.unit_profit)
                rows.append({"Date": bet.timestamp, "Member": leg.member, "Cumulative unit P/L": totals[leg.member]})
    return pd.DataFrame(rows)


def cumulative_syndicate_profit(bets: list[BetRecord]) -> pd.DataFrame:
    total, rows = 0.0, []
    for bet in sorted(bets, key=lambda item: item.timestamp):
        if bet.excluded or bet.settlement_status == SettlementStatus.PENDING:
            continue
        total += float(bet.return_amount - bet.stake)
        rows.append({"Date": bet.timestamp, "Cumulative P/L": total})
    return pd.DataFrame(rows)
