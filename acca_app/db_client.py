"""Thin sqlite3-like adapter around libsql_client, so database.py can talk to
either a local SQLite file or a remote Turso/libSQL database without caring which."""
from __future__ import annotations

from pathlib import Path

import libsql_client as libsql

LibsqlError = libsql.LibsqlError


class _Cursor:
    def __init__(self, result_set: libsql.ResultSet):
        self._result_set = result_set
        self.rowcount = result_set.rows_affected
        self.lastrowid = result_set.last_insert_rowid

    def fetchall(self) -> list[libsql.Row]:
        return list(self._result_set.rows)

    def fetchone(self) -> libsql.Row | None:
        rows = self._result_set.rows
        return rows[0] if rows else None


class Connection:
    """Mimics the subset of sqlite3.Connection used by database.py."""

    def __init__(self, client: libsql.ClientSync):
        self._client = client

    def execute(self, sql: str, params: tuple | list = ()) -> _Cursor:
        return _Cursor(self._client.execute(sql, list(params)))

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self._client.execute(statement)

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def build_client(database_path: Path | str, database_url: str, database_auth_token: str) -> libsql.ClientSync:
    if database_url:
        # Force the HTTP transport (not ws/libsql) since some hosts (e.g. Streamlit
        # Community Cloud) block or mishandle outbound WebSocket connections.
        url = database_url
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        elif url.startswith("ws://"):
            url = "http://" + url[len("ws://"):]
        elif url.startswith("wss://"):
            url = "https://" + url[len("wss://"):]
        return libsql.create_client_sync(url, auth_token=database_auth_token or None)
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return libsql.create_client_sync(f"file:{path}")
