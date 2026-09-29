---
name: cex-btc-liquidity-monitor
description: Monitor current BTC USDT perpetual order books on Bitget, Binance, OKX, and Bybit for 1, 3, and 5 minutes, assess BBO liquidity, Top 20 depth, and static slippage, and produce a Chinese Word report. Use when the user asks to check or compare current BTC盘口、深度、流动性、BBO或滑点; do not use for historical-only analysis or real order execution.
---

# CEX BTC Liquidity Monitor

Use the bundled scripts for a fresh public-market-data run. This skill never needs credentials or real orders and must not install, start, or modify a VPN.

## Default workflow

1. Load the Codex workspace dependencies and use their Python and Node runtimes.
2. Run `scripts/run_monitor.py` with those runtime paths. Keep the default BTC, four venues, 5-second snapshots, and cumulative 1/3/5-minute windows unless the user explicitly changes them.
3. Keep the collection process attached. At each `CHECKPOINT` line, give the user a concise progress update with the valid sample counts and provisional BBO/Top 20 direction. Do not present a checkpoint as the final result.
4. After the 5-minute checkpoint, read `summary.json` and lead with the side-specific conclusion: active buying consumes asks; active selling consumes bids.
5. Deliver `report.docx` after using the Documents skill to render and inspect every page. If the renderer exposes missing Chinese glyphs, configure fontconfig for the system CJK fonts and render again.

Run command shape:

```bash
<python> scripts/run_monitor.py --node <node> --duration 300 --windows 60,180,300 --sample-seconds 5 --proxy auto
```

The run directory defaults to `~/.codex/artifacts/cex-btc-liquidity-monitor/<run_id>/` and contains `raw.jsonl.gz`, `meta.json`, `summary.json`, and `report.docx`.

## Interpretation rules

- Use only aligned valid snapshots for four-venue rankings. Never forward-fill a missing or stale book.
- Treat valid snapshot rate below 80%, stale rate above 5%, any crossed book, or a sequence gap as a health warning. Suppress the affected venue from rankings but retain it in the health section.
- Report BBO, Top 5/10/20 cumulative notional, spread, both 0.1 USDT and 1 USDT seven-bucket depth, and buy/sell slippage separately. Never use a two-sided total to hide directionality.
- In the Word report, present cumulative 1-minute, 3-minute, and 5-minute depth results as three separate tables. Do not combine the windows into one table.
- Define P10, P90, coverage, parent order, and child order in plain Chinese in every report. State that depth P10 is conservative, slippage P90 is conditional on completed samples, and coverage is the completion share among valid aligned snapshots.
- For 1 USDT aggregation, report an observed lower bound when saved Top 20 does not cross the seventh bucket boundary. Do not rank an incomplete 1 USDT result as complete depth.
- A slippage value is conditional on the saved Top 20 completing the order. Always show coverage beside it; add `*` when coverage is below 80% and state that the estimate is likely optimistic.
- A zero mechanical slippage means the simulated order filled at the current best price in the relevant samples. It does not mean zero spread, fee, delay, cancellation, market impact, or markout.
- Use the generated direction-specific child-order cap as a starting point, not a trading instruction. For 250,000 USDT or larger parent orders, recommend TWAP/POV or predominantly post-only execution with price-protected IOC for urgent residuals.

Read [references/methodology.md](references/methodology.md) when explaining formulas, aggregation completeness, thresholds, output fields, or research limitations.

## Failure handling

- The collector tries the current direct network first, then an existing proxy from environment variables or a reachable local proxy. It never installs networking software.
- It automatically reconnects public WebSockets, clears the affected local book, and waits for a fresh snapshot. Gaps remain gaps.
- If all four venues do not become ready, still generate the diagnostic Word report when possible. Clearly say which venues are missing and do not publish a four-way ranking.
- Do not silently replace a failed live run with older data.
