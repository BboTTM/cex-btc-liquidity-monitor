# Methodology

## Time windows and health gates

- The default windows are cumulative from the first valid aligned sample: `0–1`, `0–3`, and `0–5` minutes.
- Sampling is on a 5-second grid. Expected snapshots per venue are 12, 36, and 60.
- A snapshot is valid only when both sides contain at least 20 levels, best bid is below best ask, and the last exchange message is no more than 1,500 ms old.
- Four-venue rankings use the intersection of valid grid timestamps from venues that pass the health gate. A venue passes when valid rate is at least 80%, stale rate is at most 5%, there are no crossed books, and no sequence gap is recorded in the window.

## Depth and spread

- Level notional is `price × normalized BTC quantity`.
- `bidN` and `askN` are cumulative levels 1 through N, not the isolated Nth level.
- Spread in basis points is `(best ask − best bid) ÷ midpoint × 10,000`.
- Front concentration is `BBO notional ÷ Top 20 notional`; Top 10 concentration is `Top 10 ÷ Top 20`.
- Binance, Bybit, and Bitget BTC-USDT linear book quantities are treated as BTC quantities. OKX `BTC-USDT-SWAP` book size is in contracts and is multiplied by its BTC contract value (`ctVal`, currently 0.01 BTC). Do not extend the skill to another symbol until the corresponding instrument metadata is wired in and tested.

## Metric definitions

- **P10 depth**: the 10th percentile of depth across valid aligned snapshots. About 90% of the observed valid snapshots are at or above this value, so it is used as a conservative available-depth reference.
- **P90 slippage**: the 90th percentile of simulated slippage among samples in which saved Top 20 fully completes the tested amount. About 90% of those completed samples are at or below this value. It is not calculated from incomplete samples.
- **Coverage**: completed samples divided by all valid aligned snapshots for that venue and window. For slippage it means Top 20 can fully fill the tested amount; for a seven-bucket metric it means saved Top 20 crosses the seventh bucket boundary.
- **Parent order**: the total notional the user intends to execute over an execution horizon. It can be split across time, venues, and order types.
- **Child order**: one smaller order created by splitting a parent order. The reported child-order cap is a conservative starting size for one active child, not a guaranteed fill amount.

## Price-bucket aggregation

“0.1 precision” and “1 precision” mean 0.1 USDT and 1 USDT bucket widths, not the API decimal precision.

- Bid bucket: `floor(price ÷ width) × width`.
- Ask bucket: `ceil(price ÷ width) × width`.
- Seven buckets start at the outward-rounded best price and extend six bucket steps away from the market.
- The amount is the sum of `raw price × BTC quantity` for all raw levels assigned to those seven buckets.
- Completeness requires the deepest saved price to reach or cross the seventh bucket boundary. Otherwise the observed amount is only a lower bound and must not be ranked as complete depth.
- When the exchange raw tick is finer than the selected width, multiple raw levels may fall into one bucket.
- If Top 20 does not cross the seventh boundary, report the accumulated amount as an observed lower bound. This is a storage-depth limitation, not proof that the exchange has no further liquidity. Incomplete results do not enter complete-depth rankings.

## Static slippage

- Test notionals are 10,000; 50,000; 100,000; 250,000; 500,000; and 1,000,000 USDT.
- An active buy consumes asks from low to high. An active sell consumes bids from high to low.
- Each simulation fixes quote notional in USDT. At each level, take `min(remaining quote, price × available BTC)` and convert the taken quote to BTC at that level.
- VWAP is `total filled USDT ÷ total filled BTC`.
- Buy slippage is `(VWAP ÷ best ask − 1) × 10,000` bps.
- Sell slippage is `(1 − VWAP ÷ best bid) × 10,000` bps.
- If Top 20 cannot fill the whole target, the sample is incomplete: do not invent a price for the unfilled amount.
- Coverage is the percentage of aligned valid snapshots that fully fill the target. P50/P90 slippage is conditional on complete samples; append `*` below 80% coverage.

## Suggested active child-order cap

Calculate buy and sell separately.

1. Find the largest test notional with coverage at least 90% and P90 slippage no more than 0.10 bps.
2. Compute 15% of the opposing 0.1-USDT seven-bucket P10 depth: asks for an active buy, bids for an active sell.
3. Take the smaller value and round down to the nearest 1,000 USDT.
4. If seven-bucket completeness is below 80%, omit the depth-derived cap and flag the recommendation as data-limited.

The cap is an execution starting point, not a guarantee. Public visible depth omits queue priority, cancellations, latency, self-impact, fees, fills, and post-trade markout.

## Output files

- `raw.jsonl.gz`: valid normalized Top 20 snapshots.
- `meta.json`: run parameters, attempts, invalid reasons, connection health, and checkpoint summaries.
- `summary.json`: window metrics, rankings, slippage, coverage, and direction-specific caps.
- `report.docx`: Chinese management report covering 1/3/5-minute results, methodology, health, and execution guidance.

## Official interface references

- Binance USD-M partial depth stream: <https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Partial-Book-Depth-Streams>
- Bybit V5 orderbook: <https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook>
- OKX V5 public instruments and books: <https://www.okx.com/docs-v5/en/>
- Bitget UTA orderbook: <https://www.bitget.com/api-doc/uta/websocket/public/Order-Book-Channel>
- Bitget futures instrument metadata: <https://www.bitget.com/api-doc/classic/contract/market/Get-All-Symbols-Contracts>
