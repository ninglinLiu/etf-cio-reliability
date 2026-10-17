"""Glenn paired switching: frozen published parameters, all three required metrics."""
import argparse
from datetime import date
import html
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etf_lab.artifacts import read_json, sha, write_new

ARTICLE = "https://alphaarchitect.com/are-uber-simple-asset-allocation-systems-robust/"
PAPER = "https://www.ifta.org/assets/docs/ifta_journal_2021.pdf"


def replay(snapshot, config):
    symbols = config["symbols"]
    assets = snapshot["assets"]
    dates = [r["date"] for r in assets[symbols[0]]]
    if dates != sorted(set(dates)): raise ValueError("Duplicate or unordered dates")
    if any([r["date"] for r in assets[s]] != dates for s in symbols):
        raise ValueError("Missing or mismatched sessions")
    prices = {s: [r["Adj Close"] for r in assets[s]] for s in symbols}
    if any(not math.isfinite(p) or p <= 0 for ps in prices.values() for p in ps):
        raise ValueError("Invalid adjusted close")
    month_ends = {d[:7]: i for i, d in enumerate(dates)}
    months = sorted(month_ends)
    end_indices = set(month_ends.values())
    selected = [i for i, d in enumerate(dates) if config["start"] <= d <= config["end"]]
    if not selected or dates[selected[0]] != config["start"] or dates[selected[-1]] != config["end"]:
        raise ValueError("Evaluation endpoints unavailable")
    cash, units, held = float(config["initial_cash"]), 0., None
    ledger, trades = [], []

    def choose(index):
        month_index = months.index(dates[index][:7])
        old = month_ends[months[month_index-config["lookback_months"]]]
        if month_index < config["lookback_months"]: raise ValueError("Insufficient warm-up")
        ratios = {s: prices[s][index]/prices[s][old] for s in symbols}
        return ("TLT" if ratios["TLT"] > ratios["SPY"] else "SPY"), ratios

    for i in selected:
        target, signal = held, None
        if i == selected[0]:
            previous_end = max(j for j in end_indices if j < i)
            target, signal = choose(previous_end)
        elif i in end_indices:
            target, signal = choose(i)
        if target != held:
            if held is not None:
                value = units * prices[held][i]
                cash += value-config["fee_fixed_per_order"]
                trades.append({"date": dates[i], "side": "SELL", "symbol": held, "adjusted_units": units,
                               "adjusted_price": prices[held][i], "notional": value,
                               "fee": config["fee_fixed_per_order"], "cash_after": cash})
                units = 0.
            units = (cash-config["fee_fixed_per_order"])/prices[target][i]
            value = units*prices[target][i]
            cash -= value + config["fee_fixed_per_order"]
            trades.append({"date": dates[i], "side": "BUY", "symbol": target, "adjusted_units": units,
                           "adjusted_price": prices[target][i], "notional": value,
                           "fee": config["fee_fixed_per_order"], "cash_after": cash})
            held = target
        if cash < -1e-7 or units < 0: raise ArithmeticError("Negative cash or units")
        equity = cash + units*prices[held][i]
        ledger.append({"date": dates[i], "holding": held, "adjusted_units": units, "cash": cash,
                       "equity": equity, "review_ratios": signal})
    peak, mdd = config["initial_cash"], 0.
    for row in ledger:
        peak = max(peak, row["equity"])
        mdd = min(mdd, row["equity"]/peak-1)
    gain = ledger[-1]["equity"]/config["initial_cash"]
    return {"metrics": {"total_return": gain-1, "annualized_return": gain**(config["annualization"]/len(ledger))-1,
                         "max_drawdown": mdd, "order_count": len(trades)}, "observations": len(ledger),
            "orders": trades, "daily": ledger, "total_fees": sum(t["fee"] for t in trades)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise SystemExit("Choose a new output directory")
    data = ROOT / "data/paired-spy-tlt.json"
    config_path = ROOT / "config/paired-replication.json"
    config = read_json(config_path)
    result = replay(read_json(data), config)
    comparisons = [{"metric": key, "reference": value, "reproduced": result["metrics"][key],
                    "relative_deviation": abs(result["metrics"][key]-value)/abs(value)}
                   for key, value in config["reference"].items()]
    for row in comparisons: row["passed"] = row["relative_deviation"] <= config["relative_tolerance"]
    passed = all(row["passed"] for row in comparisons)
    report = {"status": "passed" if passed else "failed", "reference_article": ARTICLE,
              "primary_paper": {"url": PAPER, "title": "Simple and Effective Market Timing With Tactical Asset Allocation",
                                "author": "Lewis A. Glenn", "publication": "IFTA Journal 2021",
                                "printed_page": 95, "pdf_page": 97, "table": "Table 1, k=1 month, mu=3 months"},
              "source_selection": "Alpha Architect's strategy research article discusses Glenn's paper; precise numbers are anchored to one row of the author's IFTA publication, not mixed with another backtest.",
              "input_hashes": {data.name: sha(data.read_bytes()), config_path.name: sha(config_path.read_bytes())},
              "script_sha256": sha(Path(__file__).read_bytes()), "config": config,
              "comparisons": comparisons, "result": result,
              "limits": ["This reproduces the SPY/TLT article model separately from the audited SPY20/50 sample.",
                         "Article execution at signal close is replicated; it is not the next-open execution used by the main sample.",
                         "Trade count means each BUY or SELL: one switch is two orders. No final artificial sale.",
                         "Paper states nominal transaction costs but no amount; fixed $1 per order was declared before replay.",
                         "Current-vintage Yahoo adjusted prices can differ from the author's historical inputs; distributions are embedded once, with no additional dividend cash.",
                         "The paper abstract's 30% average annual return is not CAGR. Table 1 gives 14.6% CAGR; the same table gives 18.9% drawdown whereas body text rounds to 18.8%."]}
    write_new(args.output / "replication.json", report)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 3.5), dpi=140)
    ax.plot([date.fromisoformat(d["date"]) for d in result["daily"]],
            [d["equity"]/config["initial_cash"] for d in result["daily"]], color="#087d78")
    ax.set(ylabel="Equity / initial capital", title="Published SPY/TLT paired-switching replication")
    ax.grid(alpha=.15); fig.tight_layout(); fig.savefig(args.output/"nav.png"); plt.close(fig)
    rows = ''.join('<tr><td>'+r['metric']+'</td><td>'+f"{r['reference']:.6f}"+'</td><td>'+f"{r['reproduced']:.6f}"+'</td><td>'+f"{r['relative_deviation']:.2%}"+'</td><td>'+str(r['passed'])+'</td></tr>' for r in comparisons)
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>机构三指标复现</title><style>body{font:16px system-ui;max-width:1080px;margin:40px auto;color:#203737}td,th{padding:14px;border-bottom:1px solid #ddd}li{margin:12px 0}aside{padding:20px;background:#e1f3e9}img{width:100%}</style><h1>机构文章对标 · SPY / TLT 配对轮动</h1><aside><b>'+('四项偏差均 ≤10%（含必需的收益、回撤、交易次数）' if passed else '未通过')+'</b><p>2004-01-02 — 2013-12-31 · 固定三个月动量 · 月末切换 · 本次没有参数搜索</p></aside>'
    page += '<p>机构报道：<a href="'+ARTICLE+'">Alpha Architect</a>；精确数值来源：<a href="'+PAPER+'">Glenn 原文，IFTA 2021，第 95 页表 1 第一行</a>。收益与回撤在表中以小数显示。</p>'
    page += '<table><tr><th>指标</th><th>原文精确值</th><th>本地复现</th><th>相对偏差</th><th>通过</th></tr>'+rows+'</table><img src="nav.png" alt="机构策略复现净值">'
    page += '<h2>口径与限制</h2><ul>'+''.join('<li>'+html.escape(x)+'</li>' for x in report['limits'])+'</ul><p><a href="replication.json">全部逐日账本、订单、原文定位、冻结配置和数据哈希</a></p></html>'
    (args.output / "report.html").write_text(page,encoding="utf-8")
    write_new(args.output / "integrity.json", {"files": {p.name: sha(p.read_bytes()) for p in args.output.iterdir()}})
    print(json.dumps({"status": report["status"], "observations": result["observations"], "comparisons": comparisons}, indent=2))
    return 0 if passed else 2


if __name__ == "__main__": sys.exit(main())
