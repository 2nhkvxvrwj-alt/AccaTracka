from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from acca_app.models import BetRecord, ParsedAccumulator, SettlementStatus


def money(value: Decimal | float, currency: str = "GBP") -> str:
    symbol = {"GBP": "\u00a3", "EUR": "\u20ac", "USD": "$"}.get(currency, f"{currency} ")
    return f"{symbol}{float(value):,.2f}"


def render_legs(legs, show_member: bool = True) -> None:
    rows = []
    for leg in legs:
        score = "-" if leg.home_score is None else f"{leg.home_score}-{leg.away_score}"
        row = {
            "Selection": leg.selection, "Event": leg.event, "Odds": f"{leg.odds}",
            "Fixture date": leg.fixture_date.isoformat() if leg.fixture_date else "-",
            "Score": score, "Result": leg.settlement_status.value.title(),
            "Goal shortfall": leg.goal_shortfall, "Score source": leg.score_origin.value.title(),
        }
        if show_member:
            row = {"Member": leg.member, **row}
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def render_parsed(parsed: ParsedAccumulator, currency: str) -> None:
    st.subheader("Parsed accumulator")
    first, second, third = st.columns(3)
    first.metric("Stake", money(parsed.stake, currency) if parsed.stake is not None else "Not shown")
    second.metric("Return", money(parsed.return_amount, currency) if parsed.return_amount is not None else "Not shown")
    third.metric("Settlement", parsed.settlement_status.value.title())
    render_legs(parsed.legs, show_member=False)
    if parsed.warnings:
        st.warning(" | ".join(parsed.warnings))
    if parsed.confidence < 0.6:
        st.warning("AI extraction confidence is low. Review every field before saving.")
    st.caption(f"AI extraction confidence: {parsed.confidence:.0%}")


def render_bet(bet: BetRecord, currency: str, expanded: bool = False) -> None:
    profit = bet.return_amount - bet.stake
    label = f"#{bet.id} | {bet.fixture_date:%d %b %Y} | {bet.settlement_status.value.title()} | {money(profit, currency)} P/L"
    with st.expander(label, expanded=expanded):
        cols = st.columns(4)
        cols[0].metric("Stake", money(bet.stake, currency))
        cols[1].metric("Return", money(bet.return_amount, currency))
        cols[2].metric("P/L", money(profit, currency))
        cols[3].metric("Settlement", bet.settlement_status.value.title())
        render_legs(bet.legs)
        for leg in bet.legs:
            if leg.score_source_url:
                label = leg.score_source_title or f"Score source for {leg.event}"
                st.markdown(f"[{label}]({leg.score_source_url})")
        details = [value for value in (bet.bookmaker, bet.reference) if value]
        if details:
            st.caption(" | ".join(details))


def style_selection_history(frame: pd.DataFrame):
    def colour(value):
        text = str(value)
        if "| Won |" in text:
            return "background-color: #dcefe5; color: #124b2c"
        if "| Lost |" in text:
            return "background-color: #f7dddd; color: #6d1f1f"
        if "| Pending |" in text:
            return "background-color: #fff0c7; color: #6b4d00"
        if "| Void |" in text:
            return "background-color: #e7e9e8; color: #3f4945"
        return ""
    member_columns = [column for column in frame.columns if column not in ("Date", "Acca", "Excluded")]
    return frame.style.map(colour, subset=member_columns)
