"""美股供應量與標準化價格監控器 — Textual TUI 版本。

使用 yahooquery 套件（asynchronous=True）從 Yahoo Finance 批量獲取美股的
當前價格、市值與流通股數，並計算假設統一 1,000,000,000（10 億）股流通量下的
「標準化價格」，方便用戶比較不同股票在相同股數條件下的市值含義。

執行方式：
    python stock_supply.py
"""

from __future__ import annotations

import time

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Static
from yahooquery import Ticker

# ===== 常數 =====

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

REFRESH_OPTIONS = [15, 30, 60, 120, 300]  # 秒
DEFAULT_REFRESH_INDEX = 2  # 預設 60 秒
NORMALIZED_SUPPLY = 1_000_000_000  # 10 億
SORT_MODES = ["market_cap_rank", "normalized_desc", "normalized_asc"]
SORT_LABELS = {
    "market_cap_rank": "排序: 市值排名",
    "normalized_desc": "排序: 標準化價格 ↓",
    "normalized_asc": "排序: 標準化價格 ↑",
}
COLORS = [
    "#00ff00", "#00ffff", "#ffff00", "#ff00ff", "#ff0000",
    "#ff8c00", "#00ff7f", "#1e90ff", "#ff69b4", "#7b68ee",
    "#ffd700", "#00ced1", "#ff6347", "#adff2f", "#da70d6",
    "#40e0d0", "#f0e68c", "#87ceeb", "#dda0dd", "#98fb98",
]


# ===== 工具函式 =====


def calculate_normalized_price(market_cap: float | None) -> float | None:
    """計算標準化價格：market_cap / 1,000,000,000。

    若 market_cap 為 None、零或負數則回傳 None。
    """
    if market_cap is None or market_cap <= 0:
        return None
    return market_cap / NORMALIZED_SUPPLY


def format_price(price: float | None) -> str:
    """依價格大小選擇精度格式化。

    - price >= 100: 千位分隔符 + 2 位小數
    - price >= 1:   4 位小數
    - price < 1:    6 位小數
    - None:         "N/A"
    """
    if price is None:
        return "N/A"
    if price >= 100:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.6f}"


def format_supply(supply: float | None) -> str:
    """供應量縮寫格式化。

    - >= 1B: "{x}B"（1 位小數）
    - >= 1M: "{x}M"（1 位小數）
    - >= 1K: "{x}K"（1 位小數）
    - < 1K:  原始數值
    - None:  "N/A"
    """
    if supply is None:
        return "N/A"
    if supply >= 1_000_000_000:
        return f"{supply / 1_000_000_000:.1f}B"
    if supply >= 1_000_000:
        return f"{supply / 1_000_000:.1f}M"
    if supply >= 1_000:
        return f"{supply / 1_000:.1f}K"
    return f"{supply:.0f}"


def format_market_cap(market_cap: float | None) -> str:
    """市值縮寫格式化（兆/億級）。

    - >= 1T (1兆): "{x}T"（2 位小數）
    - >= 1B (10億): "{x}B"（1 位小數）
    - >= 1M (百萬): "{x}M"（1 位小數）
    - < 1M: 千位分隔 + 2 位小數
    - None: "N/A"
    """
    if market_cap is None:
        return "N/A"
    if market_cap >= 1_000_000_000_000:
        return f"{market_cap / 1_000_000_000_000:.2f}T"
    if market_cap >= 1_000_000_000:
        return f"{market_cap / 1_000_000_000:.1f}B"
    if market_cap >= 1_000_000:
        return f"{market_cap / 1_000_000:.1f}M"
    return f"{market_cap:,.2f}"


def sort_stocks(stocks: list[dict], mode: str) -> list[dict]:
    """依指定模式排序股票清單。

    - "market_cap_rank": 按 market_cap 降序（市值最大排第一），None 排最後
    - "normalized_desc": 按 normalized_price 降序，N/A 排最後
    - "normalized_asc":  按 normalized_price 升序，N/A 排最後

    回傳排序後的新清單（不修改原始輸入）。
    """
    if mode == "market_cap_rank":
        return sorted(
            stocks,
            key=lambda s: (
                s.get("market_cap") is None,
                -(s.get("market_cap") if s.get("market_cap") is not None else 0),
            ),
        )
    elif mode == "normalized_desc":
        return sorted(
            stocks,
            key=lambda s: (
                s.get("normalized_price") is None,
                -(s.get("normalized_price") if s.get("normalized_price") is not None else 0),
            ),
        )
    elif mode == "normalized_asc":
        return sorted(
            stocks,
            key=lambda s: (
                s.get("normalized_price") is None,
                s.get("normalized_price") if s.get("normalized_price") is not None else 0,
            ),
        )
    # 未知模式，回傳原順序
    return list(stocks)


# ===== 資料抓取 =====


def fetch_stock_data() -> list[dict] | None:
    """使用 yahooquery 批量獲取所有美股的即時價格資料。

    回傳 list[dict]，每個 dict 包含：
        - symbol: 股票代碼
        - name: 股票名稱（shortName，若無則使用 SYMBOLS 中的名稱）
        - regularMarketPrice: 當前價格
        - marketCap: 市值
        - sharesOutstanding: 流通股數

    失敗時回傳 None。
    """
    try:
        symbols_list = [s[0] for s in SYMBOLS]
        # 建立名稱對照表，用於 shortName 為空時的 fallback
        name_map = {s[0]: s[1] for s in SYMBOLS}

        ticker = Ticker(symbols_list, asynchronous=True, timeout=15)
        price_data = ticker.price

        # 驗證回傳資料是否為有效字典
        if not isinstance(price_data, dict):
            return None

        results = []
        for symbol in symbols_list:
            stock_info = price_data.get(symbol)
            if not isinstance(stock_info, dict):
                continue

            short_name = stock_info.get("shortName")
            name = short_name if short_name else name_map.get(symbol, symbol)

            price = stock_info.get("regularMarketPrice")
            market_cap = stock_info.get("marketCap")
            shares = stock_info.get("sharesOutstanding")

            # fallback: 從市值與價格反推流通股數
            if shares is None and price and market_cap and price > 0:
                shares = market_cap / price

            results.append({
                "symbol": symbol,
                "name": name,
                "regularMarketPrice": price,
                "marketCap": market_cap,
                "sharesOutstanding": shares,
            })

        return results if results else None
    except Exception:
        return None


# ===== Textual TUI 應用程式 =====


class StockSupplyApp(App):
    """美股供應量與標準化價格監控 TUI。"""

    TITLE = "美股供應量與標準化價格"

    CSS = """
    #countdown { height: auto; padding: 0 1; text-align: right; }
    #main-area { height: 1fr; padding: 1; }
    """

    BINDINGS = [
        ("ctrl+up", "speed_up", "加快刷新"),
        ("ctrl+down", "slow_down", "減慢刷新"),
        ("s", "toggle_sort", "切換排序"),
        ("q", "quit", "退出"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.refresh_index: int = DEFAULT_REFRESH_INDEX
        self.sort_mode_index: int = 0
        self._next_refresh_at: float = 0.0
        self._refresh_timer = None
        self._last_data: list[dict] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="countdown")
        with VerticalScroll(id="main-area"):
            yield Static("載入中...", id="content")
        yield Footer()

    def on_mount(self) -> None:
        """啟動初始抓取、設定刷新計時器與倒數計時器。"""
        self._next_refresh_at = time.time() + REFRESH_OPTIONS[self.refresh_index]
        self.fetch_worker()
        self._refresh_timer = self.set_interval(
            REFRESH_OPTIONS[self.refresh_index], self.fetch_worker
        )
        self.set_interval(1, self._update_countdown)

    @work(thread=True, exclusive=True)
    def fetch_worker(self) -> None:
        """背景線程抓取 Yahoo Finance 市場資料。"""
        data = fetch_stock_data()
        if data is not None:
            # 為每支股票計算標準化價格
            for stock in data:
                stock["normalized_price"] = calculate_normalized_price(
                    stock.get("marketCap")
                )
            self._last_data = data
            self.call_from_thread(self._render_table, data)
        else:
            # 抓取失敗：顯示錯誤訊息，保留上次成功資料
            self.call_from_thread(self._show_fetch_error)
        self._next_refresh_at = time.time() + REFRESH_OPTIONS[self.refresh_index]

    def _show_fetch_error(self) -> None:
        """顯示抓取失敗的錯誤訊息，保留上次成功資料。"""
        if self._last_data is not None:
            # 保留現有表格，重新渲染上次成功資料並顯示錯誤提示
            self._render_table(self._last_data, error_msg="⚠ 抓取失敗，稍後重試")
        else:
            self.query_one("#content", Static).update(
                "[red]⚠ 抓取失敗，稍後重試[/red]"
            )

    def _update_countdown(self) -> None:
        """每秒更新倒數顯示。"""
        remain = max(0, int(self._next_refresh_at - time.time()))
        self.query_one("#countdown", Static).update(f"下次更新: {remain}s")

    def _reset_refresh_timer(self) -> None:
        """停止舊計時器、建立新計時器、重設倒數。"""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
        interval = REFRESH_OPTIONS[self.refresh_index]
        self._refresh_timer = self.set_interval(interval, self.fetch_worker)
        self._next_refresh_at = time.time() + interval

    def action_speed_up(self) -> None:
        """加快刷新間隔。"""
        if self.refresh_index > 0:
            self.refresh_index -= 1
            self._reset_refresh_timer()

    def action_slow_down(self) -> None:
        """減慢刷新間隔。"""
        if self.refresh_index < len(REFRESH_OPTIONS) - 1:
            self.refresh_index += 1
            self._reset_refresh_timer()

    def action_toggle_sort(self) -> None:
        """切換排序模式。"""
        self.sort_mode_index = (self.sort_mode_index + 1) % len(SORT_MODES)
        if self._last_data is not None:
            self._render_table(self._last_data)

    def _render_table(self, data: list[dict], error_msg: str | None = None) -> None:
        """格式化並渲染供應量與標準化價格表格。"""
        # 計算每支股票的 normalized_price 並橋接 market_cap 鍵供 sort_stocks 使用
        for stock in data:
            stock["normalized_price"] = calculate_normalized_price(stock.get("marketCap"))
            stock["market_cap"] = stock.get("marketCap")

        # 依目前排序模式排序
        current_mode = SORT_MODES[self.sort_mode_index]
        sorted_data = sort_stocks(data, current_mode)

        # 排序標籤
        sort_label = SORT_LABELS[current_mode]

        # 資訊列
        lines = []
        if error_msg:
            lines.append(f"[red]{error_msg}[/red]")
            lines.append("")
        lines.extend([
            f"[bold white]更新: {time.strftime('%H:%M:%S')} | "
            f"每 {REFRESH_OPTIONS[self.refresh_index]} 秒刷新 | "
            f"{sort_label} | "
            f"共 {len(sorted_data)} 股[/bold white]",
            "[dim]" + "─" * 100 + "[/dim]",
            f"[dim] {'排名':<4}  {'代碼':<6}  {'名稱':<24}  {'當前價格':>10}  {'流通股數':>8}  {'市值':>10}  {'標準化價格 (1.0B)':>18}[/dim]",
            "[dim]" + "─" * 100 + "[/dim]",
        ])

        for i, stock in enumerate(sorted_data):
            row_color = COLORS[i % len(COLORS)]

            # 排名
            if current_mode == "market_cap_rank":
                rank_str = str(i + 1)
            else:
                rank_str = "-"

            # 代碼
            symbol = stock.get("symbol", "N/A")

            # 名稱（截斷過長名稱）
            name = stock.get("name") or "N/A"
            if len(name) > 22:
                name = name[:20] + ".."

            # 當前價格
            price_str = format_price(stock.get("regularMarketPrice"))

            # 流通股數
            supply_str = format_supply(stock.get("sharesOutstanding"))

            # 市值（用縮寫格式）
            market_cap_str = format_market_cap(stock.get("marketCap"))

            # 標準化價格（括號顯示標準化供應量）
            normalized_str = format_price(stock.get("normalized_price"))
            if normalized_str != "N/A":
                normalized_display = f"{normalized_str} (1.0B)"
            else:
                normalized_display = "N/A"

            lines.append(
                f"[{row_color}] {rank_str:<4}  {symbol:<6}  {name:<24}  "
                f"{price_str:>10}  {supply_str:>8}  {market_cap_str:>10}  "
                f"{normalized_display:>18}[/{row_color}]"
            )

        content = "\n".join(lines)
        self.query_one("#content", Static).update(content)


if __name__ == "__main__":
    StockSupplyApp().run()
