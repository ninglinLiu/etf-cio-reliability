"""One-time source download for the independent institutional replication."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etf_lab.artifacts import write_new

target = ROOT / "data/paired-spy-tlt.json"
if target.exists(): raise SystemExit("Frozen snapshot already exists; choose a new version explicitly")
yf.set_tz_cache_location(str(ROOT / ".cache/yfinance"))
assets = {}
for symbol in ("SPY", "TLT"):
    frame = yf.Ticker(symbol).history(start="2003-01-01", end="2014-01-01", interval="1d",
                                     auto_adjust=False, back_adjust=False, actions=True, repair=False,
                                     keepna=True, raise_errors=True, timeout=30)
    if frame.empty or frame.isna().any().any(): raise ValueError("Incomplete provider data")
    assets[symbol] = [{"date": str(index.date()), **{column: float(row[column]) for column in frame.columns}}
                      for index, row in frame.iterrows()]
if [r["date"] for r in assets["SPY"]] != [r["date"] for r in assets["TLT"]]:
    raise ValueError("Mismatched sessions; no silent intersection")
write_new(target, {"schema": "paired-institution-bars-v1", "source": "yfinance / Yahoo Finance",
                  "source_urls": {s: "https://finance.yahoo.com/quote/"+s+"/history/" for s in assets},
                  "observed_at": datetime.now(timezone.utc).isoformat(), "provider_version": yf.__version__,
                  "interval": "1d", "coverage": ["2003-01-01", "2013-12-31"],
                  "price_basis": "Raw returned OHLC plus Adj Close. Replication uses Adj Close total-return ratios; never adds cash dividends again.",
                  "data_vintage": "Current downloaded history, not point-in-time historical vintage", "assets": assets})
print(json.dumps({"status":"succeeded","rows_per_asset":{s:len(v) for s,v in assets.items()}}))
