"""
Percentile-rank normalization shared by all four pillars.

A metric's raw value (barrel%, HR/9, wind speed...) is meaningless on its
own scale when weighted against other metrics — everything gets converted
to a 0-100 percentile against a *qualified reference population* first
(e.g. all 2022 batters with >= min_pa plate appearances). The qualification
minimum controls who defines the scale, not who gets scored: every row
still gets looked up against that scale regardless of its own sample size.
Only a genuinely missing stat (no data at all) falls back to a neutral 50.
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
