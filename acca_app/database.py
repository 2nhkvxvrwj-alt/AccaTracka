from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from acca_app.migrations import migrate
from acca_app.models import (
    AuditRecord,
    BetRecord,
    LegRecord,
    ParsedAccumulator,
    ReceiptRecord,
    calculate_goal_shortfall,
)


class DuplicateBetError(ValueError):
    pass


class Repository:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            migrate(connection)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def list_members(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT name FROM members ORDER BY name").fetchall()
        return [row["name"] for row in rows]

    def add_member(self, name: str) -> None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Member name cannot be empty")
        with self.connect() as connection:
            connection.execute("INSERT OR IGNORE INTO members(name) VALUES (?)", (clean_name,))

    def _member_ids(self, connection: sqlite3.Connection, names: list[str]) -> dict[str, int]:
        if len(names) != len(set(names)):
            raise ValueError("Each member can only own one selection in an accumulator")
        placeholders = ",".join("?" for _ in names)
        rows = connection.execute(
            f"SELECT id, name FROM members WHERE name IN ({placeholders})", names
        ).fetchall()
        result = {row["name"]: row["id"] for row in rows}
        missing = set(names) - set(result)
        if missing:
            raise ValueError(f"Unknown syndicate member: {', '.join(sorted(missing))}")
        return result

    def add_bet(
        self,
        parsed: ParsedAccumulator,
        allocations: list[str],
        excluded: bool,
        source_hash: str,
        receipts: list[tuple[str, str, bytes]],
    ) -> int:
        parsed.validate_ready_to_save()
        if len(allocations) != len(parsed.legs):
            raise ValueError("Every selection must have one member")
        now = datetime.now(timezone.utc)
        timestamp = parsed.placed_at or now
        try:
            with self.connect() as connection:
                member_ids = self._member_ids(connection, allocations)
                cursor = connection.execute(
                    """INSERT INTO bets (
                        stake, return, settlement_status, excluded, timestamp,
                        created_at, updated_at, bookmaker, reference, confidence, source_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(parsed.stake), str(parsed.return_amount), parsed.settlement_status.value,
                     int(excluded), timestamp.isoformat(), now.isoformat(), now.isoformat(),
                     parsed.bookmaker, parsed.reference, parsed.confidence, source_hash),
                )
                bet_id = int(cursor.lastrowid)
                for leg, member in zip(parsed.legs, allocations, strict=True):
                    connection.execute(
                        """INSERT INTO bet_legs (
                            bet_id, member_id, selection, market, event, selected_outcome,
                            home_team, away_team, fixture_date, odds, home_score, away_score,
                            settlement_status, goal_shortfall, manual_override, score_origin,
                            score_source_title, score_source_url
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (bet_id, member_ids[member], leg.selection, leg.market, leg.event,
                         leg.selected_outcome.value, leg.home_team, leg.away_team,
                         leg.fixture_date.isoformat() if leg.fixture_date else None, str(leg.odds),
                         leg.home_score, leg.away_score, leg.settlement_status.value, leg.goal_shortfall,
                         int(leg.manual_override), leg.score_origin.value, leg.score_source_title,
                         leg.score_source_url),
                    )
                for filename, mime_type, data in receipts:
                    connection.execute(
                        "INSERT INTO receipt_files (bet_id, filename, mime_type, data, sha256) VALUES (?, ?, ?, ?, ?)",
                        (bet_id, filename, mime_type, data, hashlib.sha256(data).hexdigest()),
                    )
        except sqlite3.IntegrityError as exc:
            if "source_hash" in str(exc):
                raise DuplicateBetError("This set of receipt images has already been saved") from exc
            raise
        saved = self.get_bet(bet_id)
        self._audit(bet_id, "created", None, saved, None)
        return bet_id

    def list_bets(self, excluded: bool | None = None, limit: int | None = None) -> list[BetRecord]:
        query, params = "SELECT * FROM bets", []
        if excluded is not None:
            query += " WHERE excluded = ?"
            params.append(int(excluded))
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
            bets = [self._to_bet(connection, row) for row in rows]
        bets.sort(key=lambda bet: (bet.fixture_date, bet.id), reverse=True)
        if limit is not None:
            bets = bets[:limit]
        return bets

    def delete_bet(self, bet_id: int) -> None:
        with self.connect() as connection:
            deleted = connection.execute("DELETE FROM bets WHERE id=?", (bet_id,)).rowcount
        if not deleted:
            raise ValueError(f"Accumulator #{bet_id} does not exist")

    def get_bet(self, bet_id: int) -> BetRecord:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM bets WHERE id = ?", (bet_id,)).fetchone()
            if row is None:
                raise ValueError(f"Accumulator #{bet_id} does not exist")
            return self._to_bet(connection, row)

    def update_bet(self, bet: BetRecord, note: str | None = None) -> BetRecord:
        before = self.get_bet(bet.id)
        now = datetime.now(timezone.utc)
        names = [leg.member for leg in bet.legs]
        with self.connect() as connection:
            member_ids = self._member_ids(connection, names)
            connection.execute(
                """UPDATE bets SET stake=?, return=?, settlement_status=?, excluded=?,
                    timestamp=?, updated_at=?, bookmaker=?, reference=? WHERE id=?""",
                (str(bet.stake), str(bet.return_amount), bet.settlement_status.value,
                 int(bet.excluded), bet.timestamp.isoformat(), now.isoformat(),
                 bet.bookmaker, bet.reference, bet.id),
            )
            for leg in bet.legs:
                shortfall = calculate_goal_shortfall(leg.selected_outcome, leg.home_score, leg.away_score)
                connection.execute(
                    """UPDATE bet_legs SET member_id=?, selection=?, market=?, event=?,
                        selected_outcome=?, home_team=?, away_team=?, odds=?, home_score=?,
                        fixture_date=?,
                        away_score=?, settlement_status=?, goal_shortfall=?, manual_override=?,
                        score_origin=?, score_source_title=?, score_source_url=?
                        WHERE id=? AND bet_id=?""",
                    (member_ids[leg.member], leg.selection, leg.market, leg.event,
                     leg.selected_outcome.value, leg.home_team, leg.away_team, str(leg.odds),
                     leg.home_score, leg.fixture_date.isoformat() if leg.fixture_date else None,
                     leg.away_score, leg.settlement_status.value,
                     shortfall, int(leg.manual_override), leg.score_origin.value,
                     leg.score_source_title, leg.score_source_url, leg.id, bet.id),
                )
        after = self.get_bet(bet.id)
        self._audit(bet.id, "amended", before, after, note)
        return after

    def list_receipts(self, bet_id: int) -> list[ReceiptRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, bet_id, filename, mime_type, data FROM receipt_files WHERE bet_id=? ORDER BY id",
                (bet_id,),
            ).fetchall()
        return [ReceiptRecord(**dict(row)) for row in rows]

    def list_audit(self, bet_id: int) -> list[AuditRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM bet_audit_log WHERE bet_id=? ORDER BY changed_at DESC, id DESC", (bet_id,)
            ).fetchall()
        return [AuditRecord(**dict(row)) for row in rows]

    def _audit(self, bet_id: int, action: str, before: BetRecord | None, after: BetRecord, note: str | None) -> None:
        before_json = before.model_dump_json() if before else None
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO bet_audit_log (bet_id, changed_at, action, before_json, after_json, note)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (bet_id, datetime.now(timezone.utc).isoformat(), action, before_json,
                 after.model_dump_json(), note.strip() if note else None),
            )

    @staticmethod
    def _to_bet(connection: sqlite3.Connection, row: sqlite3.Row) -> BetRecord:
        leg_rows = connection.execute(
            """SELECT l.*, m.name AS member FROM bet_legs l
               JOIN members m ON m.id=l.member_id WHERE l.bet_id=? ORDER BY l.id""", (row["id"],)
        ).fetchall()
        legs = [LegRecord(
            id=leg["id"], member=leg["member"], selection=leg["selection"], market=leg["market"],
            event=leg["event"], selected_outcome=leg["selected_outcome"], home_team=leg["home_team"],
            away_team=leg["away_team"], fixture_date=leg["fixture_date"],
            odds=Decimal(str(leg["odds"])), home_score=leg["home_score"],
            away_score=leg["away_score"], settlement_status=leg["settlement_status"],
            goal_shortfall=leg["goal_shortfall"], manual_override=bool(leg["manual_override"]),
            score_origin=leg["score_origin"], score_source_title=leg["score_source_title"],
            score_source_url=leg["score_source_url"],
        ) for leg in leg_rows]
        return BetRecord(
            id=row["id"], stake=Decimal(str(row["stake"])), return_amount=Decimal(str(row["return"])),
            settlement_status=row["settlement_status"], excluded=bool(row["excluded"]),
            timestamp=datetime.fromisoformat(row["timestamp"]), created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]), bookmaker=row["bookmaker"],
            reference=row["reference"], confidence=row["confidence"], source_hash=row["source_hash"], legs=legs,
        )
