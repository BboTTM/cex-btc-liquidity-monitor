"use strict";

const fs = require("fs");
const net = require("net");
const path = require("path");
const zlib = require("zlib");
const { chromium } = require("playwright");

function parseArgs(argv) {
  const out = {
    outputDir: null,
    duration: 300,
    sampleSeconds: 5,
    windows: [60, 180, 300],
    venues: ["bitget", "binance", "okx", "bybit"],
    proxy: "auto",
    readyTimeout: 45,
  };
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[++i];
    if (key === "--output-dir") out.outputDir = path.resolve(value);
    else if (key === "--duration") out.duration = Number(value);
    else if (key === "--sample-seconds") out.sampleSeconds = Number(value);
    else if (key === "--windows") out.windows = value.split(",").map(Number);
    else if (key === "--venues") out.venues = value.split(",");
    else if (key === "--proxy") out.proxy = value;
    else if (key === "--ready-timeout") out.readyTimeout = Number(value);
    else throw new Error(`unknown argument: ${key}`);
  }
  if (!out.outputDir) throw new Error("--output-dir is required");
  if (Math.max(...out.windows) > out.duration) throw new Error("duration must cover every window");
  const supported = new Set(["bitget", "binance", "okx", "bybit"]);
  if (out.venues.some((v) => !supported.has(v))) throw new Error("only bitget,binance,okx,bybit are supported");
  return out;
}

function tcpReachable(host, port, timeoutMs = 250) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host, port });
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(value);
    };
    socket.setTimeout(timeoutMs, () => done(false));
    socket.on("connect", () => done(true));
    socket.on("error", () => done(false));
  });
}

async function proxyCandidates(mode) {
  if (mode === "none" || mode === "direct") return [null];
  if (mode !== "auto") return [mode];
  const candidates = [null];
  for (const key of ["HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"]) {
    const value = process.env[key];
    if (value && !candidates.includes(value)) candidates.push(value);
  }
  if (await tcpReachable("127.0.0.1", 7890)) {
    const local = "http://127.0.0.1:7890";
    if (!candidates.includes(local)) candidates.push(local);
  }
  return candidates;
}

async function collectAttempt(options, proxyServer, readyTimeoutSeconds) {
  const chromePath = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const launchOptions = { headless: true };
  if (fs.existsSync(chromePath)) launchOptions.executablePath = chromePath;
  if (proxyServer) launchOptions.proxy = { server: proxyServer };
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage();
  page.on("console", (msg) => {
    const text = msg.text();
    if (text.startsWith("CHECKPOINT ")) process.stdout.write(`${text}\n`);
  });

  const result = await page.evaluate(async ({ duration, sampleSeconds, windows, venues, readyTimeoutSeconds }) => {
    const state = Object.fromEntries(venues.map((venue) => [venue, {
      bids: new Map(), asks: new Map(), ws: null, connected: false, messages: 0,
      exchangeTs: null, recvTs: null, sequence: null, reconnects: 0,
      sequenceGaps: 0, errors: [], generation: 0,
    }]));
    const samples = [];
    const attempts = [];
    const timers = [];
    let running = true;

    const clearBook = (s) => { s.bids.clear(); s.asks.clear(); s.recvTs = null; s.sequence = null; };
    const updateSide = (book, rows, multiplier = 1) => {
      for (const row of rows || []) {
        const price = Number(row[0]);
        const quantity = Number(row[1]) * multiplier;
        if (!Number.isFinite(price) || !Number.isFinite(quantity) || price <= 0 || quantity < 0) continue;
        if (quantity === 0) book.delete(price); else book.set(price, quantity);
      }
    };
    const replaceBook = (s, bids, asks, multiplier = 1) => {
      s.bids.clear(); s.asks.clear();
      updateSide(s.bids, bids, multiplier); updateSide(s.asks, asks, multiplier);
    };
    const top = (book, descending, count = 20) => [...book.entries()]
      .sort((a, b) => descending ? b[0] - a[0] : a[0] - b[0]).slice(0, count);
    const parsePayload = async (event) => {
      let text;
      if (typeof event.data === "string") text = event.data;
      else if (event.data instanceof Blob) text = await event.data.text();
      else text = new TextDecoder().decode(event.data);
      if (text === "pong" || text === "ping") return text;
      return JSON.parse(text);
    };

    const specs = {
      binance: {
        url: "wss://fstream.binance.com/ws/btcusdt@depth20@100ms",
        onOpen: null,
        onData: (d, s, ws) => {
          if (d?.e !== "depthUpdate") return;
          const previous = Number(d.pu); const next = Number(d.u);
          if (Number.isFinite(previous) && s.sequence !== null && previous !== s.sequence) {
            s.sequenceGaps += 1; s.errors.push(`sequence_gap:${s.sequence}->${previous}`); ws.close(); return;
          }
          replaceBook(s, d.b, d.a, 1);
          s.exchangeTs = Number(d.T || d.E); s.sequence = next; s.recvTs = Date.now(); s.messages += 1;
        },
      },
      bybit: {
        url: "wss://stream.bybit.com/v5/public/linear",
        onOpen: (ws) => ws.send(JSON.stringify({ op: "subscribe", args: ["orderbook.50.BTCUSDT"] })),
        onData: (d, s, ws) => {
          if (d?.topic !== "orderbook.50.BTCUSDT" || !d.data) return;
          const next = Number(d.data.u ?? d.data.seq);
          if (d.type !== "snapshot" && Number.isFinite(next) && s.sequence !== null && next !== s.sequence + 1) {
            s.sequenceGaps += 1; s.errors.push(`sequence_gap:${s.sequence}->${next}`); ws.close(); return;
          }
          if (d.type === "snapshot" || s.bids.size === 0) replaceBook(s, d.data.b, d.data.a, 1);
          else { updateSide(s.bids, d.data.b, 1); updateSide(s.asks, d.data.a, 1); }
          s.exchangeTs = Number(d.ts || d.data.ts); s.sequence = Number.isFinite(next) ? next : s.sequence;
          s.recvTs = Date.now(); s.messages += 1;
        },
        ping: (ws) => ws.send(JSON.stringify({ op: "ping" })),
      },
      okx: {
        url: "wss://ws.okx.com/ws/v5/public",
        onOpen: (ws) => ws.send(JSON.stringify({ op: "subscribe", args: [{ channel: "books", instId: "BTC-USDT-SWAP" }] })),
        onData: (d, s, ws) => {
          if (d?.arg?.channel !== "books" || !d.data?.length) return;
          const row = d.data[0];
          const previous = Number(row.prevSeqId); const next = Number(row.seqId);
          if (d.action !== "snapshot" && Number.isFinite(previous) && s.sequence !== null && previous !== s.sequence) {
            s.sequenceGaps += 1; s.errors.push(`sequence_gap:${s.sequence}->${previous}`); ws.close(); return;
          }
          if (d.action === "snapshot" || s.bids.size === 0) replaceBook(s, row.bids, row.asks, 0.01);
          else { updateSide(s.bids, row.bids, 0.01); updateSide(s.asks, row.asks, 0.01); }
          s.exchangeTs = Number(row.ts); s.sequence = Number.isFinite(next) ? next : s.sequence;
          s.recvTs = Date.now(); s.messages += 1;
        },
        ping: (ws) => ws.send("ping"),
      },
      bitget: {
        url: "wss://ws.bitget.com/v3/ws/public",
        onOpen: (ws) => ws.send(JSON.stringify({ op: "subscribe", args: [{ instType: "usdt-futures", topic: "books50", symbol: "BTCUSDT" }] })),
        onData: (d, s, ws) => {
          if (d?.arg?.topic !== "books50" || !d.data?.length) return;
          const row = d.data[0];
          const previous = Number(row.pseq ?? row.prevSeqId); const next = Number(row.seq ?? row.seqId);
          if (d.action !== "snapshot" && Number.isFinite(previous) && s.sequence !== null && previous !== s.sequence) {
            s.sequenceGaps += 1; s.errors.push(`sequence_gap:${s.sequence}->${previous}`); ws.close(); return;
          }
          if (d.action === "snapshot" || s.bids.size === 0) replaceBook(s, row.b, row.a, 1);
          else { updateSide(s.bids, row.b, 1); updateSide(s.asks, row.a, 1); }
          s.exchangeTs = Number(row.ts || d.ts); s.sequence = Number.isFinite(next) ? next : s.sequence;
          s.recvTs = Date.now(); s.messages += 1;
        },
        ping: (ws) => ws.send("ping"),
      },
    };

    const connectVenue = (venue) => {
      if (!running) return;
      const s = state[venue]; const spec = specs[venue]; const generation = ++s.generation;
      clearBook(s);
      const ws = new WebSocket(spec.url); s.ws = ws;
      ws.onopen = () => { if (generation !== s.generation) return; s.connected = true; spec.onOpen?.(ws); };
      ws.onmessage = async (event) => {
        try {
          const payload = await parsePayload(event);
          if (payload === "pong" || payload === "ping") return;
          spec.onData(payload, s, ws);
        } catch (error) { s.errors.push(`parse:${String(error)}`); }
      };
      ws.onerror = () => s.errors.push("websocket_error");
      ws.onclose = (event) => {
        if (generation !== s.generation) return;
        s.connected = false; clearBook(s);
        if (running) {
          s.reconnects += 1; s.errors.push(`close:${event.code}:${event.reason || ""}`);
          timers.push(setTimeout(() => connectVenue(venue), Math.min(5000, 500 * (2 ** Math.min(s.reconnects, 4)))));
        }
      };
      if (spec.ping) timers.push(setInterval(() => {
        if (running && s.ws === ws && ws.readyState === WebSocket.OPEN) {
          try { spec.ping(ws); } catch (_) {}
        }
      }, 20000));
    };
    venues.forEach(connectVenue);

    const waitUntil = async (predicate, timeoutMs) => {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        if (predicate()) return true;
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      return false;
    };
    const bookReady = (venue) => {
      const s = state[venue];
      return s.recvTs && Date.now() - s.recvTs <= 1500 && top(s.bids, true).length >= 20 && top(s.asks, false).length >= 20;
    };
    const ready = await waitUntil(() => venues.every(bookReady), readyTimeoutSeconds * 1000);
    const startedMs = Date.now();

    const depth = (levels, count) => levels.slice(0, count).reduce((sum, [p, q]) => sum + p * q, 0);
    const median = (values) => {
      if (!values.length) return null;
      const xs = [...values].sort((a, b) => a - b); const mid = Math.floor(xs.length / 2);
      return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
    };
    const quickCheckpoint = (elapsed) => ({
      elapsed_seconds: elapsed,
      expected_per_venue: Math.floor(elapsed / sampleSeconds),
      venues: Object.fromEntries(venues.map((venue) => {
        const rows = samples.filter((x) => x.exchange === venue && x.sample_ts_ms <= startedMs + elapsed * 1000);
        return [venue, {
          samples: rows.length,
          bid1_median_usdt: median(rows.map((x) => depth(x.bids, 1))),
          ask1_median_usdt: median(rows.map((x) => depth(x.asks, 1))),
          bid20_median_usdt: median(rows.map((x) => depth(x.bids, 20))),
          ask20_median_usdt: median(rows.map((x) => depth(x.asks, 20))),
        }];
      })),
    });

    if (ready) {
      const sampleMs = sampleSeconds * 1000;
      let nextGrid = startedMs + sampleMs;
      const endMs = startedMs + duration * 1000;
      const checkpointSet = new Set(windows);
      while (nextGrid <= endMs) {
        const wait = nextGrid - Date.now();
        if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
        const sampleTs = nextGrid;
        for (const venue of venues) {
          const s = state[venue]; const bids = top(s.bids, true); const asks = top(s.asks, false);
          let reason = null;
          if (!s.recvTs || sampleTs - s.recvTs > 1500) reason = "stale";
          else if (bids.length < 20 || asks.length < 20) reason = "insufficient_levels";
          else if (bids[0][0] >= asks[0][0]) reason = "crossed";
          attempts.push({ exchange: venue, sample_ts_ms: sampleTs, valid: reason === null, reason });
          if (!reason) samples.push({
            exchange: venue, symbol: "BTC", sample_ts_ms: sampleTs,
            exchange_ts_ms: s.exchangeTs, recv_ts_ms: s.recvTs, sequence: s.sequence,
            bids, asks,
          });
        }
        const elapsed = Math.round((sampleTs - startedMs) / 1000);
        if (checkpointSet.has(elapsed)) console.log(`CHECKPOINT ${JSON.stringify(quickCheckpoint(elapsed))}`);
        nextGrid += sampleMs;
      }
    }

    running = false;
    for (const timer of timers) { clearTimeout(timer); clearInterval(timer); }
    for (const venue of venues) {
      const ws = state[venue].ws;
      if (ws && ws.readyState <= WebSocket.OPEN) { try { ws.close(); } catch (_) {} }
    }
    const endedMs = Date.now();
    return {
      ready, started_ms: startedMs, ended_ms: endedMs, duration_seconds: ready ? duration : 0,
      sample_interval_seconds: sampleSeconds, windows_seconds: windows, venues,
      samples, attempts,
      health: Object.fromEntries(venues.map((venue) => {
        const s = state[venue];
        return [venue, { messages: s.messages, last_recv_ts_ms: s.recvTs, reconnects: s.reconnects,
          sequence_gaps: s.sequenceGaps, errors: s.errors }];
      })),
    };
  }, { duration: options.duration, sampleSeconds: options.sampleSeconds, windows: options.windows,
       venues: options.venues, readyTimeoutSeconds });

  await browser.close();
  return result;
}

async function main() {
  const options = parseArgs(process.argv);
  fs.mkdirSync(options.outputDir, { recursive: true });
  const candidates = await proxyCandidates(options.proxy);
  const perAttempt = Math.max(8, Math.floor(options.readyTimeout / candidates.length));
  let best = null; let usedProxy = null;
  const attempts = [];
  for (const candidate of candidates) {
    process.stdout.write(`NETWORK_ATTEMPT ${JSON.stringify({ proxy: candidate || "direct", ready_timeout_seconds: perAttempt })}\n`);
    try {
      const result = await collectAttempt(options, candidate, perAttempt);
      attempts.push({ proxy: candidate || "direct", ready: result.ready,
        messages: Object.fromEntries(Object.entries(result.health).map(([v, h]) => [v, h.messages])) });
      if (!best || result.samples.length > best.samples.length) { best = result; usedProxy = candidate; }
      if (result.ready) break;
    } catch (error) {
      attempts.push({ proxy: candidate || "direct", ready: false, error: String(error) });
    }
  }
  if (!best) best = { ready: false, started_ms: Date.now(), ended_ms: Date.now(), duration_seconds: 0,
    sample_interval_seconds: options.sampleSeconds, windows_seconds: options.windows, venues: options.venues,
    samples: [], attempts: [], health: {} };
  const lines = best.samples.map((row) => JSON.stringify(row)).join("\n") + (best.samples.length ? "\n" : "");
  fs.writeFileSync(path.join(options.outputDir, "raw.jsonl.gz"), zlib.gzipSync(lines));
  const meta = { ...best, samples: undefined, network_attempts: attempts, proxy_used: usedProxy || "direct" };
  fs.writeFileSync(path.join(options.outputDir, "meta.json"), JSON.stringify(meta, null, 2));
  process.stdout.write(`COLLECTION_COMPLETE ${JSON.stringify({ ready: best.ready, samples: best.samples.length,
    output_dir: options.outputDir, proxy_used: meta.proxy_used })}\n`);
  if (!best.ready) process.exitCode = 2;
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
