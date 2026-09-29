from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


TARGETS = (10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000)
METRIC_COUNTS = (1, 5, 10, 20)
DISPLAY = {"bitget": "Bitget", "binance": "Binance", "okx": "OKX", "bybit": "Bybit"}
BUCKET_WIDTHS = (0.1, 1.0)


def percentile(values: list[float], q: float) -> float | None:
    xs = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not xs:
        return None
    position = (len(xs) - 1) * q
    lo, hi = math.floor(position), math.ceil(position)
    return xs[lo] if lo == hi else xs[lo] * (hi - position) + xs[hi] * (position - lo)


def distribution(values: list[float]) -> dict[str, float | None]:
    return {"p10": percentile(values, 0.10), "median": percentile(values, 0.50), "p90": percentile(values, 0.90)}


def depth(levels: list[list[float]], count: int) -> float:
    return sum(float(price) * float(quantity) for price, quantity in levels[:count])


def spread_bps(row: dict) -> float:
    bid, ask = float(row["bids"][0][0]), float(row["asks"][0][0])
    return (ask - bid) / ((ask + bid) / 2) * 10_000


def aggregate_seven(levels: list[list[float]], side: str, width: float = 0.1) -> dict:
    scale = 10
    step = round(width * scale)
    best_tick = round(float(levels[0][0]) * scale)
    anchor = best_tick // step * step if side == "bid" else (best_tick + step - 1) // step * step
    outer = anchor - 6 * step if side == "bid" else anchor + 6 * step
    amount = 0.0
    buckets: set[int] = set()
    for price_raw, quantity_raw in levels:
        price, quantity = float(price_raw), float(quantity_raw)
        tick = round(price * scale)
        bucket = tick // step * step if side == "bid" else (tick + step - 1) // step * step
        inside = bucket >= outer if side == "bid" else bucket <= outer
        if inside:
            amount += price * quantity
            buckets.add(bucket)
    deepest_tick = round(float(levels[-1][0]) * scale)
    complete = deepest_tick <= outer if side == "bid" else deepest_tick >= outer
    return {"usdt": amount, "complete": complete, "populated_buckets": len(buckets),
            "anchor_price": anchor / scale, "outer_price": outer / scale}


def simulate(levels: list[list[float]], side: str, target_usdt: float) -> dict:
    best = float(levels[0][0])
    remaining_quote = float(target_usdt)
    filled_base = 0.0
    levels_used = 0
    for price_raw, quantity_raw in levels:
        price, quantity = float(price_raw), float(quantity_raw)
        take_quote = min(remaining_quote, price * quantity)
        if take_quote > 0:
            filled_base += take_quote / price
            remaining_quote -= take_quote
            levels_used += 1
        if remaining_quote <= 1e-7:
            break
    if remaining_quote > 1e-7:
        return {"complete": False, "slippage_bps": None, "levels_used": None}
    vwap = target_usdt / filled_base
    slippage = (vwap / best - 1) * 10_000 if side == "buy" else (1 - vwap / best) * 10_000
    return {"complete": True, "slippage_bps": max(0.0, slippage), "levels_used": levels_used}


def load_rows(path: Path) -> list[dict]:
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def health_for_window(meta: dict, venue: str, cutoff: int, expected: int) -> dict:
    attempts = [x for x in meta.get("attempts", []) if x.get("exchange") == venue and int(x.get("sample_ts_ms", 0)) <= cutoff]
    valid = sum(bool(x.get("valid")) for x in attempts)
    stale = sum(x.get("reason") == "stale" for x in attempts)
    crossed = sum(x.get("reason") == "crossed" for x in attempts)
    insufficient = sum(x.get("reason") == "insufficient_levels" for x in attempts)
    denominator = expected or len(attempts) or 1
    valid_rate = valid / denominator * 100
    stale_rate = stale / denominator * 100
    sequence_gaps = int(meta.get("health", {}).get(venue, {}).get("sequence_gaps", 0))
    passed = valid_rate >= 80 and stale_rate <= 5 and crossed == 0 and sequence_gaps == 0
    return {"expected": expected, "attempts": len(attempts), "valid": valid,
            "valid_rate_pct": valid_rate, "stale": stale, "stale_rate_pct": stale_rate,
            "crossed": crossed, "insufficient_levels": insufficient,
            "sequence_gaps": sequence_gaps, "rank_eligible": passed}


def venue_metrics(rows: list[dict]) -> dict:
    if not rows:
        return {"sample_count": 0, "depth": {}, "spread_bps": distribution([]),
                "freshness_ms": distribution([]), "p01_seven": {}, "p1_seven": {}, "slippage": {}}
    depth_metrics = {}
    for side, key in (("bid", "bids"), ("ask", "asks")):
        for count in METRIC_COUNTS:
            depth_metrics[f"{side}{count}_usdt"] = distribution([depth(row[key], count) for row in rows])
    price_buckets = {}
    for width in BUCKET_WIDTHS:
        result = {}
        for side, key in (("bid", "bids"), ("ask", "asks")):
            aggregates = [aggregate_seven(row[key], side, width) for row in rows]
            complete = [x for x in aggregates if x["complete"]]
            result[side] = {
                **distribution([x["usdt"] for x in complete]),
                "observed_lower_bound": distribution([x["usdt"] for x in aggregates]),
                "coverage_pct": len(complete) / len(aggregates) * 100,
                "complete_samples": len(complete),
                "total_samples": len(aggregates),
            }
        price_buckets[str(width)] = result
    p01 = price_buckets["0.1"]
    p1 = price_buckets["1.0"]
    slippage = {}
    for target in TARGETS:
        pair = {}
        for side, key in (("buy", "asks"), ("sell", "bids")):
            sims = [simulate(row[key], side, target) for row in rows]
            complete = [x for x in sims if x["complete"]]
            pair[side] = {
                "coverage_pct": len(complete) / len(sims) * 100,
                "complete_samples": len(complete), "total_samples": len(sims),
                "p50_bps": percentile([x["slippage_bps"] for x in complete], 0.50),
                "p90_bps": percentile([x["slippage_bps"] for x in complete], 0.90),
                "levels_p90": percentile([x["levels_used"] for x in complete], 0.90),
            }
        slippage[str(target)] = pair
    bid1 = depth_metrics["bid1_usdt"]["median"] or 0
    ask1 = depth_metrics["ask1_usdt"]["median"] or 0
    bid10 = depth_metrics["bid10_usdt"]["median"] or 0
    ask10 = depth_metrics["ask10_usdt"]["median"] or 0
    bid20 = depth_metrics["bid20_usdt"]["median"] or 0
    ask20 = depth_metrics["ask20_usdt"]["median"] or 0
    return {
        "sample_count": len(rows), "depth": depth_metrics,
        "spread_bps": distribution([spread_bps(row) for row in rows]),
        "freshness_ms": distribution([max(0, int(row["sample_ts_ms"]) - int(row["recv_ts_ms"])) for row in rows]),
        "concentration": {
            "bid1_over_bid20": bid1 / bid20 if bid20 else None,
            "ask1_over_ask20": ask1 / ask20 if ask20 else None,
            "bid10_over_bid20": bid10 / bid20 if bid20 else None,
            "ask10_over_ask20": ask10 / ask20 if ask20 else None,
            "top20_imbalance": (bid20 - ask20) / (bid20 + ask20) if bid20 + ask20 else None,
        },
        "p01_seven": p01, "p1_seven": p1,
        "price_bucket_seven": price_buckets, "slippage": slippage,
    }


def child_cap(metrics: dict, side: str) -> dict:
    eligible = []
    for target in TARGETS:
        item = metrics.get("slippage", {}).get(str(target), {}).get(side, {})
        p90 = item.get("p90_bps")
        if item.get("coverage_pct", 0) >= 90 and p90 is not None and p90 <= 0.10:
            eligible.append(target)
    slip_cap = max(eligible) if eligible else 0
    book_side = "ask" if side == "buy" else "bid"
    aggregate = metrics.get("p01_seven", {}).get(book_side, {})
    complete = aggregate.get("coverage_pct", 0) >= 80
    depth_cap = (aggregate.get("p10") or 0) * 0.15 if complete else None
    if depth_cap is None:
        cap = slip_cap
    else:
        cap = min(slip_cap, depth_cap) if slip_cap else 0
    cap = math.floor(cap / 1_000) * 1_000
    return {"recommended_usdt": cap, "slippage_cap_usdt": slip_cap,
            "depth_cap_usdt": depth_cap, "data_limited": not complete}


def build_rankings(venue_results: dict, eligible: list[str]) -> dict:
    specs = {
        "bid1": ("depth", "bid1_usdt"), "ask1": ("depth", "ask1_usdt"),
        "bid10": ("depth", "bid10_usdt"), "ask10": ("depth", "ask10_usdt"),
        "bid20": ("depth", "bid20_usdt"), "ask20": ("depth", "ask20_usdt"),
    }
    rankings = {}
    for name, (_, metric) in specs.items():
        values = [(venue, venue_results[venue]["metrics"]["depth"].get(metric, {}).get("median")) for venue in eligible]
        rankings[name] = [venue for venue, value in sorted(values, key=lambda x: x[1] or -1, reverse=True) if value is not None]
    spread_values = [(venue, venue_results[venue]["metrics"]["spread_bps"].get("median")) for venue in eligible]
    rankings["spread"] = [venue for venue, value in sorted(spread_values, key=lambda x: x[1] if x[1] is not None else float("inf")) if value is not None]
    return rankings


def conclusions(rankings: dict, venues: dict, eligible: list[str]) -> list[str]:
    if len(eligible) < 4:
        missing = [DISPLAY[v] for v in venues if v not in eligible]
        return [f"仅{len(eligible)}家交易所通过数据健康门槛，取消完整四所排名；需重点检查：{'、'.join(missing) or '无'}。"]
    items = [
        f"买盘前20档排序：{' > '.join(DISPLAY[v] for v in rankings['bid20'])}；主动卖出优先看该侧承接。",
        f"卖盘前20档排序：{' > '.join(DISPLAY[v] for v in rankings['ask20'])}；主动买入优先看该侧承接。",
        f"价差从窄到宽：{' > '.join(DISPLAY[v] for v in rankings['spread'])}。",
    ]
    bg = venues.get("bitget", {}).get("metrics", {})
    if bg:
        buy_cap = child_cap(bg, "buy")["recommended_usdt"]
        sell_cap = child_cap(bg, "sell")["recommended_usdt"]
        items.append(f"按当前P10深度、覆盖率和P90机械滑点，Bitget主动买入/卖出建议子单上限约为{buy_cap/10000:.1f}万/{sell_cap/10000:.1f}万USDT。")
    return items


def analyze(raw_path: Path, meta_path: Path) -> dict:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    rows = load_rows(raw_path)
    venues = meta.get("venues", ["bitget", "binance", "okx", "bybit"])
    started = int(meta.get("started_ms", 0))
    sample_seconds = int(meta.get("sample_interval_seconds", 5))
    output = {
        "run": {
            "ready": bool(meta.get("ready")), "started_ms": started, "ended_ms": meta.get("ended_ms"),
            "start_beijing": datetime.fromtimestamp(started / 1000, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds") if started else None,
            "sample_interval_seconds": sample_seconds, "venues": venues,
            "proxy_used": meta.get("proxy_used"), "network_attempts": meta.get("network_attempts", []),
        },
        "windows": {},
        "method": {
            "window_semantics": "cumulative_from_start", "depth_levels": 20,
            "slippage_targets_usdt": TARGETS, "aggregation_widths_usdt": BUCKET_WIDTHS,
            "health_gate": {"min_valid_rate_pct": 80, "max_stale_rate_pct": 5, "crossed": 0, "sequence_gaps": 0},
        },
    }
    for seconds in meta.get("windows_seconds", [60, 180, 300]):
        cutoff = started + int(seconds) * 1000
        expected = int(seconds) // sample_seconds
        health = {venue: health_for_window(meta, venue, cutoff, expected) for venue in venues}
        eligible = [venue for venue in venues if health[venue]["rank_eligible"]]
        rows_by_venue = {venue: [r for r in rows if r["exchange"] == venue and int(r["sample_ts_ms"]) <= cutoff] for venue in venues}
        if eligible:
            sets = [set(int(r["sample_ts_ms"]) for r in rows_by_venue[v]) for v in eligible]
            common = set.intersection(*sets) if sets else set()
        else:
            common = set()
        venue_results = {}
        for venue in venues:
            comparable = [r for r in rows_by_venue[venue] if int(r["sample_ts_ms"]) in common] if venue in eligible else rows_by_venue[venue]
            metrics = venue_metrics(comparable)
            metrics["child_order_caps"] = {
                "active_buy": child_cap(metrics, "buy"),
                "active_sell": child_cap(metrics, "sell"),
            }
            venue_results[venue] = {"health": health[venue], "metrics": metrics}
        rankings = build_rankings(venue_results, eligible)
        key = f"{int(seconds)//60}m"
        output["windows"][key] = {
            "seconds": int(seconds), "expected_per_venue": expected, "common_valid_snapshots": len(common),
            "eligible_venues": eligible, "venues": venue_results, "rankings": rankings,
            "conclusions": conclusions(rankings, venue_results, eligible),
        }
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--meta", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.raw, args.meta)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": str(args.output), "windows": list(result["windows"]),
                      "ready": result["run"]["ready"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
