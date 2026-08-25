from acca_app.analytics import member_leaderboard, syndicate_overall
from acca_app.database import Repository
from acca_app.parser import ExtractedAccumulator
from tests.test_parser_schema import extraction_payload


def saved_bets(tmp_path):
    repository = Repository(tmp_path / "analytics.db")
    repository.add_member("Luke")
    repository.add_member("Bailey")
    parsed = ExtractedAccumulator.model_validate(extraction_payload()).to_domain()
    repository.add_bet(parsed, ["Luke", "Bailey"], False, "one", [])
    return repository.list_bets()


def test_syndicate_uses_actual_accumulator_money(tmp_path):
    summary = syndicate_overall(saved_bets(tmp_path))
    assert summary["stakes"] == 6
    assert summary["returns"] == 0
    assert summary["profit"] == -6
    assert summary["roi"] == -100


def test_members_use_one_unit_per_selection(tmp_path):
    board = member_leaderboard(saved_bets(tmp_path), ["Luke", "Bailey"]).set_index("Member")
    assert board.loc["Luke", "Unit P/L"] == 2.25
    assert board.loc["Luke", "Unit ROI %"] == 225
    assert board.loc["Bailey", "Unit P/L"] == -1
    assert board.loc["Bailey", "Goal shortfall"] == -2
