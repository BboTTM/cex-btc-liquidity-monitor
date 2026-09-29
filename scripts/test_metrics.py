from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

import analyze


def book(ts, venue="bitget"):
    bids = [[100.0 - i * 0.1, 10.0] for i in range(20)]
    asks = [[100.1 + i * 0.1, 10.0] for i in range(20)]
    return {"exchange": venue, "symbol": "BTC", "sample_ts_ms": ts,
            "exchange_ts_ms": ts - 10, "recv_ts_ms": ts - 5,
            "sequence": ts, "bids": bids, "asks": asks}


class MetricTests(unittest.TestCase):
    def test_depth_and_spread(self):
        row = book(1_000)
        self.assertAlmostEqual(analyze.depth(row["bids"], 1), 1_000.0)
        self.assertAlmostEqual(analyze.depth(row["bids"], 10), sum(p * q for p, q in row["bids"][:10]))
        self.assertGreater(analyze.spread_bps(row), 0)

    def test_aggregation_direction_and_completeness(self):
        bid = analyze.aggregate_seven(book(1_000)["bids"], "bid", 0.1)
        ask = analyze.aggregate_seven(book(1_000)["asks"], "ask", 0.1)
        self.assertEqual(bid["anchor_price"], 100.0)
        self.assertEqual(bid["outer_price"], 99.4)
        self.assertEqual(ask["anchor_price"], 100.1)
        self.assertEqual(ask["outer_price"], 100.7)
        self.assertTrue(bid["complete"])
        self.assertTrue(ask["complete"])
        short = analyze.aggregate_seven(book(1_000)["bids"][:5], "bid", 0.1)
        self.assertFalse(short["complete"])

    def test_one_usdt_aggregation_and_lower_bound(self):
        dense = book(1_000)
        self.assertFalse(analyze.aggregate_seven(dense["bids"], "bid", 1.0)["complete"])
        bid_levels = [[100.9 - i, 1.0] for i in range(8)]
        ask_levels = [[100.1 + i, 1.0] for i in range(8)]
        bid = analyze.aggregate_seven(bid_levels, "bid", 1.0)
        ask = analyze.aggregate_seven(ask_levels, "ask", 1.0)
        self.assertEqual(bid["anchor_price"], 100.0)
        self.assertEqual(bid["outer_price"], 94.0)
        self.assertEqual(ask["anchor_price"], 101.0)
        self.assertEqual(ask["outer_price"], 107.0)
        self.assertTrue(bid["complete"])
        self.assertTrue(ask["complete"])
        metrics = analyze.venue_metrics([dense])
        self.assertIn("p1_seven", metrics)
        self.assertEqual(metrics["p1_seven"]["bid"]["coverage_pct"], 0)
        self.assertIsNotNone(metrics["p1_seven"]["bid"]["observed_lower_bound"]["p10"])

    def test_fixed_quote_slippage_direction(self):
        asks = [[100.0, 1.0], [101.0, 1.0]]
        buy = analyze.simulate(asks, "buy", 150.0)
        self.assertTrue(buy["complete"])
        self.assertGreater(buy["slippage_bps"], 0)
        bids = [[100.0, 1.0], [99.0, 1.0]]
        sell = analyze.simulate(bids, "sell", 150.0)
        self.assertTrue(sell["complete"])
        self.assertGreater(sell["slippage_bps"], 0)
        self.assertFalse(analyze.simulate(asks, "buy", 1_000.0)["complete"])

    def test_end_to_end_windows_and_health(self):
        venues = ["bitget", "binance", "okx", "bybit"]
        started = 1_800_000_000_000
        rows = []
        attempts = []
        for offset in range(5, 65, 5):
            for venue in venues:
                ts = started + offset * 1_000
                rows.append(book(ts, venue))
                attempts.append({"exchange": venue, "sample_ts_ms": ts, "valid": True, "reason": None})
        meta = {"ready": True, "started_ms": started, "ended_ms": started + 60_000,
                "sample_interval_seconds": 5, "windows_seconds": [60], "venues": venues,
                "attempts": attempts, "health": {v: {"sequence_gaps": 0} for v in venues}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / "raw.jsonl.gz"
            with gzip.open(raw, "wt", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")
            meta_path = root / "meta.json"
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            result = analyze.analyze(raw, meta_path)
        window = result["windows"]["1m"]
        self.assertEqual(window["common_valid_snapshots"], 12)
        self.assertEqual(set(window["eligible_venues"]), set(venues))
        self.assertTrue(all(window["venues"][v]["health"]["rank_eligible"] for v in venues))

    def test_health_failure_suppresses_ranking(self):
        meta = {"attempts": [{"exchange": "bitget", "sample_ts_ms": i, "valid": i < 8,
                               "reason": None if i < 8 else "stale"} for i in range(10)],
                "health": {"bitget": {"sequence_gaps": 0}}}
        health = analyze.health_for_window(meta, "bitget", 100, 12)
        self.assertFalse(health["rank_eligible"])

    def test_child_cap_uses_smaller_constraint(self):
        metrics = {
            "slippage": {
                "10000": {"buy": {"coverage_pct": 100, "p90_bps": 0.0}},
                "50000": {"buy": {"coverage_pct": 95, "p90_bps": 0.08}},
                "100000": {"buy": {"coverage_pct": 75, "p90_bps": 0.04}},
            },
            "p01_seven": {"ask": {"coverage_pct": 100, "p10": 200_000}},
        }
        cap = analyze.child_cap(metrics, "buy")
        self.assertEqual(cap["slippage_cap_usdt"], 50_000)
        self.assertEqual(cap["depth_cap_usdt"], 30_000)
        self.assertEqual(cap["recommended_usdt"], 30_000)

    def test_low_coverage_has_no_invented_slippage(self):
        rows = [book(1_000 + i) for i in range(10)]
        metrics = analyze.venue_metrics(rows)
        item = metrics["slippage"]["1000000"]["buy"]
        self.assertLess(item["coverage_pct"], 80)
        if item["complete_samples"] == 0:
            self.assertIsNone(item["p90_bps"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
