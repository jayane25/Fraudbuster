"""Checks for the ML side: clean train / test split, no label or future leakage, and that the trained hybrid
model tells a donation drive apart from smurfing."""
import os

import joblib
import pandas as pd
import pytest

import aml
import features as F
from conftest import ROOT, T0

DATA = os.path.join(ROOT, "data")
MODEL = os.path.join(ROOT, "models", "fraudbusters_model.joblib")


def _accounts(rows):
    """rows: (account, holder, kind, days_open_before_T0)"""
    return pd.DataFrame([dict(account=a, holder=h, kind=k, opened=T0 - pd.Timedelta(days=d)) for a, h, k, d in rows]
                        ).set_index("account")


@pytest.mark.skipif(not os.path.exists(os.path.join(DATA, "train.csv")), reason="run src/train_model.py first")
def test_train_and_test_files_are_split_by_date_without_overlap():
    tr = pd.read_csv(os.path.join(DATA, "train.csv"), parse_dates=["ts"])
    te = pd.read_csv(os.path.join(DATA, "test.csv"), parse_dates=["ts"])
    assert tr.ts.max() < te.ts.min(), "every test transfer must be later than every training transfer"
    assert not set(tr.tx_id) & set(te.tx_id)
    assert len(tr) + len(te) == len(pd.read_csv(os.path.join(DATA, "transactions.csv")))
    assert te.is_laundering.sum() > 0 and tr.is_laundering.sum() > 0


def test_features_never_include_the_label():
    for f in F.HYBRID_FEATURES:
        assert "laundering" not in f and "typology" not in f and "scenario" not in f


def test_history_features_do_not_peek_into_the_future(make_df):
    rows = [(f"S{i % 7}", f"R{i % 5}", 1000 + 37 * i, i * 3) for i in range(120)]
    df = make_df(rows)
    acc = _accounts([(x, x, "individual", 400) for x in set(df.src) | set(df.dst)])
    full = F.graph_features(df, acc)
    cut = df.ts.iloc[60]
    early = F.graph_features(df[df.ts <= cut], acc)
    backward = ["dst_in_72h", "dst_in_72h_cv", "src_out_72h", "src_in_72h", "src_paid_before_48h", "pair_before",
                "reverse_before", "dst_senders_so_far", "src_payees_so_far"]
    pd.testing.assert_frame_equal(full.loc[early.index, backward], early[backward])


def test_same_owner_chain_is_explained_as_own_money(make_df):
    df = make_df([("A1", "A2", 50_000, 0), ("A2", "A3", 50_000, 1), ("A3", "A4", 50_000, 2)])
    acc = _accounts([(a, "CUST1", "individual", 3000) for a in ("A1", "A2", "A3", "A4")])
    alerts = aml.detect_all(df)
    X = F.build(df, acc, alerts)
    chain = next(a for a in alerts if a.typology == "mule_chain")
    texts = [s["text"] for s in F.signals(chain, df, acc, X) if s["side"] == "genuine"]
    assert any("same customer" in t for t in texts)


@pytest.mark.skipif(not os.path.exists(MODEL), reason="run src/train_model.py first")
def test_model_scores_donation_drive_below_smurfing(make_df):
    # Donation drive: an 8 year old NGO gets 15 round donations from long standing customers and keeps the money.
    donation = [(f"D{i}", "NGO", 5000, i * 3) for i in range(15)]
    # Smurfing: a 2 month old personal account gets 15 deposits just under ₹10,000, then sends it all out next day.
    smurf = [(f"M{i}", "COL", 9500 + 20 * i, 200 + i * 3) for i in range(15)] + [("COL", "EXIT", 140_000, 250)]
    df = make_df(donation + smurf)
    acc = _accounts([("NGO", "NGO", "ngo", 3000), ("COL", "COL", "individual", 60), ("EXIT", "EXIT", "individual", 30)]
                    + [(f"D{i}", f"D{i}", "individual", 2000) for i in range(15)]
                    + [(f"M{i}", f"M{i}", "individual", 90) for i in range(15)])
    bundle = joblib.load(MODEL)
    p = bundle["model"].predict_proba(F.build(df, acc)[bundle["features"]])[:, 1]
    p_donation = p[(df.dst == "NGO").to_numpy()].mean()
    p_smurf = p[(df.dst == "COL").to_numpy()].mean()
    assert p_donation < bundle["threshold"] <= p_smurf
