# Feature: crypto-supply-price, Property 2: Price formatting precision rules
"""Property-based test for format_price() precision rules.

**Validates: Requirements 2.2, 3.4**

For any non-null price value, format_price(price) SHALL:
- produce a string with exactly 2 decimal places and comma thousands separators when price >= 100
- produce a string with exactly 4 decimal places when 1 <= price < 100
- produce a string with exactly 6 decimal places when price < 1

And for any None input, the function SHALL return "N/A".
"""

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from crypto_supply import format_price


@given(price=st.floats(min_value=0.000001, max_value=1e10))
@settings(max_examples=100)
def test_price_formatting_precision(price: float) -> None:
    """For any non-null price, format_price produces correct decimal places."""
    result = format_price(price)

    # Extract the decimal portion
    assert "." in result, f"Expected decimal point in result: {result}"
    decimal_part = result.split(".")[-1]

    if price >= 100:
        # Should have exactly 2 decimal places and comma thousands separators
        assert len(decimal_part) == 2, (
            f"Expected 2 decimal places for price={price}, got '{result}'"
        )
        # Verify comma thousands separator in integer part
        integer_part = result.split(".")[0]
        # Remove commas and verify format
        assert re.match(
            r"^[\d]{1,3}(,\d{3})*$", integer_part
        ), f"Expected comma-separated thousands for price={price}, got '{result}'"
    elif price >= 1:
        # Should have exactly 4 decimal places
        assert len(decimal_part) == 4, (
            f"Expected 4 decimal places for price={price}, got '{result}'"
        )
    else:
        # price < 1: should have exactly 6 decimal places
        assert len(decimal_part) == 6, (
            f"Expected 6 decimal places for price={price}, got '{result}'"
        )


@given(price=st.none())
@settings(max_examples=100)
def test_price_formatting_none_returns_na(price: None) -> None:
    """For any None input, format_price SHALL return 'N/A'."""
    result = format_price(price)
    assert result == "N/A", f"Expected 'N/A' for None input, got '{result}'"
