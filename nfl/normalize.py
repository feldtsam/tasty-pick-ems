"""
Percentile-rank normalization, shared by every scoring component in
nfl/scoring.py. Duplicated from backtest/scoring/normalize.py rather than
imported across domains — each top-level domain (backtest/, pipeline/,
nfl/) is self-contained by design (see pipeline/api/live_scoring/
score_candidate.py's docstring for the same call).

A metric's raw value is meaningless on its own scale when weighted against
other metrics — everything gets converted to a 0-100 percentile against a
qualified reference population first. Only a genuinely missing stat (no
data at all) falls back to a neutral 50; a real, recorded zero is not
"missing" and should percentile-rank normally (typically landing low).
"""
import numpy as np
import pandas as pd


def build_reference_scale(values: pd.Series, qualified_mask: pd.Series = None) -> np.ndarray:
    ref = values[qualified_mask] if qualified_mask is not None else values
    return np.sort(ref.dropna().to_numpy(dtype=float))


def percentile_lookup(raw_values: pd.Series, reference_scale: np.ndarray) -> pd.Series:
    """% of the reference population at or below each raw value, 0-100. NaN stays NaN."""
    n = len(reference_scale)
    if n == 0:
        return pd.Series(np.nan, index=raw_values.index)
    numeric = pd.to_numeric(raw_values, errors="coerce")
    idx = np.searchsorted(reference_scale, numeric.to_numpy(dtype=float), side="left")
    pct = idx / n * 100
    result = pd.Series(pct, index=raw_values.index)
    result[numeric.isna()] = np.nan
    return result


def fill_neutral(percentiles: pd.Series, neutral: float = 50.0) -> pd.Series:
    return percentiles.fillna(neutral)
