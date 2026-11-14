"""Each detector is checked on a tiny hand made case: it must catch the planted pattern
and stay quiet on a look alike that breaks one of its rules."""
import aml


def typologies(df):
    return [a.typology for a in aml.detect_all(df)]


# ---------------------------------------------------------------- fan in (smurfing)
def test_fan_in_catches_many_small_deposits_into_one_account(make_df):
    # 12 different people each send 9,500 (just under the 10,000 limit) to C within 24 hours
    df = make_df([(f"S{i}", "C", 9_500, i * 2) for i in range(12)])
    alerts = aml.detect_fan_in(df)
    assert len(alerts) == 1
    assert alerts[0].focal == "C"
    assert len(alerts[0].accounts) == 13


def test_fan_in_ignores_too_few_senders(make_df):
    # only 5 senders: below FAN_MIN_PARTIES (8)
    df = make_df([(f"S{i}", "C", 9_500, i) for i in range(5)])
    assert aml.detect_fan_in(df) == []


def test_fan_in_ignores_senders_spread_over_weeks(make_df):
    # 12 senders, but one every 4 days, so never 8 inside the 72 hour window
    df = make_df([(f"S{i}", "C", 9_500, i * 96) for i in range(12)])
    assert aml.detect_fan_in(df) == []


# ---------------------------------------------------------------- fan out (dispersion)
def test_fan_out_catches_one_account_paying_many(make_df):
    df = make_df([("D", f"R{i}", 20_000, i) for i in range(10)])
    alerts = aml.detect_fan_out(df)
    assert len(alerts) == 1 and alerts[0].focal == "D"


# ---------------------------------------------------------------- cycle (round tripping)
def test_cycle_catches_money_returning_to_origin(make_df):
    df = make_df([("A", "B", 100_000, 0), ("B", "C", 98_000, 5), ("C", "A", 96_000, 10)])
    assert "cycle" in typologies(df)


def test_cycle_ignores_loop_that_loses_too_much(make_df):
    # each hop keeps only 50%, so it does not look like the same money coming back
    df = make_df([("A", "B", 100_000, 0), ("B", "C", 50_000, 5), ("C", "A", 25_000, 10)])
    assert "cycle" not in typologies(df)


def test_cycle_ignores_slow_hops(make_df):
    # 5 days between hops: longer than CYCLE_MAX_GAP_H (48 h)
    df = make_df([("A", "B", 100_000, 0), ("B", "C", 98_000, 120), ("C", "A", 96_000, 240)])
    assert "cycle" not in typologies(df)


# ---------------------------------------------------------------- mule chain
def test_mule_chain_catches_fast_pass_through(make_df):
    df = make_df([("O", "M1", 50_000, 0), ("M1", "M2", 49_000, 2), ("M2", "M3", 48_000, 4), ("M3", "X", 47_000, 6)])
    alerts = [a for a in aml.detect_all(df) if a.typology == "mule_chain"]
    assert len(alerts) == 1
    assert alerts[0].path == ["O", "M1", "M2", "M3", "X"]


def test_mule_chain_ignores_short_chain(make_df):
    # 2 hops only: below CHAIN_MIN_HOPS (3)
    df = make_df([("O", "M1", 50_000, 0), ("M1", "X", 49_000, 2)])
    assert "mule_chain" not in typologies(df)


# ---------------------------------------------------------------- scoring and baseline
def test_risk_is_between_0_and_100_and_sorted(make_df):
    rows = [(f"S{i}", "C", 9_500, i) for i in range(12)]
    rows += [("A", "B", 100_000, 0), ("B", "Q", 98_000, 5), ("Q", "A", 96_000, 10)]
    alerts = aml.detect_all(make_df(rows))
    risks = [a.risk for a in alerts]
    assert risks and all(0 <= r <= 100 for r in risks)
    assert risks == sorted(risks, reverse=True)
    assert alerts[0].alert_id == "AL0001"


def test_threshold_baseline_flags_large_amounts(make_df):
    df = make_df([("A", "B", 50, 0), ("A", "B", aml.REPORT_THRESHOLD, 1)])
    assert list(aml.threshold_baseline(df)) == [False, True]


def test_normal_traffic_raises_no_alerts(make_df):
    # a handful of ordinary unrelated payments
    df = make_df([("A", "B", 1_200, 0), ("C", "D", 560, 30), ("E", "F", 3_000, 70), ("B", "G", 90, 100)])
    assert aml.detect_all(df) == []
