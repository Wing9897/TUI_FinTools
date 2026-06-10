"""App 生命週期與鍵盤綁定單元測試 — CryptoSupplyApp 的動作方法。

測試排序循環、刷新間隔邊界、null 欄位顯示、失敗後保留上次成功資料。

Validates: Requirements 4.3, 4.4, 4.6, 4.7, 5.2, 5.3, 5.4, 7.2
"""

from unittest.mock import MagicMock, patch

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crypto_supply import (
    CryptoSupplyApp,
    REFRESH_OPTIONS,
    SORT_MODES,
    format_price,
    format_supply,
)


def _create_app_instance() -> CryptoSupplyApp:
    """Create a CryptoSupplyApp instance without running the full Textual event loop."""
    app = CryptoSupplyApp.__new__(CryptoSupplyApp)
    app.refresh_index = 1  # DEFAULT_REFRESH_INDEX
    app.sort_mode_index = 0
    app._last_data = None
    app._refresh_timer = None
    app._next_refresh_at = 0.0
    return app


class TestSortCycle:
    """排序循環測試：s 鍵切換 0→1→2→0。"""

    def test_sort_cycle_through_all_modes(self):
        """連續按 3 次 s 鍵，sort_mode_index 循環 0→1→2→0。

        **Validates: Requirements 7.2**
        """
        app = _create_app_instance()
        # Mock _render_table since we have no real data
        app._render_table = MagicMock()
        # No data cached, so _render_table should not be called
        app._last_data = None

        assert app.sort_mode_index == 0

        app.action_toggle_sort()
        assert app.sort_mode_index == 1

        app.action_toggle_sort()
        assert app.sort_mode_index == 2

        app.action_toggle_sort()
        assert app.sort_mode_index == 0

    def test_sort_cycle_triggers_render_when_data_exists(self):
        """有快取資料時，toggle_sort 應觸發 _render_table。

        **Validates: Requirements 7.2**
        """
        app = _create_app_instance()
        app._render_table = MagicMock()
        app._last_data = [{"id": "bitcoin", "market_cap": 1e12}]

        app.action_toggle_sort()

        app._render_table.assert_called_once_with(app._last_data)

    def test_sort_cycle_no_render_when_no_data(self):
        """無快取資料時，toggle_sort 不應呼叫 _render_table。

        **Validates: Requirements 7.2**
        """
        app = _create_app_instance()
        app._render_table = MagicMock()
        app._last_data = None

        app.action_toggle_sort()

        app._render_table.assert_not_called()


class TestSpeedUpBoundary:
    """刷新間隔加快邊界測試。"""

    def test_speed_up_at_minimum_does_nothing(self):
        """refresh_index=0 時呼叫 action_speed_up，index 不變且不重設計時器。

        **Validates: Requirements 4.6, 5.3**
        """
        app = _create_app_instance()
        app.refresh_index = 0
        app._reset_refresh_timer = MagicMock()

        app.action_speed_up()

        assert app.refresh_index == 0
        app._reset_refresh_timer.assert_not_called()

    def test_speed_up_normal_decreases_index(self):
        """refresh_index=2 時呼叫 action_speed_up，index 變為 1 並重設計時器。

        **Validates: Requirements 4.3, 5.3**
        """
        app = _create_app_instance()
        app.refresh_index = 2
        app._reset_refresh_timer = MagicMock()

        app.action_speed_up()

        assert app.refresh_index == 1
        app._reset_refresh_timer.assert_called_once()


class TestSlowDownBoundary:
    """刷新間隔減慢邊界測試。"""

    def test_slow_down_at_maximum_does_nothing(self):
        """refresh_index=4 時呼叫 action_slow_down，index 不變且不重設計時器。

        **Validates: Requirements 4.6, 5.4**
        """
        app = _create_app_instance()
        app.refresh_index = 4
        app._reset_refresh_timer = MagicMock()

        app.action_slow_down()

        assert app.refresh_index == 4
        app._reset_refresh_timer.assert_not_called()

    def test_slow_down_normal_increases_index(self):
        """refresh_index=2 時呼叫 action_slow_down，index 變為 3 並重設計時器。

        **Validates: Requirements 4.4, 5.4**
        """
        app = _create_app_instance()
        app.refresh_index = 2
        app._reset_refresh_timer = MagicMock()

        app.action_slow_down()

        assert app.refresh_index == 3
        app._reset_refresh_timer.assert_called_once()


class TestNullFieldsRenderNA:
    """null 欄位正確顯示 "N/A" 測試。"""

    def test_null_price_renders_na(self):
        """current_price 為 None 時顯示 "N/A"。

        **Validates: Requirements 4.7, 5.2**
        """
        app = _create_app_instance()
        mock_static = MagicMock()
        app.query_one = MagicMock(return_value=mock_static)

        coin_data = [
            {
                "market_cap_rank": 1,
                "symbol": "btc",
                "name": "Bitcoin",
                "current_price": None,
                "market_cap": 1_000_000_000_000,
                "circulating_supply": 19_000_000,
            }
        ]

        app._render_table(coin_data)

        rendered = mock_static.update.call_args[0][0]
        # current_price is None -> "N/A" should appear
        assert "N/A" in rendered

    def test_null_supply_renders_na(self):
        """circulating_supply 為 None 時顯示 "N/A"。

        **Validates: Requirements 4.7, 5.2**
        """
        app = _create_app_instance()
        mock_static = MagicMock()
        app.query_one = MagicMock(return_value=mock_static)

        coin_data = [
            {
                "market_cap_rank": 2,
                "symbol": "eth",
                "name": "Ethereum",
                "current_price": 3500.0,
                "market_cap": 400_000_000_000,
                "circulating_supply": None,
            }
        ]

        app._render_table(coin_data)

        rendered = mock_static.update.call_args[0][0]
        assert "N/A" in rendered

    def test_null_market_cap_renders_na(self):
        """market_cap 為 None 時，市值與標準化價格都顯示 "N/A"。

        **Validates: Requirements 4.7, 5.2**
        """
        app = _create_app_instance()
        mock_static = MagicMock()
        app.query_one = MagicMock(return_value=mock_static)

        coin_data = [
            {
                "market_cap_rank": 3,
                "symbol": "sol",
                "name": "Solana",
                "current_price": 150.0,
                "market_cap": None,
                "circulating_supply": 500_000_000,
            }
        ]

        app._render_table(coin_data)

        rendered = mock_static.update.call_args[0][0]
        # Both market_cap and normalized_price should be "N/A"
        # format_price(None) -> "N/A", and normalized_price will also be None
        assert rendered.count("N/A") >= 2


class TestFetchFailurePreservesLastData:
    """失敗後保留上次成功資料測試。"""

    def test_show_fetch_error_preserves_last_data(self):
        """抓取失敗時 _last_data 保持不變，且使用快取資料重新渲染。

        **Validates: Requirements 4.7**
        """
        app = _create_app_instance()
        mock_static = MagicMock()
        app.query_one = MagicMock(return_value=mock_static)

        # Set cached data
        cached_data = [
            {
                "market_cap_rank": 1,
                "symbol": "btc",
                "name": "Bitcoin",
                "current_price": 70000.0,
                "market_cap": 1_400_000_000_000,
                "circulating_supply": 19_700_000,
            }
        ]
        app._last_data = cached_data

        # Call _show_fetch_error
        app._show_fetch_error()

        # _last_data should be preserved
        assert app._last_data is cached_data
        # _render_table should have been called (via _show_fetch_error which renders with error_msg)
        mock_static.update.assert_called_once()
        rendered = mock_static.update.call_args[0][0]
        # The error message should appear
        assert "抓取失敗" in rendered
        # The cached coin data should still be rendered
        assert "BTC" in rendered

    def test_show_fetch_error_no_cached_data(self):
        """無快取資料時，抓取失敗顯示錯誤訊息。

        **Validates: Requirements 4.7**
        """
        app = _create_app_instance()
        mock_static = MagicMock()
        app.query_one = MagicMock(return_value=mock_static)
        app._last_data = None

        app._show_fetch_error()

        mock_static.update.assert_called_once()
        rendered = mock_static.update.call_args[0][0]
        assert "抓取失敗" in rendered
