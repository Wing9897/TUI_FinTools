"""美股指數與個股監控器 — Textual TUI 版本。

使用 Yahoo Finance 公開端點（免費、無需 API Key）顯示美股指數與熱門個股即時報價。

執行方式：
    python stocks_tui.py
"""

from __future__ import annotations

import time

import requests
from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Static

# ===== 常數 =====

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
REFRESH_SEC = 30
REFRESH_OPTIONS = [15, 30, 60, 120, 300]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 監控的股票/指數
SYMBOLS = [
    # 指數 ETF
    ("SPY", "S&P 500 ETF"),
    ("QQQ", "Nasdaq 100 ETF"),
    ("DIA", "Dow Jones ETF"),
    ("IWM", "Russell 2000 ETF"),
    ("VIX", "恐慌指數 VIX"),
    # 科技巨頭
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("GOOGL", "Google"),
    ("AMZN", "Amazon"),
    ("NVDA", "Nvidia"),
    ("META", "Meta"),
    ("TSLA", "Tesla"),
    # 金融/其他
    ("JPM", "JP Morgan"),
    ("V", "Visa"),
    ("WMT", "Walmart"),
    # 加密相關
    ("COIN", "Coinbase"),
    ("MSTR", "MicroStrategy"),
    ("MARA", "Marathon Digital"),
    # 港股 ADR
    ("BABA", "阿里巴巴"),
    ("PDD", "拼多多"),
]

COLORS = [
    "#00ff00", "#00ffff", "#ffff00", "#ff00ff", "#ff0000",
    "#ff8c00", "#00ff7f", "#1e90ff", "#ff69b4", "#7b68ee",
    "#ffd700", "#00ced1", "#ff6347", "#adff2f", "#da70d6",
    "#40e0d0", "#f0e68c", "#87ceeb", "#dda0dd", "#98fb98",
]


def fetch_quote(symbol: str) -> dict | None:
    """從 Yahoo Finance 抓取單一股票/指數報價。"""
    try:
        url = YAHOO_CHART_URL.format(symbol=symbol)
        r = requests.get(url, params={"interval": "1d", "range": "1d"}, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice", 0)
        prev_close = meta.get("chartPreviousClose", 0)
        return {
            "symbol": symbol,
            "price": float(price),
            "prev_close": float(prev_close),
            "currency": meta.get("currency", "USD"),
        }
    except Exception:
        return None


def fetch_all_quotes() -> list[dict]:
    """併發抓取所有監控股票的報價（使用 ThreadPoolExecutor 加速）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        for symbol, name in SYMBOLS:
            future = executor.submit(fetch_quote, symbol)
            futures[future] = (symbol, name)

        for future in as_completed(futures, timeout=30):
            symbol, name = futures[future]
            try:
                quote = future.result()
                if quote:
                    quote["name"] = name
                    results.append(quote)
            except Exception:
                pass

    # 按 SYMBOLS 原始順序排列
    order = {s: i for i, (s, _) in enumerate(SYMBOLS)}
    results.sort(key=lambda q: order.get(q["symbol"], 999))
    return results


class StocksApp(App):
    """美股指數與個股監控 TUI。"""

    TITLE = "美股指數監控"

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
        self.refresh_index = 1  # 預設 30 秒
        self._next_refresh_at: float = 0.0
        self._refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="countdown")
        with VerticalScroll(id="main-area"):
            yield Static("載入中...", id="stocks-content")
        yield Footer()

    def on_mount(self) -> None:
        self._next_refresh_at = time.time() + REFRESH_SEC
        self.fetch_worker()
        self._refresh_timer = self.set_interval(REFRESH_SEC, self.fetch_worker)
        self.set_interval(1, self._update_countdown)

    @work(thread=True, exclusive=True)
    def fetch_worker(self) -> None:
        data = fetch_all_quotes()
        if data:
            self.call_from_thread(self._render_stocks, data)
        else:
            self.call_from_thread(
                self.query_one("#stocks-content", Static).update,
                "[red]⚠ 抓取失敗，稍後重試（非交易時段報價可能為上一收盤價）[/red]",
            )
        self._next_refresh_at = time.time() + REFRESH_OPTIONS[self.refresh_index]

    def _render_stocks(self, data: list[dict]) -> None:
        lines = [
            f"[bold white]更新: {time.strftime('%H:%M:%S')} | "
            f"每 {REFRESH_OPTIONS[self.refresh_index]}秒 | "
            f"共 {len(data)} 檔[/bold white]",
            "[dim]" + "─" * 65 + "[/dim]",
            "[dim] 代碼       名稱              價格          漲跌      漲跌%[/dim]",
            "[dim]" + "─" * 65 + "[/dim]",
        ]

        for i, q in enumerate(data):
            symbol = q["symbol"]
            name = q["name"]
            price = q["price"]
            prev = q["prev_close"]
            change = price - prev
            change_pct = (change / prev * 100) if prev != 0 else 0

            if change > 0:
                arrow = "▲"
                change_color = "#00ff00"
            elif change < 0:
                arrow = "▼"
                change_color = "#ff0000"
            else:
                arrow = "─"
                change_color = "#888888"

            row_color = COLORS[i % len(COLORS)]

            lines.append(
                f"[{row_color}] {symbol:<8}  {name:<16}  "
                f"${price:>10,.2f}  "
                f"[{change_color}]{arrow}{abs(change):>7,.2f}  {arrow}{abs(change_pct):>5.2f}%[/{change_color}]"
                f"[/{row_color}]"
            )

        lines.append("")
        lines.append("[dim]⚠ 非交易時段顯示上一收盤價[/dim]")

        self.query_one("#stocks-content", Static).update("\n".join(lines))

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
    StocksApp().run()
