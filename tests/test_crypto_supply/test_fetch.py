"""API 抓取邏輯單元測試 — fetch_market_data() 的重試與逾時處理。

Validates: Requirements 1.3, 1.4
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from crypto_supply import COINGECKO_URL, fetch_market_data

EXPECTED_PARAMS = {
    "vs_currency": "usd",
    "order": "market_cap_desc",
    "per_page": 30,
    "page": 1,
}


class TestFetchMarketDataSuccess:
    """成功回應場景。"""

    @patch("crypto_supply.time.sleep")
    @patch("crypto_supply.requests.get")
    def test_successful_response_returns_list(self, mock_get, mock_sleep):
        """第一次請求成功應回傳 JSON 資料。"""
        fake_data = [{"id": "bitcoin", "symbol": "btc", "current_price": 70000}]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = fake_data
        mock_get.return_value = mock_response

        result = fetch_market_data()

        assert result == fake_data
        mock_get.assert_called_once_with(
            COINGECKO_URL, params=EXPECTED_PARAMS, timeout=10
        )
        mock_sleep.assert_not_called()


class TestFetchMarketDataRetry:
    """重試邏輯場景。"""

    @patch("crypto_supply.time.sleep")
    @patch("crypto_supply.requests.get")
    def test_first_fail_retries_and_succeeds(self, mock_get, mock_sleep):
        """第一次 HTTP 非 200，重試第二次成功。"""
        fail_response = MagicMock()
        fail_response.status_code = 500

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = [{"id": "ethereum"}]

        mock_get.side_effect = [fail_response, success_response]

        result = fetch_market_data()

        assert result == [{"id": "ethereum"}]
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(3)

    @patch("crypto_supply.time.sleep")
    @patch("crypto_supply.requests.get")
    def test_both_attempts_fail_returns_none(self, mock_get, mock_sleep):
        """兩次 HTTP 非 200 回傳 None。"""
        fail_response = MagicMock()
        fail_response.status_code = 503

        mock_get.return_value = fail_response

        result = fetch_market_data()

        assert result is None
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(3)


class TestFetchMarketDataTimeout:
    """逾時（例外）處理場景。"""

    @patch("crypto_supply.time.sleep")
    @patch("crypto_supply.requests.get")
    def test_timeout_triggers_retry(self, mock_get, mock_sleep):
        """第一次逾時觸發重試，第二次成功。"""
        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = [{"id": "solana"}]

        mock_get.side_effect = [requests.Timeout("timeout"), success_response]

        result = fetch_market_data()

        assert result == [{"id": "solana"}]
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(3)

    @patch("crypto_supply.time.sleep")
    @patch("crypto_supply.requests.get")
    def test_double_timeout_returns_none(self, mock_get, mock_sleep):
        """兩次逾時回傳 None。"""
        mock_get.side_effect = [
            requests.Timeout("timeout"),
            requests.Timeout("timeout"),
        ]

        result = fetch_market_data()

        assert result is None
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(3)

    @patch("crypto_supply.time.sleep")
    @patch("crypto_supply.requests.get")
    def test_first_timeout_second_succeeds(self, mock_get, mock_sleep):
        """第一次逾時，第二次成功回傳資料。"""
        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = [{"id": "cardano", "symbol": "ada"}]

        mock_get.side_effect = [
            requests.ConnectionError("connection failed"),
            success_response,
        ]

        result = fetch_market_data()

        assert result == [{"id": "cardano", "symbol": "ada"}]
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(3)

    @patch("crypto_supply.time.sleep")
    @patch("crypto_supply.requests.get")
    def test_correct_params_passed_to_requests(self, mock_get, mock_sleep):
        """驗證傳遞正確的參數給 requests.get。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        fetch_market_data()

        mock_get.assert_called_with(
            COINGECKO_URL, params=EXPECTED_PARAMS, timeout=10
        )
