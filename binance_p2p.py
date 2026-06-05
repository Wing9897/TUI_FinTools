import time

import requests
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, RichLog, Select, Static

try:
    import winsound  # Windows 內建,用嚟響鈴
except ImportError:
    winsound = None

# ========== 抓取設定 ==========
URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
REFRESH_SEC = 5  # 幾多秒刷新一次
ALARM_COOLDOWN_SEC = 60  # 響一次後,要隔幾耐先可以再響

HEADERS = {
    "Accept": "*/*",
    "Content-Type": "application/json",
    "Origin": "https://p2p.binance.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

PAYLOAD = {
    "fiat": "HKD", "page": 1, "rows": 10,
    "tradeType": "BUY",  # BUY = 用 HKD 買 USDT
    "asset": "USDT", "countries": [],
    "proMerchantAds": False, "shieldMerchantAds": False,
    "filterType": "all", "periods": [],
    "additionalKycVerifyFilter": 0, "publisherType": None,
    "payTypes": [], "classifies": ["mass", "profession", "fiat_trade"],
}

# 可切換選項
REFRESH_OPTIONS = [3, 5, 10, 15, 30, 60, 120]
FIAT_OPTIONS = ["HKD", "USD", "CNY", "EUR", "GBP", "JPY", "KRW", "TWD", "SGD", "AUD"]
ASSET_OPTIONS = ["USDT", "BTC", "ETH", "BNB", "FDUSD"]
TRADE_OPTIONS = ["BUY", "SELL"]

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


def fetch_prices():
    """回傳 [dict, ...];失敗回傳 None。斷網時自動等 3 秒重試一次。"""
    for attempt in range(2):
        try:
            r = requests.post(URL, headers=HEADERS, json=PAYLOAD, timeout=10)
            if r.status_code != 200:
                if attempt == 0:
                    time.sleep(3)
                    continue
                return None
            out = []
            for w in r.json().get("data", []):
                ad = w.get("adv", {})
                m = w.get("advertiser", {})
                out.append({
                    "nick": m.get("nickName", "N/A"),
                    "price": float(ad.get("price", 0)),
                    "available": float(ad.get("surplusAmount", 0)),
                    "min": ad.get("minSingleTransAmount", "N/A"),
                    "max": ad.get("maxSingleTransAmount", "N/A"),
                    "pay": [t.get("payType", "") for t in ad.get("tradeMethods", [])],
                })
            return out
        except Exception:
            if attempt == 0:
                time.sleep(3)
                continue
            return None
    return None


class C2CApp(App):
    TITLE = "Binance C2C P2P"
    CSS = """
    #left  { width: 1fr; border: round $accent; }
    #right { width: 1fr; }
    #prices { padding: 1; }
    #alarm_box { height: 1fr; border: round $warning; }
    Input { dock: top; }
    """

    BINDINGS = [
        ("ctrl+up", "speed_up", "加快刷新"),
        ("ctrl+down", "slow_down", "減慢刷新"),
        ("q", "quit", "離開"),
    ]

    def __init__(self):
        super().__init__()
        self.latest = []
        self.alarm_price = None
        self.last_alarm_ts = 0.0
        self.refresh_index = 1  # 預設 index 1 = 5 秒
        self._refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with VerticalScroll(id="left"):
                yield Static("載入中…", id="prices")
            with Vertical(id="right"):
                yield Select.from_values(FIAT_OPTIONS, value="HKD", allow_blank=False, id="fiat-select", prompt="法幣")
                yield Select.from_values(ASSET_OPTIONS, value="USDT", allow_blank=False, id="asset-select", prompt="幣種")
                yield Select.from_values(TRADE_OPTIONS, value="BUY", allow_blank=False, id="trade-select", prompt="方向")
                yield Input(placeholder="輸入目標價（如 7.80）按 Enter 設定警報", id="target")
                yield RichLog(id="alarm_box", highlight=False, markup=True)
        yield Footer()

    def on_mount(self):
        self.log_alarm(f"Ctrl+↑ 加快刷新 | Ctrl+↓ 減慢刷新 | 用右側選單切換幣對")
        self.log_alarm(f"目前: {PAYLOAD['fiat']} → {PAYLOAD['asset']} ({PAYLOAD['tradeType']}) 每 {REFRESH_SEC}秒")
        self.fetch_worker()
        self._refresh_timer = self.set_interval(REFRESH_SEC, self.fetch_worker)

    def on_select_changed(self, event: Select.Changed) -> None:
        """處理 Select 下拉選單變更事件。"""
        if event.value == Select.NULL:
            return
        select_id = event.select.id
        if select_id == "fiat-select":
            PAYLOAD["fiat"] = event.value
            self.alarm_price = None
            self.log_alarm(f"✓ 法幣切換為 {event.value} (目標價已重設)")
        elif select_id == "asset-select":
            PAYLOAD["asset"] = event.value
            self.alarm_price = None
            self.log_alarm(f"✓ 幣種切換為 {event.value} (目標價已重設)")
        elif select_id == "trade-select":
            PAYLOAD["tradeType"] = event.value
            direction = "買入" if event.value == "BUY" else "賣出"
            self.log_alarm(f"✓ 方向切換為 {direction}")
        self.fetch_worker()

    # ---------- 背景抓取(thread,唔會卡 UI) ----------
    @work(thread=True, exclusive=True)
    def fetch_worker(self):
        data = fetch_prices()
        if data is not None:
            self.call_from_thread(self.render_prices, data)
        else:
            self.call_from_thread(self.log_alarm, "⚠ 抓取失敗,稍後重試")

    def render_prices(self, data):
        self.latest = data
        direction = "買入" if PAYLOAD["tradeType"] == "BUY" else "賣出"
        pair_info = f"{PAYLOAD['fiat']} → {direction} {PAYLOAD['asset']}"
        lines = [f"[bold]更新: {time.strftime('%H:%M:%S')}   {pair_info}   每 {REFRESH_SEC}秒[/bold]", ""]
        for i, d in enumerate(data, 1):
            color = COLORS[i % len(COLORS)]
            pay = " | ".join(p for p in d["pay"] if p) or "N/A"
            lines.append(f"[{color}]{i:>2}. {d['price']:>8.2f} {PAYLOAD['fiat']}   {d['nick']}[/{color}]")
            lines.append(f"[{color}]    可用 {d['available']:.0f} | 限額 {d['min']}-{d['max']} | {pay}[/{color}]")
        self.query_one("#prices", Static).update("\n".join(lines))
        self.check_alarm()

    # ---------- Alarm ----------
    def on_input_submitted(self, event: Input.Submitted):
        val = event.value.strip()
        if not val:
            return
        event.input.clear()

        try:
            self.alarm_price = float(val)
            self.last_alarm_ts = 0.0
            self.log_alarm(f"已設定目標價 ≤ {self.alarm_price:.2f}")
        except ValueError:
            self.log_alarm("請輸入有效目標價（如 7.80）")

    def check_alarm(self):
        if self.alarm_price is None or not self.latest:
            return
        low = min(d["price"] for d in self.latest)
        if low <= self.alarm_price:
            now = time.time()
            if now - self.last_alarm_ts >= ALARM_COOLDOWN_SEC:  # 過咗冷卻先再響
                self.last_alarm_ts = now
                self.log_alarm(f"⚠ 觸發！最低價 {low:.2f} ≤ {self.alarm_price:.2f}")
                if winsound:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                else:
                    self.bell()

    def log_alarm(self, msg):
        self.query_one("#alarm_box", RichLog).write(f"[{time.strftime('%H:%M:%S')}] {msg}")

    # ---------- Ctrl+Key 刷新速度 ----------
    def _reset_refresh_timer(self) -> None:
        """停止舊的刷新計時器並建立新的。"""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
        self._refresh_timer = self.set_interval(REFRESH_SEC, self.fetch_worker)

    def action_speed_up(self) -> None:
        """Ctrl+Up: 加快刷新（減少秒數）"""
        global REFRESH_SEC
        if self.refresh_index > 0:
            self.refresh_index -= 1
        REFRESH_SEC = REFRESH_OPTIONS[self.refresh_index]
        self._reset_refresh_timer()
        self.log_alarm(f"✓ 刷新加快 → 每 {REFRESH_SEC} 秒")

    def action_slow_down(self) -> None:
        """Ctrl+Down: 減慢刷新（增加秒數）"""
        global REFRESH_SEC
        if self.refresh_index < len(REFRESH_OPTIONS) - 1:
            self.refresh_index += 1
        REFRESH_SEC = REFRESH_OPTIONS[self.refresh_index]
        self._reset_refresh_timer()
        self.log_alarm(f"✓ 刷新減慢 → 每 {REFRESH_SEC} 秒")


if __name__ == "__main__":
    C2CApp().run()
