import sqlite3
import os
from pathlib import Path

def get_conn() -> sqlite3.Connection:
    _default_db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data",
        "sibylla.db",
    )
    db_path = os.environ.get("DB_PATH", _default_db_path)
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if db_path != ":memory:":
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() != "delete":
            conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    conn = get_conn()
    _weather_only_cleanup(conn)
    _run_migrations(conn)
    conn.close()


def _weather_only_cleanup(conn: sqlite3.Connection):
    legacy_tables = [
        "sports_markets", "sports_alerts", "sports_trades",
        "politics_markets", "politics_alerts",
        "economics_markets", "fred_series",
        "congress_bills", "polymarket_markets",
        "odds_data", "fedwatch_data",
    ]
    for t in legacy_tables:
        conn.execute(f"DROP TABLE IF EXISTS {t}")

    for table in ("markets", "alerts", "model_outputs", "trades", "orders"):
        try:
            conn.execute(f"DELETE FROM {table} WHERE category != 'weather'")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def _run_migrations(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT UNIQUE NOT NULL,
            title TEXT,
            category TEXT DEFAULT 'weather',
            market_price REAL,
            yes_bid REAL,
            yes_ask REAL,
            no_bid REAL,
            no_ask REAL,
            status TEXT DEFAULT 'open',
            close_time TEXT,
            expiration_time TEXT,
            result TEXT,
            volume INTEGER DEFAULT 0,
            open_interest INTEGER DEFAULT 0,
            raw_json TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_ticker TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            edge REAL,
            direction TEXT,
            market_price REAL,
            model_prob REAL,
            confidence REAL,
            brain_score INTEGER,
            brain_state TEXT,
            brain_auto_qualified INTEGER DEFAULT 0,
            phantom_risk_level TEXT,
            phantom_risk_score REAL,
            phantom_risk_flags TEXT,
            details TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS model_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_ticker TEXT NOT NULL,
            category TEXT DEFAULT 'weather',
            model_prob REAL,
            edge REAL,
            confidence REAL,
            direction TEXT,
            forecast_data TEXT,
            phantom_risk_score REAL,
            phantom_risk_flags TEXT,
            phantom_risk_level TEXT,
            raw_output TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_ticker TEXT NOT NULL,
            alert_id INTEGER,
            direction TEXT,
            entry_price REAL,
            exit_price REAL,
            clv REAL,
            contracts INTEGER DEFAULT 1,
            status TEXT DEFAULT 'open',
            exit_reason TEXT,
            stop_loss_price REAL,
            take_profit_price REAL,
            paper INTEGER DEFAULT 1,
            pnl REAL,
            entry_time TEXT DEFAULT (datetime('now')),
            exit_time TEXT,
            FOREIGN KEY(alert_id) REFERENCES alerts(id)
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            market_ticker TEXT NOT NULL,
            side TEXT,
            price REAL,
            contracts INTEGER DEFAULT 1,
            status TEXT DEFAULT 'pending',
            order_type TEXT DEFAULT 'limit',
            kalshi_order_id TEXT,
            paper INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(trade_id) REFERENCES trades(id)
        );

        CREATE TABLE IF NOT EXISTS forecast_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_ticker TEXT NOT NULL,
            snapshot_date TEXT,
            forecast_high REAL,
            forecast_low REAL,
            forecast_precip REAL,
            actual_high REAL,
            actual_low REAL,
            actual_precip REAL,
            resolved INTEGER DEFAULT 0,
            model_prob REAL,
            market_price_at_snapshot REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_markets_ticker ON markets(ticker);
        CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
        CREATE INDEX IF NOT EXISTS idx_alerts_status_created_at ON alerts(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_alerts_status_updated_at ON alerts(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_alerts_ticker ON alerts(market_ticker);
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(market_ticker);
        CREATE INDEX IF NOT EXISTS idx_forecast_ticker ON forecast_snapshots(market_ticker);
        CREATE INDEX IF NOT EXISTS idx_forecast_created_at ON forecast_snapshots(created_at);
        CREATE INDEX IF NOT EXISTS idx_model_outputs_created_at ON model_outputs(created_at);

        CREATE TABLE IF NOT EXISTS balance_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kalshi_balance REAL,
            kalshi_portfolio REAL,
            paper_equity REAL,
            paper_pnl REAL,
            recorded_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS adaptive_segments (
            segment_key TEXT PRIMARY KEY,
            auto_eligible INTEGER DEFAULT 0,
            avg_clv REAL DEFAULT 0.0,
            avg_pnl REAL DEFAULT 0.0,
            positive_clv_rate REAL DEFAULT 0.0,
            recent_avg_clv REAL DEFAULT 0.0,
            recent_positive_clv_rate REAL DEFAULT 0.0,
            trade_count INTEGER DEFAULT 0,
            details TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS scan_status (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            status TEXT DEFAULT 'never',
            stage TEXT DEFAULT 'idle',
            progress INTEGER DEFAULT 0,
            started_at TEXT,
            completed_at TEXT,
            markets_found INTEGER DEFAULT 0,
            markets_processed INTEGER DEFAULT 0,
            alerts_created INTEGER DEFAULT 0,
            payload TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS price_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_ticker TEXT NOT NULL,
            yes_bid REAL,
            yes_ask REAL,
            yes_mid REAL,
            last_price REAL,
            source TEXT DEFAULT 'ws',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_price_snapshots_ticker_time
            ON price_snapshots(market_ticker, created_at);

        CREATE TABLE IF NOT EXISTS city_forecast_skill (
            series TEXT NOT NULL,
            kind TEXT NOT NULL,
            sample_count INTEGER DEFAULT 0,
            bias REAL DEFAULT 0.0,
            error_std REAL DEFAULT 0.0,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (series, kind)
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            actor TEXT DEFAULT 'system',
            market_ticker TEXT,
            trade_id INTEGER,
            order_id INTEGER,
            details TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at);
        CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);

        CREATE TABLE IF NOT EXISTS model_calibration (
            city TEXT NOT NULL,
            market_type TEXT NOT NULL,
            sample_count INTEGER DEFAULT 0,
            calibration_bias REAL DEFAULT 0.0,
            avg_model_prob REAL DEFAULT 0.0,
            avg_settlement_rate REAL DEFAULT 0.0,
            last_updated TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (city, market_type)
        );
    """)
    _ensure_column(conn, "trades", "take_profit_price", "REAL")
    _ensure_column(conn, "trades", "settlement_result", "TEXT")
    _ensure_column(conn, "trades", "prediction_correct", "INTEGER")
    _ensure_column(conn, "trades", "settlement_pnl", "REAL")
    # True closing-line value: market price just before close (from
    # price_snapshots) vs entry. The legacy `clv` column restates settlement
    # P&L for ride-to-settlement trades — see docs/STRATEGY_RECOMMENDATIONS.md §5.
    _ensure_column(conn, "trades", "close_mark_yes", "REAL")
    _ensure_column(conn, "trades", "true_clv", "REAL")
    # Entry-time fill context snapshot. The alert's recommendation is
    # recomputed every scan, so by settlement it no longer reflects what the
    # trade actually saw — these freeze fill_model and the touch at entry.
    _ensure_column(conn, "trades", "fill_model", "TEXT")
    _ensure_column(conn, "trades", "entry_side_bid", "REAL")
    _ensure_column(conn, "trades", "entry_side_ask", "REAL")
    # Live execution layer: deterministic idempotency key, re-quote tracking,
    # and order purpose (entry vs exit) for the work-the-bid engine.
    _ensure_column(conn, "orders", "client_order_id", "TEXT")
    _ensure_column(conn, "orders", "requote_count", "INTEGER DEFAULT 0")
    _ensure_column(conn, "orders", "purpose", "TEXT DEFAULT 'entry'")
    _ensure_column(conn, "orders", "exit_reason", "TEXT")
    _ensure_column(conn, "adaptive_segments", "positive_clv_rate", "REAL DEFAULT 0.0")
    _ensure_column(conn, "adaptive_segments", "recent_avg_clv", "REAL DEFAULT 0.0")
    _ensure_column(conn, "adaptive_segments", "recent_positive_clv_rate", "REAL DEFAULT 0.0")
    _ensure_column(conn, "model_calibration", "avg_model_prob", "REAL DEFAULT 0.0")
    _ensure_column(conn, "model_calibration", "avg_settlement_rate", "REAL DEFAULT 0.0")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
