from __future__ import annotations

import base64
from datetime import date
from html import escape
import json
from pathlib import Path

from .engine import Config


def table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    def cell(v):
        if v is None: return "—"
        if isinstance(v, float): return f"{v:,.6f}"
        return escape(str(v))
    return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{escape(label)}</th>' for key, label in columns) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{cell(row.get(key))}</td>' for key, label in columns) + '</tr>' for row in rows) + '</tbody></table></div>'


def chart(path: Path, result: dict, cfg: Config):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    dates = [date.fromisoformat(r["date"]) for r in result["daily"]]
    values = [r["equity"] / cfg.initial_cash for r in result["daily"]]
    comparator = [r["equity"] / cfg.initial_cash for r in result["simplified_reference"]["daily"]]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, ax = plt.subplots(figsize=(12, 4.5), dpi=150)
    fig.patch.set_facecolor("#ffffff")
    ax.plot(dates, values, color="#087d78", linewidth=1.7, label="Audited cash/share ledger, net of costs")
    ax.plot(dates, comparator, color="#b3905b", linewidth=1.2, alpha=.85, label="New simplified reference (not original system)")
    ax.set_ylabel("Equity / initial capital")
    ax.grid(axis="y", alpha=.15)
    ax.legend(frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, metadata={"Software": "etf-cio-reliability"})
    plt.close(fig)


def render(directory: Path, result: dict, cfg: Config, snapshot: dict, receipt: dict):
    chart(directory / "nav.png", result, cfg)
    image = base64.b64encode((directory / "nav.png").read_bytes()).decode()
    m, baseline = result["metrics"], result["simplified_reference"]["metrics"]
    def pct(x): return "—" if x is None else f"{x:.2%}"
    def number(x): return "—" if x is None else f"{x:.2f}"
    definitions = [("total_return", "累计收益", pct), ("annualized_return", "年化收益", pct),
                   ("max_drawdown", "最大回撤", pct), ("sharpe", "夏普比率", number),
                   ("win_rate", "已平仓胜率", pct), ("fill_count", "成交笔数", str), ("round_trip_count", "完整交易次数", str)]
    cards = ''.join(f'<div class="card"><span>{label}</span><strong>{formatter(m[key])}</strong></div>' for key, label, formatter in definitions)
    comparisons = [{"metric": label, "ledger": formatter(m[key]), "reference": "—" if baseline[key] is None else formatter(baseline[key]),
                    "delta": "—" if m[key] is None or baseline[key] is None else (f'{(m[key]-baseline[key])*100:.4f} pp' if key in ("total_return", "annualized_return", "max_drawdown", "win_rate") else number(m[key]-baseline[key]))} for key, label, formatter in definitions]
    trades = table(result["trades"], [("date","成交日"),("signal_date","信号日"),("side","方向"),("reference_open","原始开盘"),("price","成交价"),("quantity","股数"),("fee","手续费 $"),("slippage_cost","滑点 $"),("impact_cost","冲击 $"),("realized_pnl","闭合交易盈亏 $"),("cash_after","成交后现金 $")])
    daily = table(result["daily"], [("date","日期"),("close","收盘价"),("fast","快线"),("slow","慢线"),("signal_after_close","收盘后信号"),("dividend_cash","分红现金"),("trade_cash","交易现金流"),("cash","现金"),("shares","股数"),("market_value","市值"),("equity","净值 $"),("cash_reconciliation_residual","现金核对差额")])
    events = table(result["corporate_actions"], [("date","日期"),("kind","事件"),("eligible_shares","分红股数"),("per_share","每股股息"),("cash_change","现金变动"),("ratio","拆股比率")])
    cost = result["cost_totals"]
    synthetic = '<span class="badge warning">合成测试数据 · 非市场业绩</span>' if snapshot.get("synthetic") else '<span class="badge">真实历史日线 · 冻结快照</span>'
    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SPY 回测审计报告</title>
<style>*{{box-sizing:border-box}}body{{margin:0;color:#203737;background:#eff4f2;font:15px/1.65 "Microsoft YaHei",system-ui,sans-serif}}main{{max-width:1280px;margin:auto;padding:44px 32px 80px}}header{{margin-bottom:28px}}.eyebrow{{font-size:12px;letter-spacing:2px;color:#567570}}h1{{font-size:36px;line-height:1.2;margin:12px 0}}h2{{font-size:22px;margin:0 0 14px}}p{{margin:10px 0}}.muted{{color:#687b76}}.badge{{display:inline-block;font-size:12px;border-radius:4px;background:#d6eeE5;color:#185f4d;padding:4px 10px}}.warning{{background:#f6e7c7;color:#79541f}}.note{{border-left:4px solid #b68e50;padding:14px 18px;background:#fff9ed;margin:24px 0}}section{{background:white;border:1px solid #dce5e1;border-radius:9px;padding:26px;margin:22px 0}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card{{background:#fff;padding:18px;border:1px solid #dce5e1;border-radius:8px}}.card span{{display:block;color:#688078;font-size:12px}}.card strong{{display:block;font-size:25px;margin-top:8px;font-weight:600}}img{{width:100%;height:auto}}.scroll{{overflow:auto;max-height:550px}}table{{border-collapse:collapse;width:100%;font-size:12px;font-variant-numeric:tabular-nums}}th,td{{border-bottom:1px solid #e8edeb;padding:10px 12px;text-align:right;white-space:nowrap}}th{{position:sticky;top:0;background:#eaf2ef;color:#426358}}th:first-child,td:first-child{{text-align:left}}tr:nth-child(even){{background:#fafcfb}}pre{{padding:16px;background:#f1f5f3;overflow:auto;white-space:pre-wrap;word-break:break-word;font:12px/1.7 Consolas,monospace}}summary{{cursor:pointer;font-weight:600;padding:6px 0}}a{{color:#087d78}}@media(max-width:700px){{main{{padding:24px 14px}}.cards{{grid-template-columns:repeat(2,1fr)}}h1{{font-size:28px}}section{{padding:18px}}}}@media print{{body{{background:white}}main{{padding:0}}section{{break-inside:avoid}}.scroll{{max-height:none}}}}</style></head><body><main>
<header><div class="eyebrow">ETF RESEARCH / AUDITABLE BACKTEST</div><h1>SPY 均线择时 · 回测审计报告</h1><p class="muted">{escape(cfg.start)} — {escape(cfg.end)} · SMA {cfg.fast}/{cfg.slow} · 初始资金 ${cfg.initial_cash:,.0f}</p>{synthetic}</header>
<div class="note"><strong>可逐笔复核的 ETF 样板；任务二采用用户确认的独立研究可靠性验收范围。</strong><br>真实现有算法对照、机构论文重放与三场景证据在配套交付目录。本页曲线中的简化参考为本地诊断；没有接入任何现有 CIO。</div>
<div class="cards">{cards}</div>
<section><h2>净值曲线</h2><p class="muted">净值由现金与持仓逐日重建。末期未平仓按收盘估值，不虚构清仓。</p><img alt="SPY 逐笔账本与本地简化参考净值曲线" src="data:image/png;base64,{image}"></section>
<section><h2>规则与成交时序</h2><p>收盘后快均线上穿慢均线发出买入，反向交叉发出卖出，下一交易日开盘成交；初始为空仓。</p><pre>after close[t]:
  if fast[t-1] &lt;= slow[t-1] and fast[t] &gt; slow[t]: BUY
  if fast[t-1] &gt;= slow[t-1] and fast[t] &lt; slow[t]: SELL
at open[t+1]:
  credit prior-holder dividend under declared ex-date cash assumption
  BUY: largest integer quantity affordable after all costs
  SELL: liquidate held shares; include entry/exit costs and dividends in PnL
at close[t+1]: equity = cash + shares × unadjusted close</pre><p>样本前数据仅用于预热；不会为样本前已经发生的交叉追补持仓。最后一个收盘信号没有未来成交日时不执行。均线使用当时已知拆股调整、未作股息调整的价格。</p></section>
<section><h2>成本、现金与盈亏勾稽</h2><p>单边滑点 {cfg.slippage_bps:g} bp；每笔固定手续费 ${cfg.fee_fixed:g}，另按成交额 {cfg.fee_bps:g} bp。订单原始开盘名义金额超过前 {cfg.adv_window} 个交易日平均日成交额的 {cfg.impact_threshold:.2%} 时，额外冲击 {cfg.impact_bps:g} bp。此阈值是流动性参与率，非组合仓位占比；是假设模型，不是实测冲击。</p><p>合计手续费 <strong>${cost['fee']:,.2f}</strong> · 滑点 <strong>${cost['slippage_cost']:,.2f}</strong> · 冲击 <strong>${cost['impact_cost']:,.2f}</strong> · 分红 <strong>${result['dividend_income']:,.2f}</strong></p><p>期末净值 ${m['ending_equity']:,.2f} − 本金 ${cfg.initial_cash:,.2f} = 已平仓盈亏 ${result['realized_pnl']:,.2f} + 未平仓周期盈亏 ${result['open_position_pnl']:,.2f}。勾稽差额：{result['pnl_reconciliation_residual']:.10f}。</p><p class="muted">滑点与冲击已进入成交价，不再重复扣费。未平仓周期盈亏含该周期已收到分红；胜率只统计已完成买卖周期。年化使用 {cfg.annualization} 个交易日，夏普采用样本标准差，年化无风险利率 {cfg.risk_free_annual:.2%}；无交易或零波动指标显示“—”。现金利息为零，未计税。</p></section>
<section><h2>与新建简化参考的差异</h2>{table(comparisons,[("metric","指标"),("ledger","逐笔账本"),("reference","本地简化参考"),("delta","账本 − 参考")])}<p>同样本、同 SPY、同交叉信号。参考算法将前一收盘信号直接乘下一段收盘到收盘收益，包含建仓前的隔夜变化；还假设零成本、分数份额与分红即时再投资。差异共同体现成交时点、成本及资金约束，不能全部归因于单一因素。</p></section>
<section><h2>逐笔成交 · {len(result['trades'])} 笔</h2><p class="muted">买入尚无闭合交易盈亏；卖出盈亏包含买入成本、卖出费用及持仓期间分红。JSON 保留完整精度，表格显示六位小数。</p>{trades}</section>
<section><h2>逐日账本 · {len(result['daily'])} 个交易日</h2><details><summary>展开信号、现金、持仓、净值与现金勾稽</summary>{daily}</details></section>
<section><h2>公司行动与拒单</h2><details><summary>展开分红 / 拆股事件（{len(result['corporate_actions'])}）</summary>{events}</details><p>现金不足拒单：{len(result['rejected_orders'])} 笔。</p><pre>{escape(json.dumps(result['rejected_orders'],ensure_ascii=False,indent=2))}</pre></section>
<section><h2>数据与可追溯信息</h2><p>来源：{escape(snapshot['source'])} · 精度：日线 · 抓取：{escape(snapshot['observed_at'])} · 数据截止：{escape(snapshot['data_cutoff'])}。</p><p>{escape(snapshot['price_basis'])}</p><p><strong>分红简化：</strong>数据没有实际支付日，本实验明确假设除息日现金到账，可能提前增加可投资现金。没有把 Adj Close 收益与分红现金重复计入。历史价格为当前下载版本，不能证明历史当时可见的数据版本。</p><p>输入标识：<code>{escape(receipt['input_id'])}</code><br>运行标识：<code>{escape(receipt['run_id'])}</code></p><p>旁附 input.json、snapshot.json、result.json、receipt.json、integrity.json 供离线复算与校验。</p><pre>{escape(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2))}</pre></section>
<section><h2>验收状态与边界</h2><p>五类金融边界、实际算法对照、机构重放及研究交接测试见交付目录；本次运行成功仍须经当前案例提交和读取校验。</p><ul><li>现有算法：使用固定版本 finresearch-lab，明确标注与 SMA20/50 不同的策略和费用口径。</li><li>机构重放：Glenn SPY/TLT 原文三项精确数字及偏差见配套报告；与本页 SMA20/50 分开运行。Cambria 因缺交易次数，保留为未通过的候选。</li><li>任务二：独立研究结果契约；无原 CIO 集成声明。</li><li>ETF 样板不证明美股选股回测可信；历史成分股、退市与财报历史版本问题未解决。</li><li>本策略不调用 LLM。若以后加入历史 LLM 决策，需单独处理模型未来知识污染。</li><li>仅研究实验，无真实持仓、下单、CRO 或审批写入。</li></ul><p class="muted">数据接口口径参见 <a href="https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html">yfinance 官方文档</a>。行情版权与使用范围由原数据提供方规定。</p></section>
</main></body></html>'''
    (directory / "report.html").write_text(html, encoding="utf-8")
