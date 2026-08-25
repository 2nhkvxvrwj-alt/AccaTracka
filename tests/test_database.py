from acca_app.database import DuplicateBetError, Repository
from acca_app.parser import ExtractedAccumulator
from tests.test_parser_schema import extraction_payload


def test_repository_round_trip_receipts_audit_and_duplicate(tmp_path):
    repository = Repository(tmp_path / "test.db")
    repository.add_member("Luke")
    repository.add_member("Bailey")
    parsed = ExtractedAccumulator.model_validate(extraction_payload()).to_domain()
    receipts = [("receipt.png", "image/png", b"image bytes")]
    bet_id = repository.add_bet(parsed, ["Luke", "Bailey"], False, "receipt-hash", receipts)
    bet = repository.get_bet(bet_id)
    assert [leg.member for leg in bet.legs] == ["Luke", "Bailey"]
    assert bet.legs[0].score_origin.value == "receipt"
    assert bet.legs[0].fixture_date.isoformat() == "2026-08-08"
    assert repository.list_receipts(bet_id)[0].data == b"image bytes"
    assert repository.list_audit(bet_id)[0].action == "created"

    bet.legs[0].odds = 4
    amended = repository.update_bet(bet, "Corrected odds")
    assert float(amended.legs[0].odds) == 4
    assert repository.list_audit(bet_id)[0].action == "amended"

    try:
        repository.add_bet(parsed, ["Luke", "Bailey"], False, "receipt-hash", receipts)
        raise AssertionError("duplicate should fail")
    except DuplicateBetError:
        pass


def test_rejects_duplicate_member_allocations(tmp_path):
    repository = Repository(tmp_path / "test.db")
    repository.add_member("Luke")
    parsed = ExtractedAccumulator.model_validate(extraction_payload()).to_domain()
    try:
        repository.add_bet(parsed, ["Luke", "Luke"], False, "hash", [])
        raise AssertionError("duplicate member should fail")
    except ValueError as exc:
        assert "only own one" in str(exc)
