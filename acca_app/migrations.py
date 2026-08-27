from __future__ import annotations

from acca_app.db_client import Connection


MIGRATIONS: list[tuple[int, str]] = [
    (1, """
        CREATE TABLE IF NOT EXISTS members (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, member TEXT NOT NULL,
            stake NUMERIC NOT NULL CHECK (stake > 0), return NUMERIC NOT NULL CHECK (return >= 0),
            combined_odds NUMERIC NOT NULL CHECK (combined_odds > 1), legs TEXT NOT NULL CHECK (json_valid(legs)),
            settlement_status TEXT NOT NULL CHECK (settlement_status IN ('pending','won','lost','void')),
            excluded INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0,1)), timestamp TEXT NOT NULL,
            FOREIGN KEY (member) REFERENCES members(name));
        CREATE INDEX IF NOT EXISTS idx_bets_timestamp ON bets(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_bets_member ON bets(member);
    """),
    (2, """
        ALTER TABLE bets ADD COLUMN bookmaker TEXT;
        ALTER TABLE bets ADD COLUMN reference TEXT;
        ALTER TABLE bets ADD COLUMN confidence REAL;
        ALTER TABLE bets ADD COLUMN source_hash TEXT;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_bets_source_hash ON bets(source_hash) WHERE source_hash IS NOT NULL;
    """),
    (3, """
        DROP INDEX IF EXISTS idx_bets_timestamp;
        DROP INDEX IF EXISTS idx_bets_member;
        DROP INDEX IF EXISTS idx_bets_source_hash;
        ALTER TABLE bets RENAME TO legacy_bets;
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stake NUMERIC NOT NULL CHECK (stake > 0), return NUMERIC NOT NULL CHECK (return >= 0),
            settlement_status TEXT NOT NULL CHECK (settlement_status IN ('pending','won','lost','void')),
            excluded INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0,1)),
            timestamp TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            bookmaker TEXT, reference TEXT, confidence REAL, source_hash TEXT UNIQUE);
        CREATE TABLE bet_legs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bet_id INTEGER NOT NULL REFERENCES bets(id) ON DELETE CASCADE,
            member_id INTEGER NOT NULL REFERENCES members(id),
            selection TEXT NOT NULL, market TEXT NOT NULL, event TEXT NOT NULL,
            selected_outcome TEXT NOT NULL CHECK (selected_outcome IN ('home_win','away_win','draw')),
            home_team TEXT NOT NULL, away_team TEXT NOT NULL,
            odds NUMERIC NOT NULL CHECK (odds > 1),
            home_score INTEGER CHECK (home_score >= 0), away_score INTEGER CHECK (away_score >= 0),
            settlement_status TEXT NOT NULL CHECK (settlement_status IN ('pending','won','lost','void')),
            goal_shortfall INTEGER CHECK (goal_shortfall <= 0),
            manual_override INTEGER NOT NULL DEFAULT 0 CHECK (manual_override IN (0,1)),
            UNIQUE (bet_id, member_id));
        CREATE TABLE receipt_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bet_id INTEGER NOT NULL REFERENCES bets(id) ON DELETE CASCADE,
            filename TEXT NOT NULL, mime_type TEXT NOT NULL, data BLOB NOT NULL, sha256 TEXT NOT NULL);
        CREATE TABLE bet_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bet_id INTEGER NOT NULL REFERENCES bets(id) ON DELETE CASCADE,
            changed_at TEXT NOT NULL, action TEXT NOT NULL, before_json TEXT,
            after_json TEXT NOT NULL, note TEXT);
        CREATE INDEX idx_bets_timestamp ON bets(timestamp DESC);
        CREATE INDEX idx_legs_member ON bet_legs(member_id);
        CREATE INDEX idx_legs_bet ON bet_legs(bet_id);
        CREATE INDEX idx_receipts_bet ON receipt_files(bet_id);
        CREATE INDEX idx_audit_bet ON bet_audit_log(bet_id, changed_at DESC);
    """),
    (4, """
        ALTER TABLE bet_legs ADD COLUMN score_origin TEXT NOT NULL DEFAULT 'unknown'
            CHECK (score_origin IN ('unknown','receipt','web','manual'));
        ALTER TABLE bet_legs ADD COLUMN score_source_title TEXT;
        ALTER TABLE bet_legs ADD COLUMN score_source_url TEXT;
        UPDATE bet_legs
        SET score_origin = CASE
            WHEN manual_override = 1 THEN 'manual'
            WHEN home_score IS NOT NULL AND away_score IS NOT NULL THEN 'receipt'
            ELSE 'unknown'
        END;
    """),
    (5, """
        ALTER TABLE bet_legs ADD COLUMN fixture_date TEXT;
    """),
]


def migrate(connection: Connection) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()}
    for version, sql in MIGRATIONS:
        if version not in applied:
            with connection:
                connection.executescript(sql)
                connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
