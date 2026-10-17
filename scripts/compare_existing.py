"""Run the actual frozen neighboring algorithm, with an explicit strategy mismatch."""
import argparse
import ast
import html
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etf_lab.artifacts import encoded, read_json, sha, write_new
from etf_lab.engine import Config, metrics
from etf_lab.backtrader_engine import run_backtest


def load_existing():
    location = ROOT / "vendor/finresearch-lab"
    provenance = read_json(location / "provenance.json")
    raw = (location / "benchmark.py").read_bytes()
    if sha(raw) != provenance["sha256"]:
        raise ValueError("Frozen original source hash mismatch")
    names = {"weights", "rebalance_cost", "attribution", "summarize", "simulate"}
    tree = ast.parse(raw)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names
             or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PROTOCOL" for t in n.targets)]
    assert len(nodes) == len(names) + 1
    module = ast.Module(body=nodes, type_ignores=[])
    namespace = {"np": np, "math": math}
    exec(compile(module, str(location / "benchmark.py"), "exec"), namespace)
    return namespace, provenance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise SystemExit("Choose a new output directory")
    cfg = Config(**read_json(ROOT / "config/spy.json"))
    snapshot = read_json(ROOT / "data/spy.json")
    original, provenance = load_existing()
    rows = snapshot["provider_rows"]
    dates = [r["date"] for r in rows]
    adjusted_close = np.array([[r["Adj Close"]] for r in rows])
    adjusted_open = np.array([[r["Open"] * r["Adj Close"] / r["Close"]] for r in rows])
    start_index = dates.index(cfg.start) - 1
    results = {}
    for strategy in ("market", "momentum"):
        ledger, _ = original["simulate"](["SPY"], dates, adjusted_open, adjusted_close, "SPY", strategy,
                                          one_way=.0015, start_index=start_index)
        assert ledger[0]["execution_date"] == cfg.start and ledger[-1]["end_date"] == cfg.end
        stats = original["summarize"](ledger, 252)
        results[strategy] = {"metrics": stats, "ledger": ledger,
                             "order_days_plus_terminal": sum(r["turnover"] > 1e-12 for r in ledger[:-1]) +
                             int(ledger[-1]["turnover"] > 1e-12)}
    # Independently check the original buy-and-hold math, including both fees.
    p0, p1 = float(adjusted_open[start_index+1, 0]), float(adjusted_open[-1, 0])
    fractional_units = cfg.initial_cash / (p0 * 1.0015)
    independent_ending = fractional_units * p1 * .9985
    original_ending = cfg.initial_cash * (1 + results["market"]["metrics"]["net_total_return"])
    assert abs(independent_ending - original_ending) < 1e-6
    audited = run_backtest(snapshot, cfg)
    # Same open-to-open evaluation endpoints; keep the official close-marked report unchanged.
    aligned = [{"date": d["date"], "equity": d["cash_before"] + d["dividend_cash"] +
                d["shares_before"] * d["open"]} for d in audited["daily"]][1:]
    assert len(aligned) == len(results["market"]["ledger"])
    aligned_metrics = metrics(aligned, cfg.initial_cash, cfg,
                              [t["realized_pnl"] for t in audited["trades"] if t["side"] == "SELL"],
                              len(audited["trades"]))
    report = {"scope": "Actual existing algorithm comparison; strategy differences are explicit",
              "existing_source": provenance, "script_sha256": sha(Path(__file__).read_bytes()),
              "snapshot_sha256": sha((ROOT / "data/spy.json").read_bytes()),
              "interval": {"start": cfg.start + " open", "end": cfg.end + " open", "symbol": "SPY"},
              "existing_protocol": original["PROTOCOL"], "existing_results": results,
              "new_sma_aligned_metrics": aligned_metrics, "new_sma_open_equity": aligned,
              "independent_market_check": {"original_ending": original_ending, "independent_ending": independent_ending,
                                            "absolute_difference": abs(independent_ending-original_ending)},
              "limitations": ["Existing momentum is 20-day positive momentum at 50% weight, rebalanced every five sessions; not SMA20/50.",
                              "Existing adjusted-price fractional exposure embeds distribution reinvestment and terminal liquidation; new ledger uses cash dividends, integer shares and no terminal sale.",
                              "Existing one-way cost 15bp differs from the sample's 5bp slippage plus $1 fee; differences are not isolated implementation error.",
                              "Existing source has no closed-trade PnL or win rate; unavailable values are not invented."]}
    write_new(args.output / "comparison.json", report)
    metrics_table = []
    for name, m in [("逐笔 SMA20/50", aligned_metrics)] + [("原算法 " + n, x["metrics"]) for n, x in results.items()]:
        metrics_table.append('<tr><td>' + name + '</td>' + ''.join('<td>' + f'{m[k]:.4%}' + '</td>' for k in
             (("total_return", "annualized_return", "max_drawdown") if name.startswith("逐笔") else
              ("net_total_return", "annualized_return", "max_drawdown"))) + '</tr>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>现有算法对照</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;color:#203737}td,th{padding:14px;border-bottom:1px solid #ddd;text-align:right}pre{white-space:pre-wrap;overflow-wrap:anywhere}aside{padding:18px;background:#fff5dd}</style><h1>与真实现有算法的差异对照</h1>'
    page += '<p>同标的 SPY、同冻结数据、2015-01-02 开盘至 2025-12-31 开盘。正式主报告仍按收盘估值。</p>'
    page += '<aside>这里调用固定版本 finresearch-lab 的原始数值函数。原动量策略与 SMA20/50 的规则不同，所以结果差异不能全部归因于回测算法错误。</aside>'
    page += '<table><tr><th>方案</th><th>累计收益</th><th>年化收益</th><th>最大回撤</th></tr>' + ''.join(metrics_table) + '</table>'
    page += '<h2>口径差异</h2><ul>' + ''.join('<li>'+html.escape(x)+'</li>' for x in report['limitations']) + '</ul>'
    page += '<h2>原算法买入持有独立复核</h2><pre>'+html.escape(json.dumps(report['independent_market_check'],indent=2))+'</pre>'
    page += '<p>完整原始版本、逐日输出及输入哈希见 <a href="comparison.json">comparison.json</a>。</p></html>'
    (args.output / "report.html").write_text(page, encoding="utf-8")
    write_new(args.output / "integrity.json", {"files": {p.name: sha(p.read_bytes()) for p in args.output.iterdir()}})
    print(json.dumps({"status":"succeeded","source_commit":provenance["commit"],"independent_check":report["independent_market_check"]}))


if __name__ == "__main__": main()
