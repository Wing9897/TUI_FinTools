# Feature: crypto-supply-price, Property 5: Table render contains all required fields
"""Property-based tests for _render_table() table output completeness.

Validates: Requirements 1.2, 3.1
"""

from hypothesis import given, settings
from hypothesis import strategies as st
from unittest.mock import MagicMock, patch

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crypto_supply import (
    CryptoSupplyApp,
    calculate_normalized_price,
    format_price,
    format_supply,
)

# --- Strategies ---

valid_coin_data_strategy = st.fixed_dictionaries(
    {
        "market_cap_rank": st.integers(min_value=1, max_value=1000),
        "symbol": st.text(min_size=2, max_size=5, alphabet="abcdefghijklmnopqrstuvwxyz"),
        "name": st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz "),
        "current_price": st.floats(min_value=0.000001, max_value=1e10, allow_nan=False, allow_infinity=False),
        "market_cap": st.floats(min_value=0.01, max_value=1e15, allow_nan=False, allow_infinity=False),
        "circulating_supply": st.floats(min_value=0.01, max_value=1e15, allow_nan=False, allow_infinity=False),
    }
)


# --- Helper ---


def capture_render_output(coins: list[dict]) -> str:
    """Create a CryptoSupplyApp instance with mocked widgets and capture render output."""
    app = CryptoSupplyApp.__new__(CryptoSupplyApp)
    # Initialize required state without running the full app
    app.sort_mode_index = 0
    app.refresh_index = 1

    # Mock query_one to capture the content string passed to .update()
    mock_static = MagicMock()
    app.query_one = MagicMock(return_value=mock_static)

    # Call _render_table
    app._render_table(coins)

    # Get the string that was passed to Static.update()
    mock_static.update.assert_called_once()
    rendered_content = mock_static.update.call_args[0][0]
    return rendered_content


# --- Property Tests ---


@settings(max_examples=100)
@given(coins=st.lists(valid_coin_data_strategy, min_size=1, max_size=30))
def test_table_render_contains_all_required_fields(coins):
    """For any list of valid coin data, the rendered table output contains all required fields.

    For each coin: market_cap_rank, symbol (uppercased), name, formatted current_price,
    formatted circulating_supply, formatted market_cap, and formatted normalized_price.

    **Validates: Requirements 1.2, 3.1**
    """
    rendered = capture_render_output(coins)

    for coin in coins:
        # market_cap_rank
        rank_str = str(coin["market_cap_rank"])
        assert rank_str in rendered, (
            f"market_cap_rank '{rank_str}' not found in rendered output"
        )

        # symbol (uppercased)
        symbol_upper = coin["symbol"].upper()
        assert symbol_upper in rendered, (
            f"symbol '{symbol_upper}' not found in rendered output"
        )

        # name
        assert coin["name"] in rendered, (
            f"name '{coin['name']}' not found in rendered output"
        )

        # formatted current_price
        price_str = format_price(coin["current_price"])
        assert price_str in rendered, (
            f"formatted current_price '{price_str}' not found in rendered output"
        )

        # formatted circulating_supply
        supply_str = format_supply(coin["circulating_supply"])
        assert supply_str in rendered, (
            f"formatted circulating_supply '{supply_str}' not found in rendered output"
        )

        # formatted market_cap
        market_cap_str = format_price(coin["market_cap"])
        assert market_cap_str in rendered, (
            f"formatted market_cap '{market_cap_str}' not found in rendered output"
        )

        # formatted normalized_price
        normalized_price = calculate_normalized_price(coin["market_cap"])
        normalized_str = format_price(normalized_price)
        assert normalized_str in rendered, (
            f"formatted normalized_price '{normalized_str}' not found in rendered output"
        )
