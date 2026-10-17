"""Predeclared Cambria 2000s replication attempt; never optimize to pass a threshold."""
import argparse
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etf_lab.artifacts import read_json, sha, write_new

SOURCE = "https://www.cambriainvestments.com/wp-content/uploads/2018/01/A-Quantitative-Approach-to-Tactical-Asset-Allocation.pdf"
REFERENCE = {"annualized_return": .0773, "max_drawdown": -.0682, "round_trip_count": None}


def replay(snapshot, risk_free):
    # Fixed rule from paper: total-return month-end series; 10-month SMA;
    # month-end signal changes exposure for the following month's return.
    month_ends = {}
    for row in snapshot["provider_rows"]:
        month_ends[row["date"][:7]] = row
    ordered = sorted(month_ends)
    if sorted(month_ends) != [f"{year}-{month:02d}" for year in range(1998, 2010) for month in range(1, 13)]:
        raise ValueError("Incomplete monthly panel")
    values = [month_ends[m]["Adj Close"] for m in ordered]
    signals, previous = [], 0
    for i, price in enumerate(values):
        if i < 9:
            signals.append(None)
            continue
        average = sum(values[i-9:i+1])/10
        previous = 1 if price > average else 0 if price < average else previous
        signals.append(previous)
    equity, peak, drawdown, trades, ledger = 1., 1., 0., [], []
    for i, month in enumerate(ordered):
        if not "2000-01" <= month <= "2009-12": continue
        held = signals[i-1]
        rf = risk_free["monthly_rf"][month.replace("-", "")]
        market = values[i] / values[i-1] - 1
        ret = market if held else rf
        equity *= 1 + ret
        peak = max(peak, equity)
        drawdown = min(drawdown, equity/peak - 1)
        if signals[i] != held:
            trades.append({"date": month_ends[month]["date"], "side": "BUY" if signals[i] else "SELL",
                           "signal_total_return_level": values[i], "exposure_next_month": signals[i]})
        ledger.append({"month": month, "held_during_month": held, "market_return": market,
                       "cash_return": rf, "portfolio_return": ret, "equity": equity,
                       "signal_after_month_end": signals[i]})
    assert len(ledger) == 120
    return {"metrics": {"annualized_return": equity ** .1 - 1, "max_drawdown": drawdown,
                         "total_return": equity-1, "round_trip_count": sum(t["side"] == "SELL" for t in trades)},
            "trade_count_convention": "Sell exits in interval, including closure of an inherited pre-2000 position; no terminal forced sale",
            "inherited_exposure": ledger[0]["held_during_month"], "orders": trades, "ledger": ledger}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--require-pass", action="store_true")
    args = p.parse_args()
    if args.output.exists(): raise SystemExit("Use a new output directory")
    sp = ROOT / "data/spy-cambria-proxy.json"
    rf = ROOT / "data/french-rf-cambria-proxy.json"
    result = replay(read_json(sp), read_json(rf))
    comparisons = []
    for metric, reference in REFERENCE.items():
        calculated = result["metrics"][metric]
        deviation = abs(calculated-reference)/abs(reference) if reference not in (None, 0) else None
        comparisons.append({"metric": metric, "reference": reference, "reproduced_proxy": calculated,
                            "relative_deviation": deviation,
                            "within_ten_percent": deviation <= .1 if deviation is not None else None})
    report = {"status": "NOT_ACCEPTED", "article": {"url": SOURCE, "author": "Mebane T. Faber / Cambria Investment Management",
              "title": "A Quantitative Approach to Tactical Asset Allocation", "version": "February 2013 update",
              "page": 67, "table": "Appendix B, 2000s, TIMING column"},
              "rule": "Month-end total-return price > 10-month SMA; otherwise Treasury bills; no fees/taxes/slippage",
              "period": "2000-01 through 2009-12", "reference": REFERENCE, "comparisons": comparisons,
              "input_hashes": {sp.name: sha(sp.read_bytes()), rf.name: sha(rf.read_bytes())},
              "script_sha256": sha(Path(__file__).read_bytes()),
              "reproduction": result,
              "reasons": ["The article does not disclose an exact trade count for the chosen decade; no numeric deviation can be asserted.",
                          "Original input: GFD S&P 500 total-return index and 90-day T-bill series; available proxy: SPY adjusted close and French one-month RF.",
                          "This is a separately specified article replication attempt, not the SPY20/50 sample.",
                          "Same-close monthly execution is an article assumption, not a claim of implementable fills; current-vintage data do not establish point-in-time availability."]}
    if any(row["within_ten_percent"] is False for row in comparisons):
        report["reasons"].insert(0, "At least one disclosed numerical metric exceeds the 10% relative deviation threshold.")
    write_new(args.output / "replication.json", report)
    table = ''.join('<tr><td>'+r['metric']+'</td><td>'+str(r['reference'])+'</td><td>'+str(round(r['reproduced_proxy'],6))+
                    '</td><td>'+('未披露，不能验收' if r['relative_deviation'] is None else f"{r['relative_deviation']:.2%}")+'</td></tr>' for r in comparisons)
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>机构论文复现核查</title><style>body{font:16px system-ui;max-width:1050px;margin:40px auto;color:#263938}td,th{padding:16px;border-bottom:1px solid #ddd}aside{background:#fff0d8;padding:20px}li{margin:12px 0}</style><h1>Cambria 机构论文 · 复现核查</h1><aside><strong>未通过完整验收</strong>：缺精确交易次数基准，数据采用明确标注的替代序列；不能写成“偏差≤10%全部通过”。</aside>'
    page += '<p>论文 2013 年版，第 67 页 Appendix B，2000s TIMING；2000-01 至 2009-12，10 月均线，月末信号，下个月收益。</p>'
    page += '<table><tr><th>指标（小数）</th><th>论文</th><th>替代数据重放</th><th>相对偏差</th></tr>'+table+'</table><ul>'+''.join('<li>'+html.escape(reason)+'</li>' for reason in report['reasons'])+'</ul>'
    page += '<p><a href="'+SOURCE+'">机构原文 PDF</a> · <a href="replication.json">逐月账本、每次转换和全部输入哈希</a></p></html>'
    (args.output / "report.html").write_text(page, encoding="utf-8")
    write_new(args.output / "integrity.json", {"files": {f.name: sha(f.read_bytes()) for f in args.output.iterdir()}})
    print(json.dumps({"status": report["status"], "comparisons": comparisons}, indent=2))
    return 2 if args.require_pass else 0


if __name__ == "__main__": sys.exit(main())
