"""加密貨幣供應量與標準化價格監控器 — Textual TUI 版本。

使用 CoinGecko 公開 API（免費、無需 API Key）顯示主流加密貨幣的供應量、
當前價格，並計算假設統一 100,000,000 顆供應量下的「標準化價格」。

執行方式：
    python crypto_supply.py
"""

from __future__ import annotations

import time

import requests
from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Static

# ===== 常數 =====

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"
REFRESH_OPTIONS = [15, 30, 60, 120, 300]  # 秒
DEFAULT_REFRESH_INDEX = 1  # 預設 30 秒
NORMALIZED_SUPPLY = 100_000_000  # 1 億
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
    "#66cdaa", "#ffa07a", "#8fbc8f", "#b0c4de", "#ffb6c1",
    "#deb887", "#5f9ea0", "#9acd32", "#ba55d3", "#f08080",
]


# ===== 工具函式 =====


def calculate_normalized_price(market_cap: float | None) -> float | None:
    """計算標準化價格：market_cap / 100,000,000。

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


def sort_coins(coins: list[dict], mode: str) -> list[dict]:
    """依指定模式排序幣種清單。

    - "market_cap_rank": 按 market_cap_rank 升序，None 排最後
    - "normalized_desc": 按 normalized_price 降序，N/A 排最後
    - "normalized_asc":  按 normalized_price 升序，N/A 排最後

    回傳排序後的新清單（不修改原始輸入）。
    """
    if mode == "market_cap_rank":
        return sorted(
            coins,
            key=lambda c: (
                c.get("market_cap_rank") is None,
                c.get("market_cap_rank") if c.get("market_cap_rank") is not None else 0,
            ),
        )
    elif mode == "normalized_desc":
        return sorted(
            coins,
            key=lambda c: (
                c.get("normalized_price") is None,
                -(c.get("normalized_price") if c.get("normalized_price") is not None else 0),
            ),
        )
    elif mode == "normalized_asc":
        return sorted(
            coins,
            key=lambda c: (
                c.get("normalized_price") is None,
                c.get("normalized_price") if c.get("normalized_price") is not None else 0,
            ),
        )
    # 未知模式，回傳原順序
    return list(coins)


# ===== 資料抓取 =====


def fetch_market_data() -> list[dict] | None:
    """從 CoinGecko 獲取前 50 名加密貨幣的市場資料。

    向 /coins/markets 端點發送 GET 請求，參數：
    vs_currency=usd, order=market_cap_desc, per_page=50, page=1。
    逾時設為 10 秒。失敗時等待 3 秒重試一次，兩次失敗回傳 None。
    """
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 50,
        "page": 1,
    }
    for attempt in range(2):
        try:
            r = requests.get(COINGECKO_URL, params=params, timeout=10)
            if r.status_code != 200:
                if attempt == 0:
                    time.sleep(3)
                    continue
                return None
            return r.json()
        except Exception:
            if attempt == 0:
                time.sleep(3)
                continue
            return None
    return None

# ===== Textual TUI 應用程式 =====


class CryptoSupplyApp(App):
    """加密貨幣供應量與標準化價格監控 TUI。"""

    TITLE = "加密貨幣供應量與標準化價格"

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
        """背景線程抓取 CoinGecko 市場資料。"""
        data = fetch_market_data()
        if data is not None:
            # 為每個幣種計算標準化價格
            for coin in data:
                coin["normalized_price"] = calculate_normalized_price(
                    coin.get("market_cap")
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
        # 計算每個幣種的 normalized_price
        for coin in data:
            coin["normalized_price"] = calculate_normalized_price(coin.get("market_cap"))

        # 依目前排序模式排序
        current_mode = SORT_MODES[self.sort_mode_index]
        sorted_data = sort_coins(data, current_mode)

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
            f"共 {len(sorted_data)} 幣[/bold white]",
            "[dim]" + "─" * 100 + "[/dim]",
            "[dim] 排名   代碼       名稱             當前價格         供應量        市值            標準化價格 (供應量)  倍率[/dim]",
            "[dim]" + "─" * 100 + "[/dim]",
        ])

        for i, coin in enumerate(sorted_data):
            row_color = COLORS[i % len(COLORS)]

            # 排名
            rank = coin.get("market_cap_rank")
            rank_str = str(rank) if rank is not None else "-"

            # 代碼（大寫）
            symbol = coin.get("symbol", "")
            symbol_str = symbol.upper() if symbol else "N/A"

            # 名稱
            name = coin.get("name", "N/A") or "N/A"

            # 當前價格
            price_str = format_price(coin.get("current_price"))

            # 供應量
            supply_str = format_supply(coin.get("circulating_supply"))

            # 市值
            market_cap_str = format_price(coin.get("market_cap"))

            # 標準化價格（括號顯示標準化供應量）
            normalized_str = format_price(coin.get("normalized_price"))
            normalized_supply_str = format_supply(NORMALIZED_SUPPLY)
            if normalized_str != "N/A":
                normalized_display = f"{normalized_str} ({normalized_supply_str})"
            else:
                normalized_display = "N/A"

            # 倍率：真實價格 / 標準化價格 = 流通供應量 / 1億
            price_val = coin.get("current_price")
            norm_val = coin.get("normalized_price")
            if price_val and norm_val and norm_val > 0:
                ratio = price_val / norm_val
                ratio_str = f"{ratio:.2f}x"
            else:
                ratio_str = "N/A"

            lines.append(
                f"[{row_color}] {rank_str:<5} {symbol_str:<9} {name:<16} "
                f"{price_str:>14}  {supply_str:>10}  {market_cap_str:>14}  "
                f"{normalized_display:>22}  {ratio_str:>7}[/{row_color}]"
            )

        content = "\n".join(lines)
        self.query_one("#content", Static).update(content)


if __name__ == "__main__":
    CryptoSupplyApp().run()
