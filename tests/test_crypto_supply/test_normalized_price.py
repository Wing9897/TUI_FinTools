# Feature: crypto-supply-price, Property 1: Normalized price calculation
"""Property-based test for calculate_normalized_price.

**Validates: Requirements 2.1, 2.3, 2.4**

Property 1: For any non-null, positive market_cap value,
calculate_normalized_price(market_cap) SHALL return exactly
market_cap / 100_000_000, and for any market_cap that is None,
zero, or negative, the function SHALL return None.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from crypto_supply import calculate_normalized_price, NORMALIZED_SUPPLY


# Strategy: floats in range [-1e15, 1e15] combined with None
market_cap_strategy = st.one_of(
    st.floats(min_value=-1e15, max_value=1e15, allow_nan=False, allow_infinity=False),
    st.none(),
)


@given(market_cap=market_cap_strategy)
@settings(max_examples=200)
def test_normalized_price_calculation(market_cap):
    """Property 1: Normalized price calculation.

    - Positive market_cap → market_cap / 100_000_000
    - None, zero, or negative → None

    **Validates: Requirements 2.1, 2.3, 2.4**
    """
    result = calculate_normalized_price(market_cap)

    if market_cap is None or market_cap <= 0:
        assert result is None, (
            f"Expected None for market_cap={market_cap}, got {result}"
        )
    else:
        expected = market_cap / NORMALIZED_SUPPLY
        assert result == expected, (
            f"Expected {expected} for market_cap={market_cap}, got {result}"
        )


@given(market_cap=st.floats(min_value=1e-10, max_value=1e15, allow_nan=False, allow_infinity=False))
@settings(max_examples=100)
def test_normalized_price_positive_always_returns_float(market_cap):
    """For any positive market_cap, the result is always a float (not None).

    **Validates: Requirements 2.1**
    """
    assume(market_cap > 0)
    result = calculate_normalized_price(market_cap)
    assert result is not None
    assert isinstance(result, float)


@given(market_cap=st.floats(min_value=-1e15, max_value=0, allow_nan=False, allow_infinity=False))
@settings(max_examples=100)
def test_normalized_price_non_positive_returns_none(market_cap):
    """For any zero or negative market_cap, the result is always None.

    **Validates: Requirements 2.3, 2.4**
    """
    result = calculate_normalized_price(market_cap)
    assert result is None


def test_normalized_price_none_input():
    """None input returns None.

    **Validates: Requirements 2.3**
    """
    assert calculate_normalized_price(None) is None
