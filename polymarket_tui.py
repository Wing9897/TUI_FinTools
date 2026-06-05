"""Polymarket TUI - 終端使用者介面應用程式，用於搜尋和顯示 Polymarket 預測市場資料。"""

import json
import time
from dataclasses import dataclass
from typing import Optional

import requests
from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Input, Static

COLORS = [
    "#00ff00",   # green
    "#00ffff",   # cyan
    "#ffff00",   # yellow
    "#ff00ff",   # magenta
    "#ff0000",   # red
    "#ff8c00",   # dark orange
    "#00ff7f",   # spring green
    "#1e90ff",   # dodger blue
    "#ff69b4",   # hot pink
    "#7b68ee",   # medium slate blue
    "#ffd700",   # gold
    "#00ced1",   # dark turquoise
    "#ff6347",   # tomato
    "#adff2f",   # green yellow
    "#da70d6",   # orchid
    "#40e0d0",   # turquoise
    "#f0e68c",   # khaki
    "#87ceeb",   # sky blue
    "#dda0dd",   # plum
    "#98fb98",   # pale green
]

# ========== 常數設定 ==========
REFRESH_SEC = 30          # 自動更新間隔（秒）
REFRESH_OPTIONS = [10, 15, 30, 60, 120, 300]
TIMEOUT = 10              # API 請求逾時（秒）
MAX_RETRIES_429 = 3       # HTTP 429 最大重試次數
RETRY_WAIT_SEC = 5        # HTTP 429 重試等待秒數
MAX_RESULTS = 20          # 最大顯示結果數量


# ========== 資料模型 ==========
@dataclass
class MarketRecord:
    """單筆 Polymarket 市場資料的標準化資料結構。"""

    title: str                    # 市場標題/問題
    yes_price: Optional[float]    # Yes 結果價格 (0.0 ~ 1.0)
    no_price: Optional[float]     # No 結果價格 (0.0 ~ 1.0)
    volume: Optional[float]       # 總成交量 (USD)
    end_date: Optional[str]       # 結束日期 ISO 格式字串


# ========== 資料解析模組 ==========
class DataParser:
    """將 API 原始 JSON 回應轉換為標準化的 MarketRecord 資料結構。"""

    @staticmethod
    def parse_gamma_response(json_data) -> list[MarketRecord]:
        """解析 Gamma API 回應中的 events[].markets[]。

        支援兩種格式：
        - /events 端點：直接回傳 list of event objects
        - /public-search 端點：回傳 {"events": [...]}
        """
        records: list[MarketRecord] = []

        # 支援直接的 list 格式 (/events 端點)
        if isinstance(json_data, list):
            events = json_data
        elif isinstance(json_data, dict):
            events = json_data.get("events", [])
        else:
            return records

        if not isinstance(events, list):
            return records

        for event in events:
            if not isinstance(event, dict):
                continue

            markets = event.get("markets", [])
            if not isinstance(markets, list):
                continue

            for market in markets:
                if not isinstance(market, dict):
                    continue

                # 跳過已結束/已結算的市場
                if market.get("closed", False):
                    continue

                # 標題
                title = market.get("question", "")
                if not title:
                    title = event.get("title", "N/A")

                # 解析 outcomePrices (JSON 字串)
                yes_price: Optional[float] = None
                no_price: Optional[float] = None
                outcome_prices_raw = market.get("outcomePrices", "")
                if outcome_prices_raw:
                    try:
                        prices = json.loads(outcome_prices_raw)
                        if isinstance(prices, list) and len(prices) >= 1:
                            try:
                                yes_price = float(prices[0])
                            except (ValueError, TypeError):
                                yes_price = None
                        if isinstance(prices, list) and len(prices) >= 2:
                            try:
                                no_price = float(prices[1])
                            except (ValueError, TypeError):
                                no_price = None
                    except (json.JSONDecodeError, TypeError):
                        yes_price = None
                        no_price = None

                # 成交量：優先使用 volumeNum，備援使用 volume 字串
                volume: Optional[float] = None
                volume_num = market.get("volumeNum")
                if volume_num is not None:
                    try:
                        volume = float(volume_num)
                    except (ValueError, TypeError):
                        volume = None

                if volume is None:
                    volume_str = market.get("volume")
                    if volume_str is not None:
                        try:
                            volume = float(volume_str)
                        except (ValueError, TypeError):
                            volume = None

                # 結束日期
                end_date: Optional[str] = market.get("endDate")

                records.append(MarketRecord(
                    title=title,
                    yes_price=yes_price,
                    no_price=no_price,
                    volume=volume,
                    end_date=end_date,
                ))

        return records

    @staticmethod
    def parse_clob_response(json_data: list) -> list[MarketRecord]:
        """解析 CLOB /markets 回應。

        CLOB API 回傳格式為 list of market objects:
        [
          {
            "question": "Will X happen?",
            "tokens": [
              {"outcome": "Yes", "price": 0.70},
              {"outcome": "No", "price": 0.30}
            ],
            "end_date_iso": "2025-12-31T00:00:00Z",
            ...
          }
        ]
        """
        records: list[MarketRecord] = []

        if not isinstance(json_data, list):
            return records

        for item in json_data:
            if not isinstance(item, dict):
                continue

            # 標題：使用 question 欄位
            title = item.get("question", "")
            if not title:
                title = item.get("condition_id", "N/A")

            # 價格：從 tokens 陣列解析
            yes_price: Optional[float] = None
            no_price: Optional[float] = None
            tokens = item.get("tokens", [])
            if isinstance(tokens, list):
                for token in tokens:
                    if not isinstance(token, dict):
                        continue
                    outcome = token.get("outcome", "").lower()
                    token_price = token.get("price")
                    if token_price is not None:
                        try:
                            price_val = float(token_price)
                        except (ValueError, TypeError):
                            price_val = None

                        if outcome == "yes" and price_val is not None:
                            yes_price = price_val
                        elif outcome == "no" and price_val is not None:
                            no_price = price_val

            # 若只有 yes_price，計算 no_price = 1 - yes_price
            if yes_price is not None and no_price is None:
                no_price = round(1.0 - yes_price, 4)
            elif no_price is not None and yes_price is None:
                yes_price = round(1.0 - no_price, 4)

            # 成交量
            volume: Optional[float] = None
            volume_raw = item.get("volume")
            if volume_raw is not None:
                try:
                    volume = float(volume_raw)
                except (ValueError, TypeError):
                    volume = None

            # 結束日期
            end_date: Optional[str] = item.get("end_date_iso")

            records.append(MarketRecord(
                title=title,
                yes_price=yes_price,
                no_price=no_price,
                volume=volume,
                end_date=end_date,
            ))

        return records


# ========== 資料抓取模組 ==========
class MarketFetcher:
    """負責向 Polymarket API 發送請求並回傳解析後的市場資料。"""

    GAMMA_SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
    GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
    CLOB_MARKETS_URL = "https://clob.polymarket.com/markets"
    TIMEOUT = TIMEOUT
    USER_AGENT = "PolymarketTUI/1.0"
    MAX_RETRIES_429 = MAX_RETRIES_429
    RETRY_WAIT_SEC = RETRY_WAIT_SEC

    def search(self, keyword: str, limit: int = 20) -> list[MarketRecord] | None:
        """搜尋市場，先嘗試 Gamma API，失敗時使用 CLOB API 備援。

        Args:
            keyword: 搜尋關鍵字
            limit: 最大回傳筆數，預設 20

        Returns:
            排序後的 MarketRecord 列表，或 None（關鍵字無效時）
        """
        # 拒絕純空白關鍵字
        if not keyword or not keyword.strip():
            return None

        keyword = keyword.strip()

        for attempt in range(2):
            try:
                results = self._search_gamma(keyword, limit)
                break
            except Exception:
                # Gamma API 失敗時，使用 CLOB API 備援
                try:
                    results = self._search_clob(keyword, limit)
                    break
                except Exception:
                    if attempt == 0:
                        time.sleep(3)
                        continue
                    return None

        # 依成交量由高至低排序（None 排最後）
        results.sort(key=lambda r: r.volume if r.volume is not None else -1, reverse=True)

        # 限制最大筆數
        return results[:limit]

    def _search_gamma(self, keyword: str, limit: int) -> list[MarketRecord]:
        """透過 Gamma API /public-search 搜尋，並過濾已結束的市場。

        Args:
            keyword: 搜尋關鍵字
            limit: 最大回傳筆數

        Returns:
            解析後的 MarketRecord 列表（僅活躍市場）

        Raises:
            Exception: 任何請求或解析錯誤
        """
        headers = {"User-Agent": self.USER_AGENT}
        params = {"q": keyword, "events_status": "active"}

        response = self._handle_rate_limit(
            lambda: requests.get(
                self.GAMMA_SEARCH_URL,
                params=params,
                headers=headers,
                timeout=self.TIMEOUT,
            )
        )

        response.raise_for_status()
        json_data = response.json()
        return DataParser.parse_gamma_response(json_data)

    def _search_clob(self, keyword: str, limit: int) -> list[MarketRecord]:
        """透過 CLOB API /markets 搜尋（備援）。

        Args:
            keyword: 搜尋關鍵字
            limit: 最大回傳筆數

        Returns:
            解析後的 MarketRecord 列表

        Raises:
            Exception: 任何請求或解析錯誤
        """
        headers = {"User-Agent": self.USER_AGENT}

        response = self._handle_rate_limit(
            lambda: requests.get(
                self.CLOB_MARKETS_URL,
                headers=headers,
                timeout=self.TIMEOUT,
            )
        )

        response.raise_for_status()
        json_data = response.json()
        return DataParser.parse_clob_response(json_data)

    def _handle_rate_limit(self, func, *args) -> requests.Response:
        """處理 HTTP 429，等待 5 秒後重試，最多 3 次。

        Args:
            func: 一個可呼叫物件，回傳 requests.Response
            *args: 傳遞給 func 的額外參數

        Returns:
            成功的 requests.Response

        Raises:
            requests.HTTPError: 重試 3 次後仍收到 429
            Exception: 其他請求錯誤
        """
        for attempt in range(self.MAX_RETRIES_429 + 1):
            response = func(*args)
            if response.status_code != 429:
                return response
            # HTTP 429: 等待後重試
            if attempt < self.MAX_RETRIES_429:
                time.sleep(self.RETRY_WAIT_SEC)

        # 所有重試均失敗，回傳最後的 429 回應
        response.raise_for_status()
        return response  # 不會執行到這裡，raise_for_status 會拋出例外


# ========== 顯示格式化 ==========
class MarketFormatter:
    """負責將 MarketRecord 格式化為終端顯示字串。"""

    @staticmethod
    def format_price(price: Optional[float]) -> str:
        """格式化價格為百分比（如 70.5%），None 時回傳 N/A"""
        if price is None:
            return "N/A"
        return f"{price * 100:.1f}%"

    @staticmethod
    def format_volume(volume: Optional[float]) -> str:
        """格式化成交量（K/M 格式），None 時回傳 N/A"""
        if volume is None:
            return "N/A"
        if volume >= 1_000_000:
            return f"{volume / 1_000_000:.1f}M"
        if volume >= 1_000:
            return f"{volume / 1_000:.1f}K"
        return str(volume)

    @staticmethod
    def format_end_date(date_str: Optional[str]) -> str:
        """格式化結束日期為 YYYY-MM-DD，None 時回傳 N/A"""
        if date_str is None:
            return "N/A"
        # 取 ISO 格式字串的前 10 個字元 (YYYY-MM-DD)
        return date_str[:10]

    @staticmethod
    def get_price_color(yes_price: Optional[float]) -> str:
        """根據價格回傳顏色名稱：>0.7 綠, <0.3 紅, 其他預設"""
        if yes_price is None:
            return "default"
        if yes_price > 0.7:
            return "green"
        if yes_price < 0.3:
            return "red"
        return "default"

    @staticmethod
    def format_record(index: int, record: MarketRecord) -> str:
        """格式化單筆市場記錄為顯示字串"""
        # 標題截斷至 80 字元
        title = record.title
        if len(title) > 80:
            title = title[:80] + "…"

        yes_str = MarketFormatter.format_price(record.yes_price)
        no_str = MarketFormatter.format_price(record.no_price)
        vol_str = MarketFormatter.format_volume(record.volume)
        date_str = MarketFormatter.format_end_date(record.end_date)

        return (
            f"{index}. {title}\n"
            f"   Yes: {yes_str} | No: {no_str} | "
            f"Vol: {vol_str} | End: {date_str}"
        )


# ========== 搜尋控制器 ==========
class SearchController:
    """協調搜尋邏輯、自動更新與請求管理。"""

    REFRESH_INTERVAL = 30  # 秒

    def __init__(self, fetcher: MarketFetcher):
        self.fetcher = fetcher
        self.current_keyword: str = ""
        self.previous_results: list[MarketRecord] = []
        self._cancelled: bool = False
        self._last_refresh_time: float = 0.0

    def search(self, keyword: str) -> tuple[list[MarketRecord] | None, str | None]:
        """發起新搜尋，取消前一次進行中的請求，重置自動更新計時器。

        Returns:
            Tuple of (results, error_message).
            - If keyword is invalid: (None, error_message)
            - If API succeeds: (results, None)
            - If API fails: (previous_results, error_message)
        """
        # 取消前一次進行中的請求
        self.cancel_current()

        # 驗證關鍵字：拒絕純空白
        if not keyword or not keyword.strip():
            return (None, "關鍵字不得為空")

        # 去除前後空白
        keyword = keyword.strip()

        # 更新目前關鍵字並重置自動更新計時器
        self.current_keyword = keyword
        self._last_refresh_time = time.time()

        # 重置取消旗標
        self._cancelled = False

        # 執行搜尋
        try:
            results = self.fetcher.search(keyword)
        except Exception as e:
            # API 失敗：保留上次結果並回傳錯誤訊息
            error_msg = f"搜尋失敗：{type(e).__name__}"
            return (self.previous_results, error_msg)

        # 檢查是否已被取消
        if self._cancelled:
            return (self.previous_results, None)

        # MarketFetcher.search 回傳 None 表示關鍵字無效（不應到達此處，已先驗證）
        if results is None:
            error_msg = "搜尋失敗：無法取得資料"
            return (self.previous_results, error_msg)

        # 搜尋成功：更新 previous_results
        self.previous_results = results
        return (results, None)

    def cancel_current(self) -> None:
        """取消目前進行中的請求。"""
        self._cancelled = True

    def on_refresh_tick(self) -> tuple[list[MarketRecord] | None, str | None]:
        """自動更新時觸發，重新以目前 keyword 搜尋。

        Returns:
            Tuple of (results, error_message).
            - If no keyword set: (None, None)
            - Otherwise: same as search()
        """
        if not self.current_keyword:
            return (None, None)

        # 重置取消旗標
        self._cancelled = False

        # 使用目前關鍵字重新搜尋
        try:
            results = self.fetcher.search(self.current_keyword)
        except Exception as e:
            # API 失敗：保留上次結果並回傳錯誤訊息
            error_msg = f"自動更新失敗：{type(e).__name__}"
            return (self.previous_results, error_msg)

        # 檢查是否已被取消
        if self._cancelled:
            return (self.previous_results, None)

        # MarketFetcher.search 回傳 None 表示無效
        if results is None:
            error_msg = "自動更新失敗：無法取得資料"
            return (self.previous_results, error_msg)

        # 更新 previous_results 並重置計時
        self.previous_results = results
        self._last_refresh_time = time.time()
        return (results, None)


# ========== TUI 應用程式主體 ==========
class PolymarketApp(App):
    """Polymarket 預測市場 TUI 應用程式。"""

    TITLE = "Polymarket TUI"

    CSS = """
    #main-area {
        height: 1fr;
    }
    #market-content {
        padding: 1;
    }
    """

    BINDINGS = [
        ("ctrl+up", "speed_up", "加快刷新"),
        ("ctrl+down", "slow_down", "減慢刷新"),
        ("q", "quit", "退出"),
    ]

    def __init__(self):
        super().__init__()
        self.fetcher = MarketFetcher()
        self.search_controller = SearchController(self.fetcher)
        self._refresh_timer = None
        self.refresh_index = 2  # 預設 index 2 = 30 秒

    def compose(self) -> ComposeResult:
        """組合版面配置：Header → Input → Market_Display → Footer。"""
        yield Header(show_clock=True)
        yield Input(placeholder="輸入英文關鍵字搜尋 (如 bitcoin, ethereum, trump) 按 Enter", id="search-input")
        with VerticalScroll(id="main-area"):
            yield Static("", id="market-content")
        yield Footer()

    def on_mount(self) -> None:
        """應用程式啟動時顯示歡迎訊息並啟動自動更新計時器。"""
        welcome_text = (
            "🔮 [bold]Polymarket TUI[/bold]\n"
            "══════════════════════════════════════\n"
            "\n"
            "歡迎使用 Polymarket 預測市場終端介面！\n"
            "\n"
            "📖 操作說明：\n"
            "  • 輸入英文關鍵字並按 Enter 搜尋市場\n"
            "  • Ctrl+↑ 加快刷新 | Ctrl+↓ 減慢刷新\n"
            f"  • 目前自動刷新：每 {REFRESH_SEC} 秒\n"
            "  • 按 'q' 退出應用程式\n"
            "\n"
            "🔍 搜尋範例：bitcoin, ethereum, trump, crypto\n"
            "⚠ 注意：API 只支援英文搜尋\n"
        )
        self.query_one("#market-content", Static).update(welcome_text)
        self._refresh_timer = self.set_interval(REFRESH_SEC, self._on_refresh_tick)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """處理搜尋輸入提交事件。"""
        keyword = event.value.strip()
        if not keyword:
            return
        event.input.clear()

        self._reset_refresh_timer()
        self._do_search(keyword)

    @work(thread=True, exclusive=True)
    def _do_search(self, keyword: str) -> None:
        """背景執行搜尋工作。"""
        try:
            results, error_msg = self.search_controller.search(keyword)
        except Exception as e:
            timestamp = time.strftime("%H:%M:%S")
            self.call_from_thread(
                self._show_error, f"[{timestamp}] ⚠ {type(e).__name__}"
            )
            return
        self.call_from_thread(self._handle_search_result, results, error_msg)

    def _handle_search_result(
        self, results: list[MarketRecord] | None, error_msg: str | None
    ) -> None:
        """處理搜尋結果。"""
        timestamp = time.strftime("%H:%M:%S")

        if error_msg is not None:
            if results and len(results) > 0:
                self._display_results(results, timestamp, error_msg)
            else:
                self._show_error(f"[{timestamp}] ⚠ {error_msg}")
            return

        if results is None or len(results) == 0:
            self._show_error(f"[{timestamp}] 找不到相關市場")
            return

        self._display_results(results, timestamp)

    def _display_results(
        self, results: list[MarketRecord], timestamp: str, warning: str | None = None
    ) -> None:
        """格式化並顯示搜尋結果，每條不同顏色交替。"""
        lines = []
        lines.append(f"[bold white]\\[{timestamp}] 更新完成 — 共 {len(results)} 筆結果[/bold white]")
        lines.append("[dim]" + "─" * 50 + "[/dim]")

        for i, record in enumerate(results, start=1):
            color = COLORS[i % len(COLORS)]

            title = record.title
            if len(title) > 80:
                title = title[:80] + "…"

            yes_str = MarketFormatter.format_price(record.yes_price)
            no_str = MarketFormatter.format_price(record.no_price)
            vol_str = MarketFormatter.format_volume(record.volume)
            date_str = MarketFormatter.format_end_date(record.end_date)

            # 價格顏色標記（移除，統一使用行顏色以保持一致性）
            yes_display = yes_str

            lines.append(f"[{color}]{i:>2}. {title}[/{color}]")
            lines.append(f"[{color}]    Yes: {yes_display} | No: {no_str} | Vol: {vol_str} | End: {date_str}[/{color}]")

        if warning:
            lines.append(f"\n[yellow]⚠ {warning}[/yellow]")

        content = "\n".join(lines)
        market_content = self.query_one("#market-content", Static)
        market_content.update(content)

    def _show_error(self, msg: str) -> None:
        """顯示錯誤訊息於 Market_Display。"""
        self.query_one("#market-content", Static).update(f"[red]{msg}[/red]")

    def _show_message(self, msg: str) -> None:
        """顯示一般訊息於 Market_Display。"""
        self.query_one("#market-content", Static).update(f"[bright_cyan]{msg}[/bright_cyan]")

    def _on_refresh_tick(self) -> None:
        """自動更新計時器觸發。"""
        if not self.search_controller.current_keyword:
            return
        self._do_refresh()

    @work(thread=True, exclusive=True)
    def _do_refresh(self) -> None:
        """背景執行自動更新。"""
        try:
            results, error_msg = self.search_controller.on_refresh_tick()
        except Exception as e:
            timestamp = time.strftime("%H:%M:%S")
            self.call_from_thread(
                self._show_error, f"[{timestamp}] ⚠ {type(e).__name__}"
            )
            return
        if results is not None or error_msg is not None:
            self.call_from_thread(self._handle_search_result, results, error_msg)

    def _reset_refresh_timer(self) -> None:
        """重置自動更新計時器。"""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
        self._refresh_timer = self.set_interval(REFRESH_SEC, self._on_refresh_tick)

    def action_speed_up(self) -> None:
        """Ctrl+Up: 加快刷新（減少秒數）"""
        global REFRESH_SEC
        if self.refresh_index > 0:
            self.refresh_index -= 1
        REFRESH_SEC = REFRESH_OPTIONS[self.refresh_index]
        self._reset_refresh_timer()
        self._show_message(f"✓ 刷新加快 → 每 {REFRESH_SEC} 秒")

    def action_slow_down(self) -> None:
        """Ctrl+Down: 減慢刷新（增加秒數）"""
        global REFRESH_SEC
        if self.refresh_index < len(REFRESH_OPTIONS) - 1:
            self.refresh_index += 1
        REFRESH_SEC = REFRESH_OPTIONS[self.refresh_index]
        self._reset_refresh_timer()
        self._show_message(f"✓ 刷新減慢 → 每 {REFRESH_SEC} 秒")

    def action_quit(self) -> None:
        """退出應用程式。"""
        if self._refresh_timer:
            self._refresh_timer.stop()
        self.search_controller.cancel_current()
        self.workers.cancel_all()
        self.exit()


if __name__ == "__main__":
    PolymarketApp().run()
