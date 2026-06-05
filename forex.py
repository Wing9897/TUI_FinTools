"""法幣匯率監控器 — Textual TUI 版本。

使用 Frankfurter API（基於歐央行 ECB 資料）顯示 HKD 兌各主要法幣匯率。
免費、無需 API Key、無 rate limit。

執行方式：
    python forex_tui.py
"""

from __future__ import annotations

import time

import requests
from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Select, Static

# ===== 常數 =====

API_URL = "https://api.frankfurter.dev/v1/latest"
REFRESH_SEC = 60  # 每 60 秒更新
REFRESH_OPTIONS = [30, 60, 120, 300, 600]

# 可選基礎幣種
BASE_OPTIONS = ["HKD", "USD", "CNY", "EUR", "GBP", "JPY"]

# 顯示的目標幣種
TARGET_CURRENCIES = [
    "USD", "CNY", "JPY", "EUR", "GBP", "KRW", "TWD", "SGD",
    "AUD", "CAD", "CHF", "THB", "MYR", "PHP", "IDR", "INR",
    "NZD", "SEK", "NOK", "DKK",
]

COLORS = [
    "#00ff00", "#00ffff", "#ffff00", "#ff00ff", "#ff0000",
    "#ff8c00", "#00ff7f", "#1e90ff", "#ff69b4", "#7b68ee",
    "#ffd700", "#00ced1", "#ff6347", "#adff2f", "#da70d6",
    "#40e0d0", "#f0e68c", "#87ceeb", "#dda0dd", "#98fb98",
]


def fetch_rates(base: str) -> dict | None:
    """從 Frankfurter API 抓取匯率。斷網時重試一次。"""
    for attempt in range(2):
        try:
            r = requests.get(
                API_URL,
                params={"base": base},
                timeout=10,
            )
            if r.status_code == 200:
                return r.json()
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


class ForexApp(App):
    """法幣匯率監控 TUI。"""

    TITLE = "法幣匯率監控"

    CSS = """
    #toolbar { height: auto; padding: 0 1; }
    #base-picker { width: 15; }
    #countdown { dock: right; width: auto; padding: 0 1; }
    #main-area { height: 1fr; padding: 1; }
    """

    BINDINGS = [
        ("ctrl+up", "speed_up", "加快刷新"),
        ("ctrl+down", "slow_down", "減慢刷新"),
        ("q", "quit", "退出"),
    ]

    def __init__(self):
        super().__init__()
        self.base_currency = "HKD"
        self.refresh_index = 1  # 預設 60 秒
        self._next_refresh_at: float = 0.0
        self._refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Select.from_values(
            BASE_OPTIONS,
            value="HKD",
            allow_blank=False,
            id="base-picker",
            prompt="基礎幣",
            compact=True,
        )
        yield Static("", id="countdown")
        with VerticalScroll(id="main-area"):
            yield Static("載入中...", id="rates-content")
        yield Footer()

    def on_mount(self) -> None:
        self._next_refresh_at = time.time() + REFRESH_SEC
        self.fetch_worker()
        self._refresh_timer = self.set_interval(REFRESH_SEC, self.fetch_worker)
        self.set_interval(1, self._update_countdown)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.value == Select.NULL:
            return
        if event.select.id == "base-picker":
            self.base_currency = str(event.value)
            self.fetch_worker()

    @work(thread=True, exclusive=True)
    def fetch_worker(self) -> None:
        data = fetch_rates(self.base_currency)
        if data is not None:
            self.call_from_thread(self._render_rates, data)
        else:
            self.call_from_thread(
                self.query_one("#rates-content", Static).update,
                "[red]⚠ 抓取失敗，稍後重試[/red]",
            )
        self._next_refresh_at = time.time() + REFRESH_OPTIONS[self.refresh_index]

    def _render_rates(self, data: dict) -> None:
        rates = data.get("rates", {})
        date = data.get("date", "N/A")
        base = data.get("base", self.base_currency)

        lines = [
            f"[bold white]基礎幣: {base} | 資料日期: {date} | "
            f"更新: {time.strftime('%H:%M:%S')}[/bold white]",
            "[dim]" + "─" * 55 + "[/dim]",
            "[dim] 幣種     匯率            1單位目標幣 = ? {base}[/dim]".format(base=base),
            "[dim]" + "─" * 55 + "[/dim]",
        ]

        # 過濾出 TARGET_CURRENCIES 中有的幣種（排除自身）
        display_currencies = [c for c in TARGET_CURRENCIES if c in rates and c != base]

        for i, currency in enumerate(display_currencies):
            rate = rates[currency]
            inverse = 1.0 / rate if rate != 0 else 0
            color = COLORS[i % len(COLORS)]
            lines.append(
                f"[{color}] {currency:<5}  {rate:>12.4f}       "
                f"1 {currency} = {inverse:.4f} {base}[/{color}]"
            )

        # 如果基礎幣不在 TARGET 列表中，補充顯示
        if base not in TARGET_CURRENCIES and base in rates:
            pass  # base 不會在自己的 rates 裡

        content = "\n".join(lines)
        self.query_one("#rates-content", Static).update(content)

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
        self.query_one("#rates-content", Static).update(
            f"[bright_cyan]✓ 刷新加快 → 每 {REFRESH_SEC} 秒[/bright_cyan]"
        )

    def action_slow_down(self) -> None:
        global REFRESH_SEC
        if self.refresh_index < len(REFRESH_OPTIONS) - 1:
            self.refresh_index += 1
        REFRESH_SEC = REFRESH_OPTIONS[self.refresh_index]
        self._reset_refresh_timer()
        self.query_one("#rates-content", Static).update(
            f"[bright_cyan]✓ 刷新減慢 → 每 {REFRESH_SEC} 秒[/bright_cyan]"
        )


if __name__ == "__main__":
    ForexApp().run()
