import numpy as np
import pandas as pd

from mdt import data as D


def test_clean_drops_stale_and_weekend_rows_but_keeps_extreme_moves():
    idx = pd.to_datetime(["2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31",
                          "2026-08-01", "2026-08-03", "2026-08-04"])  # 08-01 is a Saturday
    close = [100, 95, 95, 119, 119, 118, 117]
    vol = [1, 1, 0, 1, 1, 1, 1]  # 07-30 repeats the close with no volume: stale
    df = pd.DataFrame({"close": close, "volume": vol}, index=idx)
    out, log = D.clean(df, "TEST")
    assert pd.Timestamp("2026-07-30") not in out.index
    assert pd.Timestamp("2026-08-01") not in out.index
    assert pd.Timestamp("2026-07-31") in out.index  # +22% move is kept, only flagged
    r = D.log_returns(out)
    assert np.isclose(r.max(), np.log(119 / 95))
    assert any(row["action"] == "keep" for row in log)
