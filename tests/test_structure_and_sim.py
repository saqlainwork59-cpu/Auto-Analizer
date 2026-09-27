import numpy as np
import pytest

from app.analysis.structure import candle_patterns, find_swings, known_swings, market_structure, sr_zones
from app.analysis.tradesim import simulate


def test_swings_confirmed_only_after_right_bars():
    high = np.array([1, 2, 3, 5, 3, 2, 1, 2, 3], dtype=float)
    low = high - 0.5
    sw = [s for s in find_swings(high, low, 2, 2) if s.kind == "high"]
    assert len(sw) == 1 and sw[0].idx == 3 and sw[0].confirmed_at == 5
    assert known_swings(sw, 4) == []
    assert known_swings(sw, 5) == sw


def test_market_structure_labels():
    from app.analysis.structure import Swing

    bull = [Swing(1, 3, 10, "high"), Swing(2, 4, 5, "low"), Swing(5, 7, 12, "high"), Swing(6, 8, 6, "low")]
    assert market_structure(bull, 11).label == "bullish"
    bear = [Swing(1, 3, 12, "high"), Swing(2, 4, 6, "low"), Swing(5, 7, 10, "high"), Swing(6, 8, 5, "low")]
    s = market_structure(bear, 4)
    assert s.label == "bearish" and s.bos == "down"


def test_zones_cluster_and_classify():
    from app.analysis.structure import Swing

    swings = [Swing(i, i + 3, p, "low") for i, p in enumerate([100.0, 100.2, 100.1])] + [Swing(10, 13, 110.0, "high")]
    zones = sr_zones(swings, close=105.0, atr=1.0, t=20)
    sup = [z for z in zones if z.kind == "support"]
    res = [z for z in zones if z.kind == "resistance"]
    assert len(sup) == 1 and sup[0].touches == 3 and sup[0].low < 100.0 < 100.2 < sup[0].high
    assert len(res) == 1 and res[0].low > 105


def test_candle_patterns():
    o = np.array([10.0, 9.65]); c = np.array([9.7, 10.2]); h = np.array([10.05, 10.25]); lo = np.array([9.65, 9.6])
    names = [n for n, _ in candle_patterns(o, h, lo, c, 1)]
    assert "bullish engulfing" in names
    o = np.array([10.0, 10.0]); c = np.array([10.0, 10.05]); h = np.array([10.1, 10.1]); lo = np.array([9.9, 9.0])
    assert ("hammer / bullish pin bar", "bullish") in candle_patterns(o, h, lo, c, 1)


def bars(rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


KW = dict(expiry_bars=3, max_hold_bars=10, tp1_fraction=0.5, move_stop_to_breakeven=True)


def test_long_hits_tp1_then_tp2():
    # entry 100, stop 98 (risk 2), tp1 104 (2R), tp2 106 (3R)
    o, h, lo, c = bars([[101, 101.5, 99.8, 100.5], [100.5, 104.2, 100.2, 104], [104, 106.5, 103.5, 106]])
    r = simulate(1, 100, 98, 104, 106, o, h, lo, c, **KW)
    assert r.status == "WIN_TP2" and r.fill_idx == 0
    assert r.r_multiple == pytest.approx(0.5 * 2 + 0.5 * 3)


def test_stop_assumed_first_when_both_inside_one_candle():
    o, h, lo, c = bars([[100.5, 100.6, 99.9, 100.2], [100.2, 105, 97.5, 101]])
    r = simulate(1, 100, 98, 104, 106, o, h, lo, c, **KW)
    assert r.status == "LOSS" and r.r_multiple == pytest.approx(-1.0)


def test_breakeven_after_tp1():
    o, h, lo, c = bars([[100.5, 100.6, 99.9, 100.2], [100.2, 104.5, 100.1, 103], [103, 103.2, 99.5, 99.8]])
    r = simulate(1, 100, 98, 104, 106, o, h, lo, c, **KW)
    assert r.status == "WIN_TP1" and r.r_multiple == pytest.approx(1.0)  # 0.5 * 2R + 0.5 * 0R


def test_expired_and_missed_move():
    o, h, lo, c = bars([[101, 102, 100.5, 101.5]] * 5)
    assert simulate(1, 100, 98, 104, 106, o, h, lo, c, **KW).status == "EXPIRED"
    o, h, lo, c = bars([[101, 104.5, 100.5, 104]])
    r = simulate(1, 100, 98, 104, 106, o, h, lo, c, **KW)
    assert r.status == "EXPIRED" and "missed" in r.note


def test_gap_through_stop_before_entry_invalidates():
    o, h, lo, c = bars([[97, 97.5, 96, 97]])
    assert simulate(1, 100, 98, 104, 106, o, h, lo, c, **KW).status == "INVALIDATED"


def test_gap_through_stop_after_fill_exits_at_open_with_slippage():
    o, h, lo, c = bars([[100.5, 100.6, 99.9, 100.2], [97, 97.2, 96.5, 97]])
    r = simulate(1, 100, 98, 104, 106, o, h, lo, c, slippage_pct=0.001, **KW)
    assert r.status == "LOSS" and r.exit_price == pytest.approx(97 * 0.999)
    assert r.r_multiple < -1.0


def test_short_is_symmetric():
    o, h, lo, c = bars([[99.5, 100.2, 99.4, 99.8], [99.8, 99.9, 95.8, 96], [96, 96.5, 93.5, 94]])
    r = simulate(-1, 100, 102, 96, 94, o, h, lo, c, **KW)
    assert r.status == "WIN_TP2" and r.r_multiple == pytest.approx(0.5 * 2 + 0.5 * 3)


def test_time_exit_and_incomplete():
    o, h, lo, c = bars([[100.2, 100.5, 99.9, 100.1]] + [[100.1, 100.6, 99.5, 100.3]] * 11)
    r = simulate(1, 100, 98, 104, 106, o, h, lo, c, **KW)
    assert r.status == "TIME_EXIT" and r.complete
    r2 = simulate(1, 100, 98, 104, 106, o[:3], h[:3], lo[:3], c[:3], **KW)
    assert r2.status == "OPEN" and not r2.complete


def test_invalid_geometry_rejected():
    with pytest.raises(ValueError):
        simulate(1, 100, 101, 104, 106, *bars([[100, 100, 100, 100]]), **KW)
