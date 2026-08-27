from __future__ import annotations

import hmac
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import streamlit as st

from acca_app.analytics import (
    cumulative_member_profit,
    cumulative_syndicate_profit,
    member_leaderboard,
    selection_history,
    syndicate_overall,
    weekly_summary,
)
from acca_app.config import get_settings
from acca_app.database import DuplicateBetError, Repository
from acca_app.files import combined_source_hash, receipt_to_images
from acca_app.models import (
    BetRecord,
    ScoreOrigin,
    SelectedOutcome,
    SettlementStatus,
    accumulator_status,
    settlement_from_score,
)
from acca_app.parser import ReceiptParser
from acca_app.ui import money, render_bet, render_parsed, style_selection_history


st.set_page_config(page_title="Acca Syndicate", layout="wide")
st.markdown("""
<style>
    .block-container {max-width: 1380px; padding-top: 1.5rem;}
    [data-testid="stMetric"] {border-left: 3px solid #14866D; padding-left: 0.8rem;}
    h1, h2, h3 {letter-spacing: 0;}
</style>
""", unsafe_allow_html=True)

settings = get_settings()
repository = Repository(settings.database_path)


def secret(name: str, fallback: str = "") -> str:
    try:
        return str(st.secrets.get(name, fallback))
    except Exception:
        return fallback


def authenticate() -> str | None:
    admin_password = secret("ADMIN_PASSWORD", settings.admin_password)
    viewer_password = secret("VIEWER_PASSWORD", settings.viewer_password)
    if not admin_password and not viewer_password:
        st.session_state.site_role = "admin"
        return "admin"
    if role := st.session_state.get("site_role"):
        return role
    st.title("Acca Syndicate")
    with st.form("site_login"):
        supplied = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            if admin_password and hmac.compare_digest(supplied, admin_password):
                st.session_state.site_role = "admin"
                st.rerun()
            if viewer_password and hmac.compare_digest(supplied, viewer_password):
                st.session_state.site_role = "viewer"
                st.rerun()
            st.error("Incorrect password")
    return None


def parse_odds(value: str) -> Decimal:
    text = value.strip()
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            odds = Decimal(numerator) / Decimal(denominator) + Decimal("1")
        else:
            odds = Decimal(text)
    except (InvalidOperation, ZeroDivisionError) as exc:
        raise ValueError(f"Invalid odds: {value}") from exc
    if odds <= 1:
        raise ValueError("Decimal odds must be greater than 1")
    return odds


def parse_score(value: str, label: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    try:
        score = int(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be a whole number") from exc
    if score < 0:
        raise ValueError(f"{label} cannot be negative")
    return score


def parse_money(value: str, label: str, allow_zero: bool) -> Decimal | None:
    text = value.strip().replace("£", "").replace(",", "")
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a valid amount") from exc
    if amount < 0 or (amount == 0 and not allow_zero):
        qualifier = "zero or more" if allow_zero else "greater than zero"
        raise ValueError(f"{label} must be {qualifier}")
    return amount


def parse_fixture_date(value: str, label: str = "Fixture date") -> date | None:
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD") from exc


def dashboard() -> None:
    bets = repository.list_bets()
    included = [bet for bet in bets if not bet.excluded]
    summary = syndicate_overall(bets)
    cols = st.columns(5)
    cols[0].metric("Syndicate P/L", money(summary["profit"], settings.app_currency))
    cols[1].metric("Settled stakes", money(summary["stakes"], settings.app_currency))
    cols[2].metric("ROI", f"{summary['roi']:.1f}%")
    cols[3].metric("Strike rate", f"{summary['strike_rate']:.1f}%")
    cols[4].metric("Accas", summary["accas"])
    st.subheader("Latest accumulator")
    if included:
        render_bet(included[0], settings.app_currency, expanded=True)
    else:
        st.info("No included accumulators yet.")
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Cumulative syndicate P/L")
        chart = cumulative_syndicate_profit(bets)
        if chart.empty:
            st.caption("No settled accumulators.")
        else:
            st.line_chart(chart, x="Date", y="Cumulative P/L")
    with right:
        st.subheader("Weekly results")
        st.dataframe(weekly_summary(bets), hide_index=True, width="stretch")


def members_view() -> None:
    bets, members = repository.list_bets(), repository.list_members()
    board = member_leaderboard(bets, members)
    st.subheader("Member performance")
    st.dataframe(board, hide_index=True, width="stretch", column_config={
        "Strike rate %": st.column_config.NumberColumn(format="%.1f%%"),
        "Unit returns": st.column_config.NumberColumn(format="%.2fu"),
        "Unit P/L": st.column_config.NumberColumn(format="%+.2fu"),
        "Unit ROI %": st.column_config.NumberColumn(format="%+.1f%%"),
        "Average odds": st.column_config.NumberColumn(format="%.2f"),
        "Implied wins": st.column_config.NumberColumn(format="%.2f"),
        "Average shortfall": st.column_config.NumberColumn(format="%.2f"),
        "Losing avg shortfall": st.column_config.NumberColumn(format="%.2f"),
    })
    st.subheader("Cumulative unit P/L")
    chart = cumulative_member_profit(bets)
    if chart.empty:
        st.caption("No settled member selections.")
    else:
        st.line_chart(chart, x="Date", y="Cumulative unit P/L", color="Member")


def selection_history_view() -> None:
    frame = selection_history(repository.list_bets(), repository.list_members())
    st.subheader("Selection history")
    if frame.empty:
        st.info("No selections saved yet.")
    else:
        st.dataframe(style_selection_history(frame), hide_index=True, width="stretch")


def display_receipts(bet_id: int) -> None:
    receipts = repository.list_receipts(bet_id)
    if not receipts:
        st.caption("No receipt images retained for this accumulator.")
    for receipt in receipts:
        st.caption(receipt.filename)
        if receipt.mime_type == "application/pdf":
            for image in receipt_to_images(receipt.data, receipt.mime_type):
                st.image(image, width=700)
            st.download_button("Download PDF", receipt.data, file_name=receipt.filename, mime=receipt.mime_type,
                               key=f"download_{receipt.id}")
        else:
            st.image(receipt.data, width=700)


def bet_history_view() -> None:
    bets = repository.list_bets()
    included_tab, excluded_tab, receipts_tab = st.tabs(["Included", "Excluded", "Receipts"])
    with included_tab:
        for bet in (item for item in bets if not item.excluded):
            render_bet(bet, settings.app_currency)
    with excluded_tab:
        for bet in (item for item in bets if item.excluded):
            render_bet(bet, settings.app_currency)
    with receipts_tab:
        if bets:
            chosen = st.selectbox("Accumulator", bets, format_func=lambda bet: f"#{bet.id} | {bet.fixture_date:%d %b %Y}")
            display_receipts(chosen.id)
        else:
            st.caption("No receipts saved yet.")


def member_management() -> list[str]:
    with st.expander("Manage syndicate members"):
        current = repository.list_members()
        st.dataframe({"Syndicate member": current}, hide_index=True, width="stretch")
        with st.form("new_member", clear_on_submit=True):
            name = st.text_input("Member name")
            if st.form_submit_button("Add member"):
                try:
                    repository.add_member(name)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    return repository.list_members()


def upload_view() -> None:
    members = member_management()
    if not members:
        st.warning("Add syndicate members before uploading a receipt.")
        return
    uploaded = st.file_uploader(
        "Upload all images/pages for one accumulator", type=["png", "jpg", "jpeg", "webp", "pdf"],
        accept_multiple_files=True,
    )
    if not uploaded:
        return
    if len(uploaded) > 8:
        st.error("Upload no more than eight files for one accumulator.")
        return
    sources = [(item.name, item.type, item.getvalue()) for item in uploaded]
    digest = combined_source_hash(sources)
    if st.session_state.get("receipt_hash") != digest:
        st.session_state.pop("parsed_receipt", None)
        st.session_state.receipt_hash = digest
        st.session_state.receipt_sources = sources
    image_files = [item for item in uploaded if item.type != "application/pdf"]
    if image_files:
        st.image([item.getvalue() for item in image_files], caption=[item.name for item in image_files], width=240)
    for item in uploaded:
        if item.type == "application/pdf":
            st.caption(f"PDF: {item.name}")
    api_key = secret("OPENAI_API_KEY", settings.openai_api_key)
    if not api_key:
        st.error("AI parsing is not configured. Add OPENAI_API_KEY and restart the app.")
        return
    if "parsed_receipt" not in st.session_state:
        if st.button("Parse receipt", type="primary"):
            try:
                with st.spinner("Reading receipt images..."):
                    images = []
                    for _, mime_type, data in sources:
                        images.extend(receipt_to_images(data, mime_type))
                    if len(images) > 12:
                        raise ValueError("The combined upload contains more than 12 pages/images")
                    st.session_state.parsed_receipt = ReceiptParser(api_key, settings.openai_model).parse(images)
                st.rerun()
            except Exception as exc:
                st.error(f"Could not parse this receipt: {exc}")
        return
    parsed = st.session_state.parsed_receipt
    render_parsed(parsed, settings.app_currency)
    if len(parsed.legs) != len(members):
        st.warning(f"Parsed {len(parsed.legs)} legs but there are {len(members)} members. Every member must own one leg.")
    with st.form("allocation"):
        st.subheader("Review and allocate")
        finance_cols = st.columns(2)
        stake_text = finance_cols[0].text_input(
            "Stake",
            value="" if parsed.stake is None else str(parsed.stake),
            placeholder="Enter the total accumulator stake",
        )
        return_text = finance_cols[1].text_input(
            "Return",
            value="" if parsed.return_amount is None else str(parsed.return_amount),
            placeholder="Enter the actual or potential return",
        )
        edited_legs = []
        status_values = list(SettlementStatus)
        for index, leg in enumerate(parsed.legs):
            st.markdown(f"#### Selection {index + 1}: {leg.selection}")
            owner_col, date_col, odds_col, home_col, away_col = st.columns([2, 1, 1, 1, 1])
            owner = owner_col.selectbox(
                "Member",
                members,
                index=index % len(members),
                key=f"owner_{index}",
            )
            fixture_date = date_col.text_input(
                "Fixture date",
                value=leg.fixture_date.isoformat() if leg.fixture_date else "",
                placeholder="YYYY-MM-DD",
                key=f"new_fixture_date_{index}",
            )
            odds = odds_col.text_input("Odds", value=str(leg.odds), key=f"new_odds_{index}")
            home_score = home_col.text_input(
                f"{leg.home_team} score",
                value="" if leg.home_score is None else str(leg.home_score),
                key=f"new_home_score_{index}",
            )
            away_score = away_col.text_input(
                f"{leg.away_team} score",
                value="" if leg.away_score is None else str(leg.away_score),
                key=f"new_away_score_{index}",
            )
            result_col, override_col = st.columns([2, 1])
            result_override = result_col.selectbox(
                "Result override",
                status_values,
                index=status_values.index(leg.settlement_status),
                format_func=lambda value: value.value.title(),
                key=f"new_status_{index}",
            )
            use_override = override_col.toggle(
                "Use result override",
                value=leg.manual_override,
                key=f"new_override_{index}",
            )
            calculated = settlement_from_score(leg.selected_outcome, leg.home_score, leg.away_score)
            if calculated:
                st.caption(f"Current score calculates as {calculated.value.title()}.")
            else:
                st.caption("No complete score is available; the extracted result is retained unless overridden.")
            if leg.score_origin == ScoreOrigin.WEB and leg.score_source_url:
                source_label = leg.score_source_title or "Online result source"
                st.markdown(f"Score found online: [{source_label}]({leg.score_source_url})")
            elif leg.score_origin == ScoreOrigin.RECEIPT:
                st.caption("Score read from the uploaded receipt.")
            edited_legs.append(
                (owner, fixture_date, odds, home_score, away_score, result_override, use_override)
            )
        excluded = st.toggle("Exclude this bet from the syndicate?", value=False)
        if st.form_submit_button("Save accumulator", type="primary", width="stretch"):
            try:
                updated = parsed.model_copy(deep=True)
                updated.stake = parse_money(stake_text, "Stake", allow_zero=False)
                updated.return_amount = parse_money(return_text, "Return", allow_zero=True)
                allocations = []
                for target, values in zip(updated.legs, edited_legs, strict=True):
                    owner, fixture_date, odds, home_score, away_score, result_override, use_override = values
                    allocations.append(owner)
                    target.fixture_date = parse_fixture_date(fixture_date)
                    entered_home = parse_score(home_score, f"{target.home_team} score")
                    entered_away = parse_score(away_score, f"{target.away_team} score")
                    if (entered_home is None) != (entered_away is None):
                        raise ValueError(f"Enter both scores or leave both blank for {target.event}")
                    if (entered_home, entered_away) != (target.home_score, target.away_score):
                        target.score_origin = ScoreOrigin.MANUAL
                        target.score_source_title = None
                        target.score_source_url = None
                    target.home_score, target.away_score = entered_home, entered_away
                    target.odds = parse_odds(odds)
                    calculated = settlement_from_score(
                        target.selected_outcome, target.home_score, target.away_score
                    )
                    target.manual_override = use_override
                    target.settlement_status = (
                        result_override if use_override else calculated or target.settlement_status
                    )
                updated.settlement_status = accumulator_status(
                    [leg.settlement_status for leg in updated.legs]
                )
                if updated.settlement_status == SettlementStatus.LOST and updated.return_amount is None:
                    updated.return_amount = Decimal("0")
                if len(parsed.legs) != len(members) or set(allocations) != set(members):
                    raise ValueError("Allocate every member exactly once")
                bet_id = repository.add_bet(
                    updated, allocations, excluded, digest, st.session_state.receipt_sources
                )
                for key in ("parsed_receipt", "receipt_hash", "receipt_sources"):
                    st.session_state.pop(key, None)
                st.success(f"Accumulator #{bet_id} saved.")
            except (ValueError, DuplicateBetError) as exc:
                st.error(str(exc))


def edit_bet_form(bet: BetRecord) -> None:
    members = repository.list_members()
    with st.form(f"edit_bet_{bet.id}"):
        col1, col2, col3, col4 = st.columns(4)
        stake = col1.number_input("Stake", min_value=0.01, value=float(bet.stake), step=0.01)
        returned = col2.number_input("Return", min_value=0.0, value=float(bet.return_amount), step=0.01)
        status_values = list(SettlementStatus)
        status = col3.selectbox("Accumulator status", status_values, index=status_values.index(bet.settlement_status),
                                format_func=lambda value: value.value.title())
        excluded = col4.toggle("Excluded", value=bet.excluded)
        date_col, time_col = st.columns(2)
        bet_date = date_col.date_input("Receipt date", value=bet.timestamp.date())
        bet_time = time_col.time_input("Receipt time", value=bet.timestamp.time().replace(microsecond=0))
        edited_legs = []
        for index, original in enumerate(bet.legs):
            st.markdown(f"#### Selection {index + 1}")
            owner_col, selection_col, odds_col, result_col = st.columns([1, 2, 1, 1])
            owner = owner_col.selectbox("Member", members, index=members.index(original.member), key=f"member_{bet.id}_{original.id}")
            selection = selection_col.text_input("Selection", value=original.selection, key=f"selection_{bet.id}_{original.id}")
            odds = odds_col.text_input("Odds", value=str(original.odds), key=f"odds_{bet.id}_{original.id}")
            leg_status = result_col.selectbox("Result", status_values, index=status_values.index(original.settlement_status),
                                              format_func=lambda value: value.value.title(), key=f"status_{bet.id}_{original.id}")
            outcome_values = list(SelectedOutcome)
            event_col, outcome_col, home_col, away_col = st.columns([2, 1, 1, 1])
            event = event_col.text_input("Event", value=original.event, key=f"event_{bet.id}_{original.id}")
            outcome = outcome_col.selectbox("Selected outcome", outcome_values,
                                            index=outcome_values.index(original.selected_outcome),
                                            format_func=lambda value: value.value.replace("_", " ").title(),
                                            key=f"outcome_{bet.id}_{original.id}")
            home_score = home_col.text_input("Home score", value="" if original.home_score is None else str(original.home_score),
                                             key=f"home_score_{bet.id}_{original.id}")
            away_score = away_col.text_input("Away score", value="" if original.away_score is None else str(original.away_score),
                                             key=f"away_score_{bet.id}_{original.id}")
            team_col1, team_col2, market_col, fixture_date_col = st.columns([1, 1, 2, 1])
            home_team = team_col1.text_input("Home team", value=original.home_team, key=f"home_team_{bet.id}_{original.id}")
            away_team = team_col2.text_input("Away team", value=original.away_team, key=f"away_team_{bet.id}_{original.id}")
            market = market_col.text_input("Market", value=original.market, key=f"market_{bet.id}_{original.id}")
            fixture_date = fixture_date_col.text_input(
                "Fixture date",
                value=original.fixture_date.isoformat() if original.fixture_date else "",
                placeholder="YYYY-MM-DD",
                key=f"fixture_date_{bet.id}_{original.id}",
            )
            edited_legs.append((original, owner, selection, odds, leg_status, event, outcome,
                                home_score, away_score, home_team, away_team, market, fixture_date))
        note = st.text_input("Amendment note (optional)")
        if st.form_submit_button("Save amendments", type="primary", width="stretch"):
            try:
                updated = bet.model_copy(deep=True)
                updated.stake, updated.return_amount = Decimal(str(stake)), Decimal(str(returned))
                updated.settlement_status, updated.excluded = status, excluded
                updated.timestamp = datetime.combine(bet_date, bet_time, tzinfo=bet.timestamp.tzinfo)
                for target, values in zip(updated.legs, edited_legs, strict=True):
                    _, owner, selection, odds, leg_status, event, outcome, home_score, away_score, home_team, away_team, market, fixture_date = values
                    target.member, target.selection, target.odds = owner, selection.strip(), parse_odds(odds)
                    target.settlement_status, target.event, target.selected_outcome = leg_status, event.strip(), outcome
                    entered_home = parse_score(home_score, "Home score")
                    entered_away = parse_score(away_score, "Away score")
                    if (entered_home is None) != (entered_away is None):
                        raise ValueError(f"Enter both scores or leave both blank for {event}")
                    score_changed = (entered_home, entered_away) != (original.home_score, original.away_score)
                    target.home_score, target.away_score = entered_home, entered_away
                    if score_changed:
                        target.score_origin = ScoreOrigin.MANUAL
                        target.score_source_title = None
                        target.score_source_url = None
                    calculated = settlement_from_score(outcome, entered_home, entered_away)
                    status_changed = leg_status != original.settlement_status
                    target.manual_override = original.manual_override or status_changed
                    if calculated is not None and not target.manual_override:
                        target.settlement_status = calculated
                    target.home_team, target.away_team, target.market = home_team.strip(), away_team.strip(), market.strip()
                    target.fixture_date = parse_fixture_date(fixture_date)
                if set(leg.member for leg in updated.legs) != set(members):
                    raise ValueError("Allocate every member exactly once")
                repository.update_bet(updated, note)
                st.success("Amendments saved and analytics recalculated.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def manage_bets_view() -> None:
    bets = repository.list_bets()
    if not bets:
        st.info("No accumulators available to amend.")
        return
    chosen = st.selectbox("Accumulator to amend", bets, format_func=lambda bet: f"#{bet.id} | {bet.fixture_date:%d %b %Y} | {bet.settlement_status.value.title()}")
    receipt_tab, edit_tab, audit_tab = st.tabs(["Receipt", "Edit", "Audit log"])
    with receipt_tab:
        display_receipts(chosen.id)
    with edit_tab:
        edit_bet_form(chosen)
        st.divider()
        with st.expander("Delete this accumulator"):
            st.warning("This permanently removes the bet, its legs, receipt screenshots, and audit history.")
            confirm = st.checkbox(f"I'm sure I want to delete accumulator #{chosen.id}", key=f"confirm_delete_{chosen.id}")
            if st.button("Delete bet and screenshots", type="primary", disabled=not confirm, key=f"delete_{chosen.id}"):
                repository.delete_bet(chosen.id)
                st.success(f"Accumulator #{chosen.id} deleted.")
                st.rerun()
    with audit_tab:
        for entry in repository.list_audit(chosen.id):
            st.markdown(f"**{entry.changed_at:%d %b %Y %H:%M} | {entry.action.title()}**")
            if entry.note:
                st.caption(entry.note)
            with st.expander("Revision data"):
                st.code(entry.after_json, language="json")


role = authenticate()
if role is None:
    st.stop()

st.title("Acca Syndicate")
st.caption("Accumulator and member selection performance")
labels = ["Dashboard", "Members", "Selection history", "Bet history"]
if role == "admin":
    labels.extend(["Upload", "Manage bets"])
tabs = st.tabs(labels)
with tabs[0]:
    @st.fragment(run_every="30s")
    def live_dashboard() -> None:
        dashboard()
    live_dashboard()
with tabs[1]:
    members_view()
with tabs[2]:
    selection_history_view()
with tabs[3]:
    bet_history_view()
if role == "admin":
    with tabs[4]:
        upload_view()
    with tabs[5]:
        manage_bets_view()
