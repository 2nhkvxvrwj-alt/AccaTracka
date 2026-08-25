# Acca Syndicate

A password-protected Streamlit tracker for a Result/BTTS accumulator syndicate. Administrators upload one or more screenshot/PDF fragments for a receipt, OpenAI vision extracts the accumulator, each leg is assigned to one member, and SQLite drives both real syndicate finances and normalized member analytics.

## Architecture

```text
Streamlit
|-- password gate (viewer/admin roles)
|-- multi-image/PDF receipt upload
|-- OpenAI Responses API vision, web search, and structured output
|-- member-per-leg allocation and validation
|-- SQLite repository + versioned migrations
|-- computed syndicate and unit analytics
|-- receipt viewer, amendments, and audit history
`-- dashboard, member charts, and selection matrix
```

Input remains screenshot/PDF only. There is no bookmaker API, scraping, browser automation, or CSV ingestion.

## Project structure

```text
app.py                    Streamlit views and workflows
acca_app/
|-- analytics.py          Syndicate, unit, history, and chart datasets
|-- config.py             Environment configuration
|-- database.py           SQLite repository, receipts, and audit operations
|-- files.py              Image/PDF preparation and combined hashing
|-- migrations.py         Ordered schema migrations
|-- models.py             Domain validation and Goal Shortfall
|-- parser.py             OpenAI multi-image extraction
`-- ui.py                 Shared renderers and table styling
tests/                    Domain, schema, database, audit, and analytics tests
```

## Data model

- `members`: syndicate roster.
- `bets`: real accumulator stake, return, status, dates, exclusion, and receipt metadata.
- `bet_legs`: one member-owned selection per contributing member, including odds, scores, score provenance, result, and Goal Shortfall.
- `receipt_files`: original private image/PDF data retained for review.
- `bet_audit_log`: immutable before/after snapshots for creation and amendments.
- `leaderboard`: computed at runtime, never stored.
- `legacy_bets`: preserved version of the original empty whole-bet ownership schema.

Migrations run automatically and are recorded in `schema_migrations`.

## Analytics rules

Syndicate metrics use actual settled accumulator money. Pending and excluded bets do not affect realized P/L or ROI. An accumulator wins only when the bookmaker settles the complete bet as won.

Member metrics normalize every settled selection to a one-unit stake:

```text
won:  unit return = decimal odds; unit P/L = odds - 1
lost: unit return = 0;            unit P/L = -1
void: unit return = 1;            unit P/L = 0
```

Pending and excluded legs do not affect realized member ROI. Goal Shortfall is zero for a successful Result/BTTS selection and otherwise the negative minimum number of added goals needed to satisfy both conditions. It never becomes positive.

## Local setup

```powershell
uv venv --python 3.12 .venv
.venv\Scripts\Activate.ps1
uv pip install -r requirements-dev.txt
Copy-Item .env.example .env
streamlit run app.py
```

Configure `.env`:

```text
OPENAI_API_KEY=your-server-side-key
OPENAI_MODEL=gpt-5.6-luna
DATABASE_PATH=data/betting_app.db
APP_CURRENCY=GBP
ADMIN_PASSWORD=private-admin-password
VIEWER_PASSWORD=password-shared-with-friends
```

The admin password exposes upload and amendment tabs. The viewer password provides read-only dashboards, history, and receipt viewing. If neither password is configured, local development opens with admin access.

Run verification with:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Upload workflow

1. Select up to eight images/PDFs belonging to one accumulator.
2. Parse all pages in one OpenAI request.
3. Missing scores are searched through OpenAI web search. An extracted fixture date is preferred; when the date is missing or ambiguous, lookup falls back to the most recent completed meeting with the same home/away teams.
4. Review or enter stake, return, odds, scores, and result overrides before saving.
5. Assign every registered member exactly once.
6. Optionally exclude the complete accumulator.
7. Save the bet and original receipts.

Missing values no longer discard an otherwise usable parse. A missing stake can be entered in the review form; a losing accumulator defaults a missing return to zero. Winning and pending accumulators still require a return before saving. Scores found online retain their source URL, while score edits are marked as manual. Combined odds are neither extracted nor stored.

## Amendments

The admin-only **Manage bets** view supports changes to actual finances, dates, exclusion, selected outcome, teams, odds, scores, member ownership, and settlement. Decimal (`3.25`) and fractional (`9/4`) odds are accepted. Every save recalculates Goal Shortfall and analytics and writes an immutable audit revision. Edited scores are marked as manual, and explicit status overrides remain authoritative even when the entered score appears inconsistent.

## Hosting

### Render

Use `render.yaml`, configure `OPENAI_API_KEY`, `ADMIN_PASSWORD`, and `VIEWER_PASSWORD`, then deploy. The included `/var/data` disk retains SQLite and receipt files.

### Railway

Deploy the `Dockerfile`, mount a persistent volume at `/data`, and set:

```text
DATABASE_PATH=/data/betting_app.db
OPENAI_API_KEY=...
ADMIN_PASSWORD=...
VIEWER_PASSWORD=...
```

### Streamlit Community Cloud

Select `app.py` and add the same values in Streamlit Secrets. Community Cloud local storage is ephemeral, so SQLite history and retained receipts may disappear after restart/redeploy. Use Render or Railway for the durable shared application.

## Extension points

- Add or adjust extraction instructions in `acca_app/parser.py`.
- `PARSER_PROMPT` controls receipt extraction and `SCORE_LOOKUP_PROMPT` controls online score matching.
- Add validation and other Result/BTTS metrics in `acca_app/models.py`.
- Add computed metrics and chart datasets in `acca_app/analytics.py`.
- Add schema changes only as new migrations in `acca_app/migrations.py`.
- Replace shared passwords with OIDC accounts and `viewer`, `uploader`, and `admin` roles.
- Add mobile/PWA upload while reusing the same image and parser pipeline.
- Build a labelled, anonymized receipt test set before changing models or prompts.
