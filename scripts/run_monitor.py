from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent


def parse_args():
    parser = argparse.ArgumentParser(description="Monitor BTC perpetual order-book liquidity")
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--venues", default="bitget,binance,okx,bybit")
    parser.add_argument("--windows", default="60,180,300")
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--sample-seconds", type=int, default=5)
    parser.add_argument("--proxy", default="auto")
    parser.add_argument("--ready-timeout", type=int, default=45)
    parser.add_argument("--node")
    parser.add_argument("--node-modules")
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def stream(command, env=None):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1, env=env)
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
    return process.wait()


def main():
    args = parse_args()
    if args.symbol.upper() != "BTC":
        raise SystemExit("第一版仅支持BTC；扩展币种前必须验证各所合约乘数。")
    windows = [int(value) for value in args.windows.split(",")]
    if max(windows) > args.duration:
        raise SystemExit("duration must cover every cumulative window")
    node = args.node or shutil.which("node")
    if not node:
        raise SystemExit("Node runtime not found; pass --node from workspace dependencies")
    run_id = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    output = (args.output_dir or Path.home() / ".codex" / "artifacts" /
              "cex-btc-liquidity-monitor" / run_id).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if args.node_modules:
        env["NODE_PATH"] = args.node_modules

    collect = [node, str(HERE / "collect_depth.js"), "--output-dir", str(output),
               "--duration", str(args.duration), "--sample-seconds", str(args.sample_seconds),
               "--windows", args.windows, "--venues", args.venues, "--proxy", args.proxy,
               "--ready-timeout", str(args.ready_timeout)]
    collection_code = stream(collect, env=env)
    raw = output / "raw.jsonl.gz"
    meta = output / "meta.json"
    if not raw.exists() or not meta.exists():
        raise SystemExit(f"collector failed without diagnostic output (exit {collection_code})")

    summary = output / "summary.json"
    analyze = [sys.executable, str(HERE / "analyze.py"), "--raw", str(raw),
               "--meta", str(meta), "--output", str(summary)]
    if stream(analyze) != 0:
        raise SystemExit("analysis failed")

    report = output / "report.docx"
    report_command = [sys.executable, str(HERE / "build_report.py"), "--summary", str(summary),
                      "--output", str(report)]
    if stream(report_command) != 0:
        raise SystemExit("report generation failed")
    payload = {"output_dir": str(output), "raw": str(raw), "summary": str(summary),
               "report": str(report), "collection_exit_code": collection_code}
    print(f"RUN_COMPLETE {json.dumps(payload, ensure_ascii=False)}", flush=True)


if __name__ == "__main__":
    main()
