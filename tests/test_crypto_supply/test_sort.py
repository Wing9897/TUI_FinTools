# Feature: crypto-supply-price, Property 4: Sort correctness with N/A handling
"""Property-based tests for sort_coins() function.

Validates: Requirements 7.2, 7.4
"""

from hypothesis import given, settings
from hypothesis import strategies as st

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crypto_supply import sort_coins

# --- Strategies ---

coin_data_strategy = st.fixed_dictionaries(
    {
        "market_cap_rank": st.one_of(st.none(), st.integers(min_value=1, max_value=1000)),
        "normalized_price": st.one_of(st.none(), st.floats(min_value=0.0, max_value=1e12, allow_nan=False, allow_infinity=False)),
        "symbol": st.text(min_size=2, max_size=5, alphabet="abcdefghijklmnopqrstuvwxyz"),
        "name": st.text(min_size=1, max_size=20),
    }
)

SORT_MODES_FOR_TEST = ["normalized_desc", "normalized_asc"]


# --- Property Tests ---


@settings(max_examples=100)
@given(
    coins=st.lists(coin_data_strategy, min_size=0, max_size=50),
    mode=st.sampled_from(SORT_MODES_FOR_TEST),
)
def test_sort_na_coins_at_end(coins, mode):
    """All coins with non-null normalized_price appear before coins with null normalized_price.

    **Validates: Requirements 7.2, 7.4**
    """
    result = sort_coins(coins, mode)

    # Find the index where None values start
    found_none = False
    for coin in result:
        if coin.get("normalized_price") is None:
            found_none = True
        else:
            # If we already saw a None, no non-None should come after
            assert not found_none, (
                f"Non-null normalized_price found after a null one in mode={mode}"
            )


@settings(max_examples=100)
@given(
    coins=st.lists(coin_data_strategy, min_size=0, max_size=50),
    mode=st.sampled_from(SORT_MODES_FOR_TEST),
)
def test_sort_order_correctness(coins, mode):
    """Among coins with non-null normalized_price, consecutive pairs satisfy the comparator.

    Descending: a >= b; Ascending: a <= b.

    **Validates: Requirements 7.2, 7.4**
    """
    result = sort_coins(coins, mode)

    # Filter to only non-None normalized_price coins
    non_null = [c for c in result if c.get("normalized_price") is not None]

    for i in range(len(non_null) - 1):
        a = non_null[i]["normalized_price"]
        b = non_null[i + 1]["normalized_price"]
        if mode == "normalized_desc":
            assert a >= b, (
                f"Descending order violated: {a} < {b}"
            )
        elif mode == "normalized_asc":
            assert a <= b, (
                f"Ascending order violated: {a} > {b}"
            )


@settings(max_examples=100)
@given(
    coins=st.lists(coin_data_strategy, min_size=0, max_size=50),
    mode=st.sampled_from(SORT_MODES_FOR_TEST),
)
def test_sort_is_permutation(coins, mode):
    """The output list has the same length and elements as the input (permutation invariant).

    **Validates: Requirements 7.2, 7.4**
    """
    result = sort_coins(coins, mode)

    # Same length
    assert len(result) == len(coins), (
        f"Output length {len(result)} != input length {len(coins)}"
    )

    # Same elements (by identity — each input element appears exactly once in output)
    input_ids = sorted(id(c) for c in coins)
    output_ids = sorted(id(c) for c in result)
    assert input_ids == output_ids, (
        "Output is not a permutation of input (element identity mismatch)"
    )
