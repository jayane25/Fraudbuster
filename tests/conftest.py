"""Shared test setup: make src/ importable and give tests a tiny hand made transaction builder."""
import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

T0 = pd.Timestamp("2024-01-01 09:00")


@pytest.fixture
def make_df():
    """make_df([(src, dst, amount, hours_after_T0), ...]) -> transactions DataFrame like load_transactions returns."""
    def _make(rows):
        df = pd.DataFrame(
            [dict(tx_id=f"T{i:04d}", src=s, dst=d, amount=float(a), ts=T0 + pd.Timedelta(hours=h))
             for i, (s, d, a, h) in enumerate(rows)])
        return df.sort_values("ts").reset_index(drop=True)
    return _make
