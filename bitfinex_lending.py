"""Bitfinex 放貸機械人 — 單檔 Textual TUI 版本。

本檔案是 Bitfinex lending bot 的單檔 Textual 終端介面（TUI）重寫版本。
原 loanbot 套件採用 asyncio + aiohttp web dashboard 架構，本檔案將其核心
業務邏輯（資料模型、設定載入、SQLite 持久化、市場抓取與解析、策略決策、
撮合、利息累計、Bitfinex 認證客戶端）整併進單一檔案，並以 Textual App 取代
aiohttp web 伺服器與 asyncio 編排。

目標是在本機終端機運行的輕量化版本，即時顯示市場行情、掛單、貸款與收益，
完整保留 dry-run 與 live 雙模式行為，並沿用既有 SQLite 資料庫與 TOML 設定格式。

執行方式：
    python loanbot_tui.py --config ./config.toml
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import pathlib
import sqlite3
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import aiohttp
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Select, Static


# ===== 常數 =====

UI_REFRESH_SEC = 5
REQUEST_TIMEOUT = 10.0
RETRY_DELAY = 5.0
RATE_LIMIT_PAUSE = 60.0
BASE_PUBLIC_URL = "https://api-pub.bitfinex.com/v2"
BASE_AUTH_URL = "https://api.bitfinex.com"

BROWSE_SYMBOLS = [
    "fUST", "fUSD", "fBTC", "fETH", "fSOL", "fXRP",
    "fDOT", "fATOM", "fLINK", "fEUR", "fJPY", "fLTC",
]


# ===== 資料模型 =====


@dataclass(frozen=True)
class FundingBookRow:
    rate: Decimal
    period: int
    amount: Decimal
    count: int
    side: Literal["bid", "ask"]


@dataclass(frozen=True)
class OrderBookSnapshot:
    id: int | None
    symbol: str
    captured_at_utc: datetime
    bids: list[FundingBookRow]
    asks: list[FundingBookRow]
    frr_estimate: Decimal | None


@dataclass(frozen=True)
class SimulatedOffer:
    id: int | None
    created_at_utc: datetime
    symbol: str
    rate: Decimal
    amount: Decimal
    period_days: int
    status: Literal["pending", "filled"]


@dataclass(frozen=True)
class SimulatedLoan:
    id: int | None
    offer_id: int
    started_at_utc: datetime
    expires_at_utc: datetime
    principal: Decimal
    daily_rate: Decimal
    period_days: int
    symbol: str
    status: Literal["active", "closed"]
    accrued_interest: Decimal
    final_interest: Decimal | None


# ===== 設定載入 =====


class ConfigError(Exception):
    """Raised when the config file is missing, unreadable, or invalid."""


@dataclass(frozen=True)
class Config:
    funding_symbol: str  # e.g. "fUST" — must start with "f"
    polling_interval_seconds: int  # 15..600
    decision_interval_seconds: int  # 10..3600
    snapshot_freshness_seconds: int  # 15..3600
    strategy_mode: Literal["ask_book_nth", "frr_delta", "best_period"]
    strategy_n: int | None  # 1..25, required for ask_book_nth
    strategy_delta: Decimal | None  # required for frr_delta
    min_rate_threshold: Decimal  # 0..1
    offer_amount: Decimal  # > 0
    period_days: int  # 2..120
    db_path: str  # non-empty
    web_port: int  # 1024..65535（保留以向後相容，TUI 不使用）
    # --- live trading (dry_run=false) ---
    dry_run: bool  # true = simulate only; false = real orders
    api_key: str  # Bitfinex API key (empty when dry_run)
    api_secret: str  # Bitfinex API secret (empty when dry_run)
    max_total_amount: Decimal  # max total exposure across all active offers+loans
    cancel_after_minutes: int  # auto-cancel offers older than N minutes (0 = never)


def load_config(path: pathlib.Path) -> Config:
    """Read ``path`` and return a validated :class:`Config`."""
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        with path.open("rb") as fh:
            data: dict[str, Any] = tomllib.load(fh)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    try:
        symbol = _str(data, "funding_symbol")
        if not symbol.startswith("f"):
            raise ConfigError("funding_symbol must start with 'f'")
        polling = _int_in(data, "polling_interval_seconds", 15, 600, default=60)
        decision = _int_in(data, "decision_interval_seconds", 10, 3600, default=60)
        freshness = _int_in(data, "snapshot_freshness_seconds", 15, 3600, default=300)
        strategy = data.get("strategy", {})
        if not isinstance(strategy, dict):
            raise ConfigError("strategy must be a TOML table")
        mode = strategy.get("mode")
        if mode not in ("ask_book_nth", "frr_delta", "best_period"):
            raise ConfigError(
                "strategy.mode must be 'ask_book_nth', 'frr_delta', or 'best_period'"
            )
        n = strategy.get("n")
        delta_raw = strategy.get("delta")
        if mode == "ask_book_nth":
            if not isinstance(n, int) or not (1 <= n <= 25):
                raise ConfigError("strategy.n must be int in [1, 25]")
            delta: Decimal | None = None
        elif mode == "frr_delta":
            if delta_raw is None:
                raise ConfigError("strategy.delta is required for frr_delta")
            delta = _decimal(delta_raw, "strategy.delta")
            n = None
        else:
            # best_period — no extra params needed
            n = None
            delta = None
        return Config(
            funding_symbol=symbol,
            polling_interval_seconds=polling,
            decision_interval_seconds=decision,
            snapshot_freshness_seconds=freshness,
            strategy_mode=mode,
            strategy_n=n,
            strategy_delta=delta,
            min_rate_threshold=_decimal_in(
                data, "min_rate_threshold", Decimal("0"), Decimal("1")
            ),
            offer_amount=_decimal_gt(data, "offer_amount", Decimal("0")),
            period_days=_int_in(data, "period_days", 2, 120),
            db_path=_str(data, "db_path"),
            web_port=_int_in(data, "web_port", 1024, 65535, default=8080),
            dry_run=data.get("dry_run", True) is True,
            api_key=str(data.get("api_key", "")),
            api_secret=str(data.get("api_secret", "")),
            max_total_amount=_decimal_gt(
                data if "max_total_amount" in data else {"max_total_amount": "10000"},
                "max_total_amount",
                Decimal("0"),
            ),
            cancel_after_minutes=_int_in(
                data, "cancel_after_minutes", 0, 10080, default=30
            ),
        )
    except ConfigError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc


def load_config_or_exit(path: pathlib.Path) -> Config:
    """Wrapper for the entry point: print to stderr + ``SystemExit`` on error."""
    try:
        return load_config(path)
    except ConfigError as exc:
        sys.stderr.write(f"config error: {exc}\n")
        raise SystemExit(2) from exc


# --- field readers -----------------------------------------------------------


def _str(data: dict[str, Any], key: str) -> str:
    if key not in data:
        raise ConfigError(f"{key} is required")
    value = data[key]
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string")
    return value


def _int_in(
    data: dict[str, Any],
    key: str,
    lo: int,
    hi: int,
    *,
    default: int | None = None,
) -> int:
    value = data.get(key, default)
    if value is None:
        raise ConfigError(f"{key} is required")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{key} must be an integer")
    if not (lo <= value <= hi):
        raise ConfigError(f"{key} must be in [{lo}, {hi}], got {value}")
    return value


def _decimal(value: Any, where: str) -> Decimal:
    """Coerce a TOML value to :class:`Decimal` without going through float."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ConfigError(f"{where} must be quoted as a string for Decimal precision")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ConfigError(f"{where} is not a valid decimal: {value!r}") from exc
    raise ConfigError(f"{where} must be a string or integer")


def _decimal_in(
    data: dict[str, Any], key: str, lo: Decimal, hi: Decimal
) -> Decimal:
    if key not in data:
        raise ConfigError(f"{key} is required")
    value = _decimal(data[key], key)
    if not (lo <= value <= hi):
        raise ConfigError(f"{key} must be in [{lo}, {hi}], got {value}")
    return value


def _decimal_gt(data: dict[str, Any], key: str, lo: Decimal) -> Decimal:
    if key not in data:
        raise ConfigError(f"{key} is required")
    value = _decimal(data[key], key)
    if value <= lo:
        raise ConfigError(f"{key} must be > {lo}, got {value}")
    return value


# ===== SQLite 持久化 =====

_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_book_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    captured_at_utc TEXT NOT NULL,
    frr_estimate TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshot_symbol_time
    ON order_book_snapshot(symbol, captured_at_utc DESC);

CREATE TABLE IF NOT EXISTS order_book_row (
    snapshot_id INTEGER NOT NULL REFERENCES order_book_snapshot(id) ON DELETE CASCADE,
    side TEXT NOT NULL CHECK(side IN ('bid','ask')),
    depth INTEGER NOT NULL,
    rate TEXT NOT NULL,
    period INTEGER NOT NULL,
    amount TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, side, depth)
);

CREATE TABLE IF NOT EXISTS funding_candle (
    symbol TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    open TEXT NOT NULL,
    close TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    volume TEXT NOT NULL,
    PRIMARY KEY (symbol, timestamp_ms)
);

CREATE TABLE IF NOT EXISTS simulated_offer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    rate TEXT NOT NULL,
    amount TEXT NOT NULL,
    period_days INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','filled'))
);

CREATE INDEX IF NOT EXISTS idx_offer_status
    ON simulated_offer(status, created_at_utc);

CREATE TABLE IF NOT EXISTS simulated_loan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id INTEGER NOT NULL REFERENCES simulated_offer(id),
    started_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    principal TEXT NOT NULL,
    daily_rate TEXT NOT NULL,
    period_days INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','closed')),
    accrued_interest TEXT NOT NULL DEFAULT '0',
    final_interest TEXT
);

CREATE INDEX IF NOT EXISTS idx_loan_status
    ON simulated_loan(status, expires_at_utc);
"""


def open_database(path: pathlib.Path | str) -> sqlite3.Connection:
    """Open the SQLite connection used by :class:`Repo`.

    The connection is configured with ``foreign_keys=ON`` so the
    ``order_book_row`` cascade delete works, and ``check_same_thread=False``
    so :func:`asyncio.to_thread` can dispatch to a worker thread.
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    """Create all tables and indices if missing. Safe to call repeatedly."""
    with conn:
        conn.executescript(_SCHEMA)


# --- repository --------------------------------------------------------------


class Repo:
    """Tiny synchronous repository serving the bot and the dashboard.

    Methods write through ``with self.conn:`` so each call commits or
    rolls back atomically. The class is deliberately not split into
    market / sim sub-repos: the simulator only has ~10 queries total and
    one class keeps the SQL co-located.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- order book snapshot --

    def insert_snapshot(self, snap: OrderBookSnapshot) -> int:
        frr = str(snap.frr_estimate) if snap.frr_estimate is not None else None
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO order_book_snapshot "
                "(symbol, captured_at_utc, frr_estimate) VALUES (?, ?, ?)",
                (snap.symbol, _to_iso(snap.captured_at_utc), frr),
            )
            snapshot_id = int(cur.lastrowid or 0)
            rows = [
                (snapshot_id, r.side, depth, str(r.rate), r.period, str(r.amount), r.count)
                for depth, r in enumerate(snap.bids)
            ] + [
                (snapshot_id, r.side, depth, str(r.rate), r.period, str(r.amount), r.count)
                for depth, r in enumerate(snap.asks)
            ]
            if rows:
                self.conn.executemany(
                    "INSERT INTO order_book_row "
                    "(snapshot_id, side, depth, rate, period, amount, count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
        return snapshot_id

    def latest_snapshot(self, symbol: str) -> OrderBookSnapshot | None:
        meta = self.conn.execute(
            "SELECT id, symbol, captured_at_utc, frr_estimate "
            "FROM order_book_snapshot "
            "WHERE symbol = ? ORDER BY captured_at_utc DESC, id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if meta is None:
            return None
        snapshot_id = int(meta[0])
        bids: list[FundingBookRow] = []
        asks: list[FundingBookRow] = []
        for side, _depth, rate, period, amount, count in self.conn.execute(
            "SELECT side, depth, rate, period, amount, count "
            "FROM order_book_row WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall():
            row = FundingBookRow(
                rate=Decimal(rate),
                period=int(period),
                amount=Decimal(amount),
                count=int(count),
                side="bid" if side == "bid" else "ask",
            )
            (bids if side == "bid" else asks).append(row)
        bids.sort(key=lambda r: r.rate, reverse=True)
        asks.sort(key=lambda r: r.rate)
        return OrderBookSnapshot(
            id=snapshot_id,
            symbol=str(meta[1]),
            captured_at_utc=_from_iso(str(meta[2])),
            bids=bids,
            asks=asks,
            frr_estimate=Decimal(meta[3]) if meta[3] is not None else None,
        )

    # -- offers --

    def insert_offer(self, offer: SimulatedOffer) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO simulated_offer "
                "(created_at_utc, symbol, rate, amount, period_days, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _to_iso(offer.created_at_utc),
                    offer.symbol,
                    str(offer.rate),
                    str(offer.amount),
                    int(offer.period_days),
                    offer.status,
                ),
            )
        return int(cur.lastrowid or 0)

    def mark_offer_filled(self, offer_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE simulated_offer SET status = 'filled' WHERE id = ?",
                (int(offer_id),),
            )

    def pending_offers(self, *, limit: int = 100) -> list[SimulatedOffer]:
        rows = self.conn.execute(
            "SELECT id, created_at_utc, symbol, rate, amount, period_days, status "
            "FROM simulated_offer WHERE status = 'pending' "
            "ORDER BY created_at_utc DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [_row_to_offer(r) for r in rows]

    def pending_offers_oldest_first(self) -> list[SimulatedOffer]:
        """Used by the matcher; returns *all* pending rows in FIFO order."""
        rows = self.conn.execute(
            "SELECT id, created_at_utc, symbol, rate, amount, period_days, status "
            "FROM simulated_offer WHERE status = 'pending' "
            "ORDER BY created_at_utc ASC, id ASC"
        ).fetchall()
        return [_row_to_offer(r) for r in rows]

    # -- loans --

    def insert_loan(self, loan: SimulatedLoan) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO simulated_loan "
                "(offer_id, started_at_utc, expires_at_utc, principal, daily_rate, "
                " period_days, symbol, status, accrued_interest, final_interest) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(loan.offer_id),
                    _to_iso(loan.started_at_utc),
                    _to_iso(loan.expires_at_utc),
                    str(loan.principal),
                    str(loan.daily_rate),
                    int(loan.period_days),
                    loan.symbol,
                    loan.status,
                    str(loan.accrued_interest),
                    str(loan.final_interest) if loan.final_interest is not None else None,
                ),
            )
        return int(cur.lastrowid or 0)

    def update_loan_accrued(self, loan_id: int, accrued: Decimal) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE simulated_loan SET accrued_interest = ? WHERE id = ?",
                (str(accrued), int(loan_id)),
            )

    def close_loan(self, loan_id: int, final_interest: Decimal) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE simulated_loan "
                "SET status = 'closed', final_interest = ?, accrued_interest = ? "
                "WHERE id = ?",
                (str(final_interest), str(final_interest), int(loan_id)),
            )

    def active_loans(self, *, limit: int = 200) -> list[SimulatedLoan]:
        return self._loans_by_status("active", "started_at_utc DESC, id DESC", limit)

    def closed_loans(self, *, limit: int = 200) -> list[SimulatedLoan]:
        return self._loans_by_status("closed", "expires_at_utc DESC, id DESC", limit)

    def total_realized_interest(self) -> Decimal:
        rows = self.conn.execute(
            "SELECT final_interest FROM simulated_loan "
            "WHERE status = 'closed' AND final_interest IS NOT NULL"
        ).fetchall()
        return sum((Decimal(r[0]) for r in rows), Decimal("0"))

    def total_unrealized_interest(self) -> Decimal:
        rows = self.conn.execute(
            "SELECT accrued_interest FROM simulated_loan WHERE status = 'active'"
        ).fetchall()
        return sum((Decimal(r[0]) for r in rows), Decimal("0"))

    def _loans_by_status(
        self, status: str, order_by: str, limit: int
    ) -> list[SimulatedLoan]:
        rows = self.conn.execute(
            "SELECT id, offer_id, started_at_utc, expires_at_utc, principal, "
            "       daily_rate, period_days, symbol, status, accrued_interest, "
            "       final_interest "
            "FROM simulated_loan WHERE status = ? "
            f"ORDER BY {order_by} LIMIT ?",
            (status, int(limit)),
        ).fetchall()
        return [_row_to_loan(r) for r in rows]

    # -- candles (used by the backtest CLI) --

    def upsert_candles(
        self, symbol: str, candles: "list[tuple[int, Decimal, Decimal, Decimal, Decimal, Decimal]]"
    ) -> int:
        """Upsert ``[timestamp_ms, open, close, high, low, volume]`` rows."""
        if not candles:
            return 0
        params = [
            (
                symbol,
                int(ts),
                str(o),
                str(c),
                str(h),
                str(low),
                str(v),
            )
            for ts, o, c, h, low, v in candles
        ]
        with self.conn:
            self.conn.executemany(
                "INSERT INTO funding_candle "
                "(symbol, timestamp_ms, open, close, high, low, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(symbol, timestamp_ms) DO UPDATE SET "
                "open=excluded.open, close=excluded.close, "
                "high=excluded.high, low=excluded.low, volume=excluded.volume",
                params,
            )
        return len(params)

    def candles_in_range(
        self, symbol: str, start_ms: int, end_ms: int
    ) -> "list[tuple[int, Decimal]]":
        """Return ``[(timestamp_ms, high)]`` candles in ``[start_ms, end_ms)``.

        The backtest only needs ``high`` (the bid threshold) so we project
        early to keep the working set tiny.
        """
        rows = self.conn.execute(
            "SELECT timestamp_ms, high FROM funding_candle "
            "WHERE symbol = ? AND timestamp_ms >= ? AND timestamp_ms < ? "
            "ORDER BY timestamp_ms ASC",
            (symbol, int(start_ms), int(end_ms)),
        ).fetchall()
        return [(int(ts), Decimal(high)) for ts, high in rows]


# --- helpers -----------------------------------------------------------------


def _to_iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _from_iso(text: str) -> datetime:
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def _row_to_offer(row: tuple) -> SimulatedOffer:
    (offer_id, created, symbol, rate, amount, period_days, status) = row
    return SimulatedOffer(
        id=int(offer_id),
        created_at_utc=_from_iso(str(created)),
        symbol=str(symbol),
        rate=Decimal(rate),
        amount=Decimal(amount),
        period_days=int(period_days),
        status=status if status in ("pending", "filled") else "pending",
    )


def _row_to_loan(row: tuple) -> SimulatedLoan:
    (
        loan_id,
        offer_id,
        started,
        expires,
        principal,
        daily_rate,
        period_days,
        symbol,
        status,
        accrued,
        final_interest,
    ) = row
    return SimulatedLoan(
        id=int(loan_id),
        offer_id=int(offer_id),
        started_at_utc=_from_iso(str(started)),
        expires_at_utc=_from_iso(str(expires)),
        principal=Decimal(principal),
        daily_rate=Decimal(daily_rate),
        period_days=int(period_days),
        symbol=str(symbol),
        status=status if status in ("active", "closed") else "active",
        accrued_interest=Decimal(accrued),
        final_interest=Decimal(final_interest) if final_interest is not None else None,
    )


# ===== 市場抓取與解析 =====

_LOGGER = logging.getLogger(__name__)


class MalformedPayload(Exception):
    """Raised when a Bitfinex response cannot be parsed."""


class RateLimited(Exception):
    """Bitfinex responded with HTTP 429."""


def parse_funding_book(
    payload: list[Any], symbol: str, captured_at_utc: datetime
) -> OrderBookSnapshot:
    """Parse the ``/book/{symbol}/P0`` payload into an :class:`OrderBookSnapshot`.

    Each row is ``[rate, period, count, amount]``. Bid amounts are
    positive; ask amounts are non-positive (we keep the sign).
    """
    if not isinstance(payload, list):
        raise MalformedPayload(f"expected list, got {type(payload).__name__}")
    bids: list[FundingBookRow] = []
    asks: list[FundingBookRow] = []
    for idx, raw in enumerate(payload):
        if not isinstance(raw, list) or len(raw) != 4:
            raise MalformedPayload(f"row {idx} is not [rate, period, count, amount]")
        try:
            rate = Decimal(str(raw[0]))
            period = int(raw[1])
            count = int(raw[2])
            amount = Decimal(str(raw[3]))
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise MalformedPayload(f"row {idx} has unparseable field: {exc}") from exc
        side = "bid" if amount > 0 else "ask"
        (bids if side == "bid" else asks).append(
            FundingBookRow(rate=rate, period=period, amount=amount, count=count, side=side)
        )
    bids.sort(key=lambda r: r.rate, reverse=True)
    asks.sort(key=lambda r: r.rate)
    period_two_asks = [a.rate for a in asks if a.period == 2]
    frr = min(period_two_asks) if period_two_asks else None
    return OrderBookSnapshot(
        id=None,
        symbol=symbol,
        captured_at_utc=captured_at_utc,
        bids=bids,
        asks=asks,
        frr_estimate=frr,
    )


async def fetch_funding_book(
    session: aiohttp.ClientSession, symbol: str
) -> list[Any]:
    """Fetch ``/book/{symbol}/P0?len=25`` and return the raw JSON list.

    Returns the unwrapped list payload. Raises :class:`MalformedPayload`
    if the response is not 2xx or not a JSON array. Rate-limit (429) and
    network errors propagate as-is so the polling loop can apply its
    backoff policy.
    """
    url = f"{BASE_PUBLIC_URL}/book/{symbol}/P0"
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    async with session.get(url, params={"len": "25"}, timeout=timeout) as resp:
        if resp.status == 429:
            raise RateLimited()
        if resp.status != 200:
            text = (await resp.text())[:200]
            raise MalformedPayload(f"HTTP {resp.status}: {text}")
        data = await resp.json()
    if not isinstance(data, list):
        raise MalformedPayload(f"expected list, got {type(data).__name__}")
    return data


# ===== 策略與業務邏輯 =====

_SECONDS_PER_DAY = Decimal(86400)


def decide_target_rate(snapshot: OrderBookSnapshot, cfg: Config) -> Decimal | None:
    """Compute the target rate per the configured strategy.

    Returns ``None`` when the strategy cannot produce a rate from the
    given snapshot — e.g. ``ask_book_nth`` with insufficient depth or
    ``frr_delta`` with no FRR estimate. The caller turns ``None`` into a
    "skipped" decision.
    """
    if cfg.strategy_mode == "ask_book_nth":
        n = cfg.strategy_n
        assert n is not None
        if len(snapshot.asks) < n:
            return None
        return snapshot.asks[n - 1].rate
    # frr_delta
    if snapshot.frr_estimate is None:
        return None
    delta = cfg.strategy_delta
    assert delta is not None
    return snapshot.frr_estimate + delta


def best_offer_from_book(
    snapshot: OrderBookSnapshot, cfg: Config
) -> tuple[Decimal, int] | None:
    """Scan all bid-side periods and pick the one with highest annualized return.

    The bot places an offer at the best bid rate for the chosen period,
    which means it will fill immediately (or very soon) because the bid
    side already has a buyer at that price. This is the "market maker"
    approach: match the best available demand.
    """
    if not snapshot.bids:
        return None

    # Group bids by period, take the best (highest) rate per period.
    best_by_period: dict[int, Decimal] = {}
    for bid in snapshot.bids:
        if bid.period < 2 or bid.period > 120:
            continue
        if bid.period not in best_by_period or bid.rate > best_by_period[bid.period]:
            best_by_period[bid.period] = bid.rate

    if not best_by_period:
        return None

    # Pick the period with the highest daily rate (= highest annualized).
    best_period = max(best_by_period, key=lambda p: best_by_period[p])
    best_rate = best_by_period[best_period]

    if best_rate < cfg.min_rate_threshold:
        return None

    # Place the offer AT the best bid rate so it fills immediately.
    return (best_rate, best_period)


def match_offers(
    pending: list[SimulatedOffer], snapshot: OrderBookSnapshot
) -> list[SimulatedOffer]:
    """Return the offers that would fill against the snapshot's best bid.

    Pure function. The bot is responsible for flipping the offer's
    status and creating the loan row.
    """
    if not snapshot.bids:
        return []
    best_bid = max(b.rate for b in snapshot.bids)
    return [o for o in pending if o.rate <= best_bid]


def accrued_interest(loan: SimulatedLoan, now_utc: datetime) -> Decimal:
    """Return ``principal * daily_rate * elapsed_days`` capped at the period."""
    elapsed = (now_utc - loan.started_at_utc).total_seconds()
    cap = loan.period_days * 86400
    clamped = min(max(elapsed, 0.0), float(cap))
    elapsed_days = Decimal(str(clamped)) / _SECONDS_PER_DAY
    return loan.principal * loan.daily_rate * elapsed_days


def final_interest(loan: SimulatedLoan) -> Decimal:
    return loan.principal * loan.daily_rate * Decimal(loan.period_days)


# ===== Bitfinex 認證客戶端 =====


class ExchangeError(Exception):
    """Raised when Bitfinex returns an error response."""

    def __init__(self, message: str, response: Any = None) -> None:
        self.response = response
        super().__init__(message)


class ExchangeClient:
    """Authenticated Bitfinex REST v2 client for funding operations.

    The client is intentionally narrow: it only exposes the four endpoints
    the lending bot needs. Adding more endpoints is a one-liner per method.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        api_secret: str,
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")

    # --- public surface -------------------------------------------------------

    async def submit_funding_offer(
        self,
        *,
        symbol: str,
        amount: Decimal,
        rate: Decimal,
        period: int,
        offer_type: str = "LIMIT",
    ) -> dict[str, Any]:
        """Submit a new funding offer.

        ``rate`` is the daily rate as a decimal (e.g. 0.0001 = 0.01%/day).
        ``amount`` is positive for lending. ``period`` is 2..120 days.
        Returns the raw Bitfinex response (a notification array).
        """
        body = {
            "type": offer_type,
            "symbol": symbol,
            "amount": str(amount),
            "rate": str(rate),
            "period": int(period),
            "flags": 0,
        }
        return await self._post("/v2/auth/w/funding/offer/submit", body)

    async def cancel_funding_offer(self, offer_id: int) -> dict[str, Any]:
        """Cancel an existing funding offer by its Bitfinex ID."""
        return await self._post(
            "/v2/auth/w/funding/offer/cancel", {"id": int(offer_id)}
        )

    async def active_funding_offers(self, symbol: str) -> list[Any]:
        """Return all active funding offers for ``symbol``."""
        data = await self._post(f"/v2/auth/r/funding/offers/{symbol}", {})
        if isinstance(data, list):
            return data
        return []

    async def wallets(self) -> list[Any]:
        """Return all wallet balances (funding + exchange + margin)."""
        data = await self._post("/v2/auth/r/wallets", {})
        if isinstance(data, list):
            return data
        return []

    async def funding_wallet_balance(self, currency: str) -> Decimal:
        """Return the available balance in the funding wallet for ``currency``.

        ``currency`` is e.g. ``"UST"`` (without the ``f`` prefix).
        Returns ``Decimal("0")`` if the wallet is not found.
        """
        all_wallets = await self.wallets()
        for w in all_wallets:
            # Wallet row: [WALLET_TYPE, CURRENCY, BALANCE, UNSETTLED_INTEREST, AVAILABLE_BALANCE, ...]
            if (
                isinstance(w, list)
                and len(w) >= 5
                and w[0] == "funding"
                and w[1] == currency
            ):
                return Decimal(str(w[4]))  # available balance
        return Decimal("0")

    async def active_funding_credits(self, symbol: str) -> list[Any]:
        """Return active funding credits (loans you've provided) for ``symbol``."""
        data = await self._post(f"/v2/auth/r/funding/credits/{symbol}", {})
        if isinstance(data, list):
            return data
        return []

    # --- auth plumbing --------------------------------------------------------

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        """Sign and POST to the authenticated endpoint."""
        nonce = str(int(time.time() * 1000000))
        body_json = json.dumps(body)
        signature_payload = f"/api{path}{nonce}{body_json}"
        sig = hmac.new(
            self._api_secret,
            signature_payload.encode("utf-8"),
            hashlib.sha384,
        ).hexdigest()

        headers = {
            "bfx-nonce": nonce,
            "bfx-apikey": self._api_key,
            "bfx-signature": sig,
            "content-type": "application/json",
        }

        url = f"{BASE_AUTH_URL}{path}"
        timeout = aiohttp.ClientTimeout(total=15.0)
        async with self._session.post(
            url, headers=headers, data=body_json, timeout=timeout
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                _LOGGER.error(
                    "Bitfinex auth error %d on %s: %s",
                    resp.status,
                    path,
                    data,
                )
                raise ExchangeError(
                    f"HTTP {resp.status} on {path}", response=data
                )
            # Bitfinex sometimes returns ["error", code, msg]
            if isinstance(data, list) and len(data) >= 1 and data[0] == "error":
                raise ExchangeError(
                    f"Bitfinex error: {data}", response=data
                )
            return data


# ===== TUI 應用程式主體 =====


class LoanBotApp(App):
    """Bitfinex 放貸機械人 TUI 應用程式。"""

    TITLE = "Bitfinex 放貸機械人"

    CSS = """
    #toolbar { height: auto; padding: 0 1; }
    #symbol-picker { width: 20; }
    #countdown { dock: right; width: auto; padding: 0 1; }
    #main-content { height: 1fr; }
    #market-panel { width: 1fr; border: round $accent; padding: 1; }
    #status-panel { width: 1fr; border: round $warning; padding: 1; }
    """

    BINDINGS = [("q", "quit", "退出")]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.conn: sqlite3.Connection | None = None
        self.repo: Repo | None = None
        self.session: aiohttp.ClientSession | None = None
        self.exchange: ExchangeClient | None = None
        self.last_successful_at_utc: datetime | None = None
        self._log_messages: list[str] = []
        self._browse_symbol: str | None = None  # 瀏覽其他幣種（None = 使用 config 設定）
        self._next_poll_at: float = 0.0  # 下次抓取時間戳

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="toolbar"):
            yield Select.from_values(
                BROWSE_SYMBOLS,
                value=self.cfg.funding_symbol,
                allow_blank=False,
                id="symbol-picker",
                prompt="瀏覽幣種",
                compact=True,
            )
            yield Static("", id="countdown")
        with Horizontal(id="main-content"):
            with VerticalScroll(id="market-panel"):
                yield Static("載入中...", id="market-content")
            with VerticalScroll(id="status-panel"):
                yield Static("載入中...", id="status-content")
        yield Footer()

    async def on_mount(self) -> None:
        # 開啟 DB
        db_path = pathlib.Path(self.cfg.db_path)
        if self.cfg.db_path != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = open_database(db_path)
        initialize(self.conn)
        self.repo = Repo(self.conn)

        # 建立 aiohttp session
        self.session = aiohttp.ClientSession()

        # Live 模式時建立 ExchangeClient
        if not self.cfg.dry_run:
            self.exchange = ExchangeClient(
                self.session, self.cfg.api_key, self.cfg.api_secret
            )

        # 記錄啟動訊息
        mode = "🟢 DRY-RUN 模擬" if self.cfg.dry_run else "🔴 LIVE 真實下單"
        self._log(f"啟動: {mode} | 幣種: {self.cfg.funding_symbol}")
        self._log(f"策略: {self.cfg.strategy_mode} | 金額: {self.cfg.offer_amount} | 期限: {self.cfg.period_days}天")

        # 啟動背景 workers 與 UI 刷新計時器
        self._next_poll_at = time.time() + self.cfg.polling_interval_seconds
        self.poll_worker()
        self.decision_worker()
        self.set_interval(UI_REFRESH_SEC, self.refresh_display)
        self.set_interval(1, self._update_countdown)

    def _log(self, msg: str) -> None:
        """記錄系統訊息（含時間戳記）。"""
        ts = time.strftime("%H:%M:%S")
        self._log_messages.append(f"[{ts}] {msg}")
        # 只保留最近 50 則
        if len(self._log_messages) > 50:
            self._log_messages = self._log_messages[-50:]

    def _update_countdown(self) -> None:
        """每秒更新倒數計時顯示。"""
        remain = max(0, int(self._next_poll_at - time.time()))
        self.query_one("#countdown", Static).update(f"下次抓取: {remain}s")

    def on_select_changed(self, event: Select.Changed) -> None:
        """處理幣種選擇變更。"""
        if event.value == Select.NULL:
            return
        if event.select.id == "symbol-picker":
            self._browse_symbol = str(event.value)
            self._log(f"瀏覽切換至 {self._browse_symbol}")
            self._fetch_browse_symbol()

    # --- background workers ---

    @work(group="poll", exclusive=True)
    async def poll_worker(self) -> None:
        """定時抓取訂單簿（取代原 Poller.run）。"""
        interval = self.cfg.polling_interval_seconds
        while True:
            await self._poll_tick()
            self._next_poll_at = time.time() + interval
            await asyncio.sleep(interval)

    @work(group="browse", exclusive=True)
    async def _fetch_browse_symbol(self) -> None:
        """即時抓取所選幣種的行情並更新 Market Panel。"""
        symbol = self._browse_symbol
        if not symbol or not self.session:
            return
        try:
            payload = await fetch_funding_book(self.session, symbol)
            now = datetime.now(tz=timezone.utc)
            snap = parse_funding_book(payload, symbol, now)
            self._render_browse_snapshot(snap)
        except Exception as exc:
            self._log(f"⚠ 瀏覽 {symbol} 失敗：{type(exc).__name__}")

    async def _poll_tick(self) -> None:
        """單次抓取+解析+寫DB+觸發撮合。"""
        symbol = self.cfg.funding_symbol
        try:
            payload = await self._fetch_with_one_retry(symbol)
        except RateLimited:
            self._log(f"⚠ 速率限制，暫停 {int(RATE_LIMIT_PAUSE)} 秒")
            await asyncio.sleep(RATE_LIMIT_PAUSE)
            return
        except (MalformedPayload, aiohttp.ClientError, TimeoutError, Exception) as exc:
            self._log(f"⚠ 抓取失敗：{type(exc).__name__}")
            return

        try:
            now = datetime.now(tz=timezone.utc)
            snapshot = parse_funding_book(payload, symbol, now)
        except MalformedPayload as exc:
            self._log(f"⚠ 資料格式異常：{exc}")
            return

        try:
            snapshot_id = await asyncio.to_thread(self.repo.insert_snapshot, snapshot)
        except Exception:
            self._log("⚠ DB 寫入失敗")
            return

        self.last_successful_at_utc = snapshot.captured_at_utc

        # 觸發撮合
        try:
            await asyncio.to_thread(self._match_against_latest)
        except Exception:
            self._log("⚠ 撮合失敗")

    async def _fetch_with_one_retry(self, symbol: str) -> list:
        """抓取訂單簿，失敗等 RETRY_DELAY 秒後重試一次。"""
        assert self.session is not None
        try:
            return await fetch_funding_book(self.session, symbol)
        except (aiohttp.ClientError, TimeoutError, MalformedPayload):
            await asyncio.sleep(RETRY_DELAY)
            return await fetch_funding_book(self.session, symbol)

    @work(group="decision", exclusive=True)
    async def decision_worker(self) -> None:
        """定時決策是否掛單（取代原 Bot.run）。"""
        interval = self.cfg.decision_interval_seconds
        while True:
            try:
                if self.cfg.dry_run:
                    await asyncio.to_thread(self._decision_tick)
                else:
                    await self._decision_tick_live()
            except Exception as exc:
                self._log(f"⚠ 決策失敗：{type(exc).__name__}")
            await asyncio.sleep(interval)

    # --- decision helpers ---

    def _decision_tick(self) -> None:
        """Dry-run 模式：refresh_loans → 取最新快照 → 根據策略計算利率 → 寫入 SimulatedOffer。"""
        now = datetime.now(tz=timezone.utc)
        self._refresh_loans(now)
        snapshot = self.repo.latest_snapshot(self.cfg.funding_symbol)
        if snapshot is None:
            return
        age = (now - snapshot.captured_at_utc).total_seconds()
        if age > self.cfg.snapshot_freshness_seconds:
            return

        # Determine rate + period based on strategy mode.
        if self.cfg.strategy_mode == "best_period":
            result = best_offer_from_book(snapshot, self.cfg)
            if result is None:
                return
            target, period = result
        else:
            target = decide_target_rate(snapshot, self.cfg)
            if target is None or target < self.cfg.min_rate_threshold:
                return
            period = self.cfg.period_days

        # In dry-run mode, just write a local simulated offer.
        offer = SimulatedOffer(
            id=None,
            created_at_utc=now,
            symbol=self.cfg.funding_symbol,
            rate=target,
            amount=self.cfg.offer_amount,
            period_days=period,
            status="pending",
        )
        self.repo.insert_offer(offer)
        self._log(f"✓ 模擬掛單：年化 {float(target) * 365 * 100:.2f}% / {self.cfg.offer_amount} / {period}天")

    async def _decision_tick_live(self) -> None:
        """Live 模式：檢查餘額 → 檢查曝險 → 取消過期掛單 → 計算利率 → 提交真實掛單。"""
        assert self.exchange is not None
        now = datetime.now(tz=timezone.utc)
        cfg = self.cfg
        symbol = cfg.funding_symbol
        currency = symbol[1:]  # "fUST" -> "UST"

        # 1. Check wallet balance
        try:
            available = await self.exchange.funding_wallet_balance(currency)
        except ExchangeError as exc:
            self._log(f"⚠ 無法讀取錢包：{exc}")
            return
        if available < cfg.offer_amount:
            self._log(f"⚠ 餘額不足：{available} < {cfg.offer_amount}")
            return

        # 2. Check total exposure
        try:
            active_offers = await self.exchange.active_funding_offers(symbol)
            active_credits = await self.exchange.active_funding_credits(symbol)
        except ExchangeError as exc:
            self._log(f"⚠ 無法讀取活躍掛單/貸款：{exc}")
            return
        total_exposure = Decimal("0")
        for o in active_offers:
            if isinstance(o, list) and len(o) > 4:
                total_exposure += abs(Decimal(str(o[4])))  # amount field
        for c in active_credits:
            if isinstance(c, list) and len(c) > 5:
                total_exposure += abs(Decimal(str(c[5])))  # amount field
        if total_exposure + cfg.offer_amount > cfg.max_total_amount:
            self._log(f"⚠ 曝險超限：{total_exposure} + {cfg.offer_amount} > {cfg.max_total_amount}")
            return

        # 3. Auto-cancel stale offers
        if cfg.cancel_after_minutes > 0:
            await self._cancel_stale_offers(active_offers, now)

        # 4. Refresh loans
        await asyncio.to_thread(self._refresh_loans, now)

        # 5. Decide target rate from latest snapshot
        snapshot = await asyncio.to_thread(
            self.repo.latest_snapshot, cfg.funding_symbol
        )
        if snapshot is None:
            return
        age = (now - snapshot.captured_at_utc).total_seconds()
        if age > cfg.snapshot_freshness_seconds:
            return

        if cfg.strategy_mode == "best_period":
            result = best_offer_from_book(snapshot, cfg)
            if result is None:
                return
            target, period = result
        else:
            target = decide_target_rate(snapshot, cfg)
            if target is None or target < cfg.min_rate_threshold:
                return
            period = cfg.period_days

        # 6. Submit real offer
        try:
            await self.exchange.submit_funding_offer(
                symbol=symbol,
                amount=cfg.offer_amount,
                rate=target,
                period=period,
            )
            self._log(f"✓ 真實掛單：年化 {float(target) * 365 * 100:.2f}% / {cfg.offer_amount}")
        except ExchangeError as exc:
            self._log(f"⚠ 掛單失敗：{exc}")

    def _match_against_latest(self) -> None:
        """撮合邏輯：取最新快照 + pending offers → match_offers → mark_offer_filled + insert_loan。"""
        snapshot = self.repo.latest_snapshot(self.cfg.funding_symbol)
        if snapshot is None:
            return
        pending = self.repo.pending_offers_oldest_first()
        for offer in match_offers(pending, snapshot):
            if offer.id is None:
                continue
            self.repo.mark_offer_filled(offer.id)
            loan = SimulatedLoan(
                id=None,
                offer_id=offer.id,
                started_at_utc=snapshot.captured_at_utc,
                expires_at_utc=snapshot.captured_at_utc
                + timedelta(days=offer.period_days),
                principal=offer.amount,
                daily_rate=offer.rate,
                period_days=offer.period_days,
                symbol=offer.symbol,
                status="active",
                accrued_interest=Decimal("0"),
                final_interest=None,
            )
            self.repo.insert_loan(loan)

    def _refresh_loans(self, now: datetime) -> None:
        """更新活躍貸款的累計利息，到期則 close。"""
        for loan in self.repo.active_loans(limit=200):
            if loan.id is None:
                continue
            if now >= loan.expires_at_utc:
                self.repo.close_loan(loan.id, final_interest(loan))
            else:
                new_accrued = accrued_interest(loan, now)
                if new_accrued != loan.accrued_interest:
                    self.repo.update_loan_accrued(loan.id, new_accrued)

    async def _cancel_stale_offers(
        self, active_offers: list, now: datetime
    ) -> None:
        """Live 模式取消超時掛單。"""
        assert self.exchange is not None
        cutoff_ms = int(
            (now - timedelta(minutes=self.cfg.cancel_after_minutes)).timestamp() * 1000
        )
        for o in active_offers:
            if not isinstance(o, list) or len(o) < 5:
                continue
            offer_id = int(o[0])
            created_ms = int(o[3]) if len(o) > 3 else 0
            if created_ms < cutoff_ms:
                try:
                    await self.exchange.cancel_funding_offer(offer_id)
                    self._log(f"✓ 取消過期掛單 #{offer_id}")
                except ExchangeError as exc:
                    self._log(f"⚠ 取消掛單 #{offer_id} 失敗：{exc}")

    # --- UI refresh ---

    def refresh_display(self) -> None:
        """定時刷新 TUI 顯示（Market + Status panels）。"""
        if self.repo is None:
            return
        try:
            self._render_market_panel()
            self._render_status_panel()
        except Exception:
            pass  # 避免刷新例外影響主迴圈

    def _render_market_panel(self) -> None:
        """重繪左側市場面板。"""
        # 如果正在瀏覽其他幣種且不是 config 設定的幣種，不覆蓋瀏覽結果
        if self._browse_symbol and self._browse_symbol != self.cfg.funding_symbol:
            return
        cfg = self.cfg
        snap = self.repo.latest_snapshot(cfg.funding_symbol)
        now = datetime.now(tz=timezone.utc)

        # 模式徽章
        mode_badge = "🟢 模擬" if cfg.dry_run else "🔴 LIVE"

        lines: list[str] = [f"[bold]{mode_badge} | {cfg.funding_symbol}[/bold]", ""]

        if snap is None:
            lines.append("[dim]尚未取得行情資料[/dim]")
        else:
            age = int((now - snap.captured_at_utc).total_seconds())
            # 過期判斷
            stale = (
                self.last_successful_at_utc is None
                or (now - self.last_successful_at_utc).total_seconds()
                > cfg.polling_interval_seconds * 3
            )
            if stale:
                lines.append("[bold red]⚠️ 資料可能已過期[/bold red]")

            # FRR
            if snap.frr_estimate:
                frr_annual = f"{float(snap.frr_estimate) * 365 * 100:.2f}"
                lines.append(
                    f"FRR: [bold green]{frr_annual}%[/bold green] 年化"
                    f" ({snap.frr_estimate}/日) · {age}s 前"
                )
            else:
                lines.append(f"FRR: N/A · {age}s 前")

            lines.append("")

            # Bids
            lines.append("[bold cyan]Bids — 借款人出價[/bold cyan]")
            lines.append("[dim]期限   年化      日Rate         金額[/dim]")
            for b in snap.bids[:8]:
                ann = f"{float(b.rate) * 365 * 100:.2f}"
                amt = f"{abs(float(b.amount)):,.0f}"
                lines.append(
                    f" {b.period:>3}d  {ann:>7}%  {float(b.rate):.8f}  {amt:>10}"
                )

            lines.append("")

            # Asks
            lines.append("[bold yellow]Asks — 出借人掛單[/bold yellow]")
            lines.append("[dim]期限   年化      日Rate         金額[/dim]")
            for a in snap.asks[:8]:
                ann = f"{float(a.rate) * 365 * 100:.2f}"
                amt = f"{abs(float(a.amount)):,.0f}"
                lines.append(
                    f" {a.period:>3}d  {ann:>7}%  {float(a.rate):.8f}  {amt:>10}"
                )

        self.query_one("#market-content", Static).update("\n".join(lines))

    def _render_browse_snapshot(self, snap: OrderBookSnapshot) -> None:
        """瀏覽模式：顯示指定幣種的即時行情。"""
        lines: list[str] = [f"[bold]瀏覽: {snap.symbol}[/bold]", ""]

        if snap.frr_estimate:
            frr_annual = f"{float(snap.frr_estimate) * 365 * 100:.2f}"
            lines.append(f"FRR: [bold green]{frr_annual}%[/bold green] 年化")
        else:
            lines.append("FRR: N/A")
        lines.append("")

        lines.append("[bold cyan]Bids — 借款人出價[/bold cyan]")
        lines.append("[dim]期限   年化      日Rate         金額[/dim]")
        for b in snap.bids[:8]:
            ann = f"{float(b.rate) * 365 * 100:.2f}"
            amt = f"{abs(float(b.amount)):,.0f}"
            lines.append(f" {b.period:>3}d  {ann:>7}%  {float(b.rate):.8f}  {amt:>10}")
        lines.append("")

        lines.append("[bold yellow]Asks — 出借人掛單[/bold yellow]")
        lines.append("[dim]期限   年化      日Rate         金額[/dim]")
        for a in snap.asks[:8]:
            ann = f"{float(a.rate) * 365 * 100:.2f}"
            amt = f"{abs(float(a.amount)):,.0f}"
            lines.append(f" {a.period:>3}d  {ann:>7}%  {float(a.rate):.8f}  {amt:>10}")

        self.query_one("#market-content", Static).update("\n".join(lines))

    def _render_status_panel(self) -> None:
        """重繪右側狀態面板（掛單/貸款/收益/系統訊息）。"""
        lines: list[str] = []

        # 收益統計
        realized = self.repo.total_realized_interest()
        unrealized = self.repo.total_unrealized_interest()
        total = realized + unrealized
        lines.append("[bold]💰 收益[/bold]")
        lines.append(f"  已實現: {float(realized):.8f} USDT")
        lines.append(f"  未實現: {float(unrealized):.8f} USDT")
        lines.append(f"  合計:   [bold green]{float(total):.8f}[/bold green] USDT")
        lines.append("")

        # 系統統計
        c = self.repo.conn
        snaps = c.execute("SELECT COUNT(*) FROM order_book_snapshot").fetchone()[0]
        offers_total = c.execute(
            "SELECT COUNT(*) FROM simulated_offer"
        ).fetchone()[0]
        filled = c.execute(
            "SELECT COUNT(*) FROM simulated_offer WHERE status='filled'"
        ).fetchone()[0]
        active_count = c.execute(
            "SELECT COUNT(*) FROM simulated_loan WHERE status='active'"
        ).fetchone()[0]
        closed_count = c.execute(
            "SELECT COUNT(*) FROM simulated_loan WHERE status='closed'"
        ).fetchone()[0]
        fill_rate = (
            f"{filled / offers_total * 100:.0f}%" if offers_total > 0 else "—"
        )

        lines.append("[bold]📊 統計[/bold]")
        lines.append(
            f"  快照: {snaps} | 掛單: {offers_total} | 成交率: {fill_rate}"
        )
        lines.append(f"  活躍貸款: {active_count} | 已結算: {closed_count}")
        lines.append("")

        # 待成交掛單
        pending = self.repo.pending_offers(limit=5)
        lines.append("[bold]📋 待成交掛單[/bold]")
        if not pending:
            lines.append("  [dim]目前無資料[/dim]")
        else:
            for o in pending:
                ann = f"{float(o.rate) * 365 * 100:.2f}"
                ts = o.created_at_utc.strftime("%H:%M")
                lines.append(
                    f"  {ts} | {ann}% 年化 | {o.amount} | {o.period_days}天"
                )
        lines.append("")

        # 活躍貸款
        active = self.repo.active_loans(limit=5)
        lines.append("[bold]🏦 活躍貸款[/bold]")
        if not active:
            lines.append("  [dim]目前無資料[/dim]")
        else:
            for loan in active:
                ann = f"{float(loan.daily_rate) * 365 * 100:.2f}"
                exp = loan.expires_at_utc.strftime("%m/%d %H:%M")
                lines.append(
                    f"  到期 {exp} | {ann}% | {loan.principal}"
                    f" | 利息 {float(loan.accrued_interest):.8f}"
                )
        lines.append("")

        # 系統訊息
        lines.append("[bold]📝 系統訊息[/bold]")
        if not self._log_messages:
            lines.append("  [dim]無訊息[/dim]")
        else:
            for msg in self._log_messages[-10:]:
                lines.append(f"  {msg}")

        self.query_one("#status-content", Static).update("\n".join(lines))

    # --- graceful shutdown ---

    async def action_quit(self) -> None:
        """退出：取消背景工作、關閉資源。"""
        self.workers.cancel_all()
        if self.session and not self.session.closed:
            await self.session.close()
        if self.conn:
            self.conn.close()
        self.exit()

# ===== 進入點 =====


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="loanbot_tui",
        description="Bitfinex 放貸機械人 — Textual TUI 版",
    )
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=pathlib.Path("loanbot_data/config.toml"),
        help="設定檔路徑 (預設: ./loanbot_data/config.toml)",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    cfg = load_config_or_exit(args.config)

    # Live 模式檢查 API 金鑰
    if not cfg.dry_run:
        if not cfg.api_key or not cfg.api_secret:
            sys.stderr.write("error: dry_run=false 但 api_key / api_secret 為空\n")
            raise SystemExit(2)

    app = LoanBotApp(cfg)
    app.run()


if __name__ == "__main__":
    main()
