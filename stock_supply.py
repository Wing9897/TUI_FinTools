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

# 監控的股票/指數（150 支熱門美股）
SYMBOLS = [
    # ===== 指數 ETF =====
    ("SPY", "標普500 ETF"),
    ("QQQ", "納斯達克100"),
    ("DIA", "道瓊 ETF"),
    ("IWM", "羅素2000"),
    ("VIX", "恐慌指數"),
    # ===== 科技 =====
    ("AAPL", "蘋果"),
    ("MSFT", "微軟"),
    ("GOOGL", "谷歌"),
    ("AMZN", "亞馬遜"),
    ("NVDA", "輝達"),
    ("META", "Meta"),
    ("TSLA", "特斯拉"),
    ("AVGO", "博通"),
    ("ORCL", "甲骨文"),
    ("CRM", "Salesforce"),
    ("AMD", "超微半導體"),
    ("NFLX", "網飛"),
    ("ADBE", "Adobe"),
    ("INTC", "英特爾"),
    ("CSCO", "思科"),
    ("QCOM", "高通"),
    ("AMAT", "應材"),
    ("MU", "美光"),
    ("NOW", "ServiceNow"),
    ("UBER", "優步"),
    ("SHOP", "Shopify"),
    ("PLTR", "Palantir"),
    ("PANW", "Palo Alto"),
    ("CRWD", "CrowdStrike"),
    ("SNOW", "Snowflake"),
    ("ABNB", "Airbnb"),
    ("SQ", "Block"),
    ("DDOG", "Datadog"),
    ("ZS", "Zscaler"),
    ("NET", "Cloudflare"),
    ("TEAM", "Atlassian"),
    ("WDAY", "Workday"),
    ("INTU", "Intuit"),
    ("SNPS", "新思科技"),
    ("CDNS", "益華電腦"),
    ("KLAC", "科磊"),
    ("LRCX", "科林研發"),
    ("MRVL", "Marvell"),
    ("ON", "安森美"),
    ("ARM", "ARM"),
    ("SMCI", "超微電腦"),
    ("DELL", "戴爾"),
    ("HPE", "慧與科技"),
    ("IBM", "IBM"),
    ("TXN", "德州儀器"),
    # ===== 金融 =====
    ("JPM", "摩根大通"),
    ("V", "Visa"),
    ("MA", "萬事達卡"),
    ("BAC", "美國銀行"),
    ("GS", "高盛"),
    ("MS", "摩根士丹利"),
    ("WFC", "富國銀行"),
    ("C", "花旗"),
    ("AXP", "美國運通"),
    ("BLK", "貝萊德"),
    ("SCHW", "嘉信理財"),
    ("BX", "黑石"),
    ("KKR", "KKR"),
    ("COF", "第一資本"),
    ("CME", "芝商所"),
    ("ICE", "洲際交易所"),
    # ===== 醫療/生技 =====
    ("LLY", "禮來"),
    ("UNH", "聯合健康"),
    ("JNJ", "嬌生"),
    ("ABBV", "艾伯維"),
    ("MRK", "默沙東"),
    ("PFE", "輝瑞"),
    ("TMO", "賽默飛"),
    ("ABT", "亞培"),
    ("AMGN", "安進"),
    ("GILD", "吉利德"),
    ("VRTX", "福泰製藥"),
    ("REGN", "再生元"),
    ("ISRG", "直覺手術"),
    ("MDT", "美敦力"),
    ("BMY", "必治妥"),
    ("MRNA", "莫德納"),
    # ===== 消費 =====
    ("WMT", "沃爾瑪"),
    ("COST", "好市多"),
    ("HD", "家得寶"),
    ("PG", "寶潔"),
    ("KO", "可口可樂"),
    ("PEP", "百事"),
    ("MCD", "麥當勞"),
    ("NKE", "耐吉"),
    ("SBUX", "星巴克"),
    ("TGT", "塔吉特"),
    ("LOW", "勞氏"),
    ("EL", "雅詩蘭黛"),
    ("CL", "高露潔"),
    ("DIS", "迪士尼"),
    ("CMCSA", "康卡斯特"),
    ("T", "AT&T"),
    ("VZ", "威訊"),
    ("TMUS", "T-Mobile"),
    # ===== 工業/能源/材料 =====
    ("XOM", "埃克森美孚"),
    ("CVX", "雪佛龍"),
    ("COP", "康菲石油"),
    ("SLB", "斯倫貝謝"),
    ("LIN", "林德"),
    ("CAT", "卡特彼勒"),
    ("DE", "迪爾"),
    ("HON", "霍尼韋爾"),
    ("UNP", "聯合太平洋"),
    ("RTX", "雷神"),
    ("LMT", "洛馬"),
    ("BA", "波音"),
    ("GE", "奇異"),
    ("MMM", "3M"),
    ("FDX", "聯邦快遞"),
    ("UPS", "優比速"),
    # ===== 半導體/電動車 =====
    ("TSM", "台積電"),
    ("ASML", "阿斯麥"),
    ("RIVN", "Rivian"),
    ("LCID", "Lucid"),
    ("F", "福特"),
    ("GM", "通用汽車"),
    # ===== 加密相關 =====
    ("COIN", "Coinbase"),
    ("MSTR", "Strategy"),
    ("MARA", "Marathon"),
    ("RIOT", "Riot"),
    ("CLSK", "CleanSpark"),
    ("HUT", "Hut 8"),
    # ===== 中概 ADR =====
    ("BABA", "阿里巴巴"),
    ("PDD", "拼多多"),
    ("JD", "京東"),
    ("BIDU", "百度"),
    ("NIO", "蔚來"),
    ("XPEV", "小鵬汽車"),
    ("LI", "理想汽車"),
    ("BILI", "嗶哩嗶哩"),
    ("TME", "騰訊音樂"),
    # ===== 其他熱門 =====
    ("BRK-B", "波克夏B"),
    ("PYPL", "PayPal"),
    ("ROKU", "Roku"),
    ("SPOT", "Spotify"),
    ("ZM", "Zoom"),
    ("SNAP", "Snap"),
    ("PINS", "Pinterest"),
    ("RBLX", "Roblox"),
    ("U", "Unity"),
    ("PATH", "UiPath"),
    ("SOFI", "SoFi"),
    ("HOOD", "Robinhood"),
    ("DKNG", "DraftKings"),
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
    "#66cdaa", "#ffa07a", "#8fbc8f", "#b0c4de", "#ffb6c1",
    "#deb887", "#5f9ea0", "#9acd32", "#ba55d3", "#f08080",
]


# ===== 工具函式 =====


def _display_width(s: str) -> int:
    """計算字串在終端的顯示寬度（中文字佔 2 格）。"""
    width = 0
    for ch in s:
        if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or '\uff00' <= ch <= '\uffef':
            width += 2
        else:
            width += 1
    return width


def _pad_right(s: str, width: int) -> str:
    """右補空格使字串顯示寬度達到 width。"""
    pad = width - _display_width(s)
    return s + " " * max(0, pad)


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
            "[dim]" + "─" * 105 + "[/dim]",
            "[dim]  #    Code    Name        Price    Shares     MarketCap    Normalized (1.0B)  Ratio[/dim]",
            "[dim]" + "─" * 105 + "[/dim]",
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

            # 名稱（截斷過長名稱，考慮中文寬度）
            name = stock.get("name") or "N/A"
            if _display_width(name) > 10:
                # 截斷到顯示寬度 8 + ".."
                truncated = ""
                w = 0
                for ch in name:
                    cw = 2 if ('\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or '\uff00' <= ch <= '\uffef') else 1
                    if w + cw > 8:
                        break
                    truncated += ch
                    w += cw
                name = truncated + ".."
            name_padded = _pad_right(name, 10)

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

            # 倍率：真實價格 / 標準化價格 = 流通股數 / 1B
            price_val = stock.get("regularMarketPrice")
            norm_val = stock.get("normalized_price")
            if price_val and norm_val and norm_val > 0:
                ratio = price_val / norm_val
                ratio_str = f"{ratio:.2f}x"
            else:
                ratio_str = "N/A"

            lines.append(
                f"[{row_color}]{rank_str:>3}  {symbol:<6}  {name_padded}  "
                f"{price_str:>10}  {supply_str:>8}  {market_cap_str:>12}  "
                f"{normalized_display:>20}  {ratio_str:>7}[/{row_color}]"
            )

        content = "\n".join(lines)
        self.query_one("#content", Static).update(content)


if __name__ == "__main__":
    StockSupplyApp().run()
