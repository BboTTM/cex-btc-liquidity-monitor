# 交易所 BTC 盘口深度

比较四家交易所 BTC 永续盘口深度、滑点与执行方式，输出中文报告。

## 安装

将本仓库克隆到 Codex 的 skills 目录：

```sh
git clone https://github.com/BboTTM/cex-btc-liquidity-monitor.git ~/.codex/skills/cex-btc-liquidity-monitor
```

阅读 [SKILL.md](SKILL.md) 使用；运行环境和输入说明见其中链接。

仓库只包含可复用流程、脚本和模板；业务运行数据保存在仓库之外。安装不会创建定时任务、发送消息或自动写入业务系统。

## 运行环境

Python 3.10+（python-docx）、Node.js、Playwright 及其 Chromium。Codex 中使用 workspace dependencies 提供的环境，通过 `--node` 和 `--node-modules` 传入对应路径。无需交易所凭证；脚本不会下单。

离线指标验证：`python scripts/test_metrics.py`。
