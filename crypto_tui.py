"""加密貨幣現貨價格監控器 — Textual TUI 版本。

使用 Binance 公開 Spot API（免費、無需 API Key）顯示主流加密貨幣即時價格與 24h 漲跌。

執行方式：
    python crypto_tui.py
"""

from __future__ import annotations

import time

import requests
from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Static

# ===== 常數 =====

TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"
REFRESH_SEC = 10
REFRESH_OPTIONS = [5, 10, 15, 30, 60]

# 監控的交易對
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT", "APTUSDT",
    "NEARUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "TONUSDT",
    "TRXUSDT", "SHIBUSDT", "PEPEUSDT", "FILUSDT", "ICPUSDT",
    "HBARUSDT", "INJUSDT", "RENDERUSDT", "FETUSDT", "THETAUSDT",
    "AAVEUSDT", "MKRUSDT", "GRTUSDT", "STXUSDT", "IMXUSDT",
    "RUNEUSDT", "TIAUSDT", "SEIUSDT", "JUPUSDT", "ENAUSDT",
]

COLORS = [
    "#00ff00", "#00ffff", "#ffff00", "#ff00ff", "#ff0000",
    "#ff8c00", "#00ff7f", "#1e90ff", "#ff69b4", "#7b68ee",
    "#ffd700", "#00ced1", "#ff6347", "#adff2f", "#da70d6",
    "#40e0d0", "#f0e68c", "#87ceeb", "#dda0dd", "#98fb98",
]


def fetch_tickers() -> list[dict] | None:
    """從 Binance 抓取所有監控幣種的 24h 行情。斷網重試一次。"""
    for attempt in range(2):
        try:
            results = []
            for symbol in SYMBOLS:
                r = requests.get(TICKER_URL, params={"symbol": symbol}, timeout=10)
                if r.status_code == 200:
                    results.append(r.json())
            if results:
                return results
            if attempt == 0:
                time.sleep(3)
                continue
            return None
        except Exception:
            if attempt == 0:
                time.sleep(3)
                continue
            return None
    return None


def fetch_tickers_batch() -> list[dict] | None:
    """嘗試批量抓取（更高效）。"""
    for attempt in range(2):
        try:
            r = requests.get(TICKER_URL, timeout=10)
            if r.status_code != 200:
                if attempt == 0:
                    time.sleep(3)
                    continue
                return None
            all_tickers = r.json()
            # 過濾出我們要的 symbols
            symbol_set = set(SYMBOLS)
            return [t for t in all_tickers if t.get("symbol") in symbol_set]
        except Exception:
            if attempt == 0:
                time.sleep(3)
                continue
            return None
    return None


class CryptoApp(App):
    """加密貨幣現貨價格監控 TUI。"""

    TITLE = "加密貨幣價格監控"

    CSS = """
    #countdown { height: auto; padding: 0 1; text-align: right; }
    #main-area { height: 1fr; padding: 1; }
    """

    BINDINGS = [
        ("ctrl+up", "speed_up", "加快刷新"),
        ("ctrl+down", "slow_down", "減慢刷新"),
        ("q", "quit", "退出"),
    ]

    def __init__(self):
        super().__init__()
        self.refresh_index = 1  # 預設 10 秒
        self._next_refresh_at: float = 0.0
        self._refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="countdown")
        with VerticalScroll(id="main-area"):
            yield Static("載入中...", id="prices-content")
        yield Footer()

    def on_mount(self) -> None:
        self._next_refresh_at = time.time() + REFRESH_SEC
        self.fetch_worker()
        self._refresh_timer = self.set_interval(REFRESH_SEC, self.fetch_worker)
        self.set_interval(1, self._update_countdown)

    @work(thread=True, exclusive=True)
    def fetch_worker(self) -> None:
        data = fetch_tickers_batch()
        if data is not None:
            self.call_from_thread(self._render_prices, data)
        else:
            self.call_from_thread(
                self.query_one("#prices-content", Static).update,
                "[red]⚠ 抓取失敗，稍後重試[/red]",
            )
        self._next_refresh_at = time.time() + REFRESH_OPTIONS[self.refresh_index]

    def _render_prices(self, data: list[dict]) -> None:
        # 依 SYMBOLS 順序排列
        symbol_order = {s: i for i, s in enumerate(SYMBOLS)}
        data.sort(key=lambda d: symbol_order.get(d.get("symbol", ""), 999))

        lines = [
            f"[bold white]更新: {time.strftime('%H:%M:%S')} | "
            f"每 {REFRESH_OPTIONS[self.refresh_index]} 秒刷新 | "
            f"共 {len(data)} 幣[/bold white]",
            "[dim]" + "─" * 70 + "[/dim]",
            "[dim] 幣種         價格 (USDT)      24h漲跌      24h高        24h低        成交量[/dim]",
            "[dim]" + "─" * 70 + "[/dim]",
        ]

        for i, t in enumerate(data):
            symbol = t.get("symbol", "???")
            coin = symbol.replace("USDT", "")
            price = float(t.get("lastPrice", 0))
            change_pct = float(t.get("priceChangePercent", 0))
            high = float(t.get("highPrice", 0))
            low = float(t.get("lowPrice", 0))
            volume = float(t.get("quoteVolume", 0))

            # 漲跌顏色
            if change_pct > 0:
                change_color = "#00ff00"
                arrow = "▲"
            elif change_pct < 0:
                change_color = "#ff0000"
                arrow = "▼"
            else:
                change_color = "#888888"
                arrow = "─"

            # 行顏色
            row_color = COLORS[i % len(COLORS)]

            # 格式化成交量
            if volume >= 1_000_000_000:
                vol_str = f"{volume / 1_000_000_000:.1f}B"
            elif volume >= 1_000_000:
                vol_str = f"{volume / 1_000_000:.1f}M"
            elif volume >= 1_000:
                vol_str = f"{volume / 1_000:.1f}K"
            else:
                vol_str = f"{volume:.0f}"

            # 格式化價格（大幣種用逗號分隔，小幣種顯示更多小數）
            if price >= 100:
                price_str = f"{price:,.2f}"
                high_str = f"{high:,.2f}"
                low_str = f"{low:,.2f}"
            elif price >= 1:
                price_str = f"{price:.4f}"
                high_str = f"{high:.4f}"
                low_str = f"{low:.4f}"
            else:
                price_str = f"{price:.6f}"
                high_str = f"{high:.6f}"
                low_str = f"{low:.6f}"

            change_str = f"[{change_color}]{arrow}{abs(change_pct):>5.2f}%[/{change_color}]"

            lines.append(
                f"[{row_color}] {coin:<8} {price_str:>14}  "
                f"{change_str}  "
                f"[{row_color}]{high_str:>12}  {low_str:>12}  {vol_str:>8}[/{row_color}]"
            )

        content = "\n".join(lines)
        self.query_one("#prices-content", Static).update(content)

    def _update_countdown(self) -> None:
        remain = max(0, int(self._next_refresh_at - time.time()))
        self.query_one("#countdown", Static).update(f"下次更新: {remain}s")

    def _reset_refresh_timer(self) -> None:
        """停止舊的刷新計時器並建立新的。"""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
        self._refresh_timer = self.set_interval(REFRESH_SEC, self.fetch_worker)

    def action_speed_up(self) -> None:
        global REFRESH_SEC
        if self.refresh_index > 0:
            self.refresh_index -= 1
        REFRESH_SEC = REFRESH_OPTIONS[self.refresh_index]
        self._reset_refresh_timer()

    def action_slow_down(self) -> None:
        global REFRESH_SEC
        if self.refresh_index < len(REFRESH_OPTIONS) - 1:
            self.refresh_index += 1
        REFRESH_SEC = REFRESH_OPTIONS[self.refresh_index]
        self._reset_refresh_timer()


if __name__ == "__main__":
    CryptoApp().run()
