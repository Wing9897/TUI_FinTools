# Feature: crypto-supply-price, Property 3: Supply abbreviation formatting
"""Property-based tests for format_supply() function.

Validates: Requirements 3.5

For any non-null, non-negative supply value, format_supply(supply) SHALL:
- produce a string ending in "B" when supply >= 1,000,000,000
- produce a string ending in "M" when 1,000,000 <= supply < 1,000,000,000
- produce a string ending in "K" when 1,000 <= supply < 1,000,000
- produce the numeric value as-is when supply < 1,000
And the numeric prefix (before the suffix) SHALL equal the supply divided by
the corresponding magnitude (rounded to 1 decimal place).
For any None input, the function SHALL return "N/A".
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from crypto_supply import format_supply


@given(supply=st.floats(min_value=1e9, max_value=1e15, allow_nan=False, allow_infinity=False))
@settings(max_examples=100)
def test_supply_billion_suffix(supply: float):
    """Supply >= 1B should produce a string ending in 'B'."""
    # **Validates: Requirements 3.5**
    result = format_supply(supply)
    assert result.endswith("B"), f"Expected suffix 'B' for supply={supply}, got '{result}'"
    # Verify the numeric prefix matches supply / 1B rounded to 1 decimal
    prefix = result[:-1]
    expected_value = round(supply / 1_000_000_000, 1)
    assert float(prefix) == expected_value, (
        f"Expected prefix {expected_value} for supply={supply}, got {prefix}"
    )


@given(supply=st.floats(min_value=1e6, max_value=1e9, allow_nan=False, allow_infinity=False, exclude_max=True))
@settings(max_examples=100)
def test_supply_million_suffix(supply: float):
    """Supply in [1M, 1B) should produce a string ending in 'M'."""
    # **Validates: Requirements 3.5**
    assume(supply < 1_000_000_000)
    result = format_supply(supply)
    assert result.endswith("M"), f"Expected suffix 'M' for supply={supply}, got '{result}'"
    # Verify the numeric prefix matches supply / 1M rounded to 1 decimal
    prefix = result[:-1]
    expected_value = round(supply / 1_000_000, 1)
    assert float(prefix) == expected_value, (
        f"Expected prefix {expected_value} for supply={supply}, got {prefix}"
    )


@given(supply=st.floats(min_value=1e3, max_value=1e6, allow_nan=False, allow_infinity=False, exclude_max=True))
@settings(max_examples=100)
def test_supply_thousand_suffix(supply: float):
    """Supply in [1K, 1M) should produce a string ending in 'K'."""
    # **Validates: Requirements 3.5**
    assume(supply < 1_000_000)
    result = format_supply(supply)
    assert result.endswith("K"), f"Expected suffix 'K' for supply={supply}, got '{result}'"
    # Verify the numeric prefix matches supply / 1K rounded to 1 decimal
    prefix = result[:-1]
    expected_value = round(supply / 1_000, 1)
    assert float(prefix) == expected_value, (
        f"Expected prefix {expected_value} for supply={supply}, got {prefix}"
    )


@given(supply=st.floats(min_value=0, max_value=1e3, allow_nan=False, allow_infinity=False, exclude_max=True))
@settings(max_examples=100)
def test_supply_below_thousand_no_suffix(supply: float):
    """Supply < 1K should produce the numeric value without B/M/K suffix."""
    # **Validates: Requirements 3.5**
    assume(supply < 1_000)
    result = format_supply(supply)
    assert not result.endswith(("B", "M", "K")), (
        f"Expected no suffix for supply={supply}, got '{result}'"
    )
    # The value should be formatted as integer (0 decimal places)
    expected = f"{supply:.0f}"
    assert result == expected, f"Expected '{expected}' for supply={supply}, got '{result}'"


def test_supply_none_returns_na():
    """None input should return 'N/A'."""
    # **Validates: Requirements 3.5**
    assert format_supply(None) == "N/A"
