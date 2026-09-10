import os, time, threading
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template, request
import requests

app = Flask(__name__)
API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
last_telegram_key = None

PAIR_MAP = {
    "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
    "AUDUSD": "AUD/USD", "USDCAD": "USD/CAD", "USDCHF": "USD/CHF",
    "NZDUSD": "NZD/USD"
}

def ema(vals, n):
    if len(vals) < n: return None
    k = 2/(n+1)
    e = sum(vals[:n])/n
    for x in vals[n:]:
        e = x*k + e*(1-k)
    return e

def rsi(vals, n=14):
    if len(vals) <= n: return None
    gains=[]; losses=[]
    for i in range(1, len(vals)):
        d=vals[i]-vals[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains[:n])/n; al=sum(losses[:n])/n
    for i in range(n, len(gains)):
        ag=(ag*(n-1)+gains[i])/n
        al=(al*(n-1)+losses[i])/n
    if al == 0: return 100.0
    return 100 - (100/(1+ag/al))

def atr(rows, n=14):
    if len(rows) <= n: return None
    trs=[]
    for i in range(1,len(rows)):
        h,l,pc=rows[i]["high"],rows[i]["low"],rows[i-1]["close"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs[-n:])/n

def fetch(symbol, interval, outputsize=120):
    if not API_KEY:
        raise RuntimeError("TWELVE_DATA_API_KEY is not configured.")
    url="https://api.twelvedata.com/time_series"
    r=requests.get(url, params={
        "symbol":symbol, "interval":interval, "outputsize":outputsize,
        "apikey":API_KEY, "format":"JSON", "order":"ASC"
    }, timeout=15)
    data=r.json()
    if "values" not in data:
        raise RuntimeError(data.get("message","Market-data API error"))
    out=[]
    for x in data["values"]:
        try:
            out.append({"datetime":x["datetime"],"open":float(x["open"]),
                        "high":float(x["high"]),"low":float(x["low"]),
                        "close":float(x["close"]),
                        "volume":float(x.get("volume") or 0)})
        except Exception:
            pass
    return out

def vwap_or_mean(rows):
    # FX feeds often have no usable volume. In that case use a clearly-labeled
    # typical-price mean proxy rather than pretending it is true exchange VWAP.
    if any(x["volume"] > 0 for x in rows[-40:]):
        pv=sum(((x["high"]+x["low"]+x["close"])/3)*x["volume"] for x in rows[-40:])
        vv=sum(x["volume"] for x in rows[-40:])
        if vv: return pv/vv, "VWAP"
    return sum((x["high"]+x["low"]+x["close"])/3 for x in rows[-20:])/20, "Mean proxy"

def analyze(pair):
    symbol=PAIR_MAP.get(pair, pair)
    m1=fetch(symbol,"1min",140)
    m5=fetch(symbol,"5min",80)
    if len(m1)<60 or len(m5)<30:
        raise RuntimeError("Not enough candles returned.")

    # Only closed candles are analyzed: last row is treated as the current/incomplete candle.
    c=m1[:-1]
    t=m5[:-1]
    last=c[-1]; prev=c[-2]
    closes=[x["close"] for x in c]
    e20=ema(closes,20); e50=ema(closes,50)
    e20_5=ema([x["close"] for x in t],20); e50_5=ema([x["close"] for x in t],50)
    last5=t[-1]["close"]
    rr=rsi(closes,14); aa=atr(c,14)
    vp, vname=vwap_or_mean(c)

    prior5=c[-6:-1]
    prior_low=min(x["low"] for x in prior5); prior_high=max(x["high"] for x in prior5)
    bull_sweep=last["low"] < prior_low and last["close"] > prior_low
    bear_sweep=last["high"] > prior_high and last["close"] < prior_high

    rng=max(last["high"]-last["low"],1e-12)
    body=abs(last["close"]-last["open"])
    displacement=body/rng >= .60 and (aa is None or body >= .50*aa)
    bull_candle=last["close"]>last["open"] and displacement
    bear_candle=last["close"]<last["open"] and displacement
    bull_confirm=last["close"] > prev["high"]
    bear_confirm=last["close"] < prev["low"]

    support=min(x["low"] for x in c[-10:])
    resistance=max(x["high"] for x in c[-10:])
    room_call=aa is None or resistance-last["close"] > .30*aa
    room_put=aa is None or last["close"]-support > .30*aa

    bull5=e20_5 is not None and e50_5 is not None and e20_5>e50_5 and last5>e20_5
    bear5=e20_5 is not None and e50_5 is not None and e20_5<e50_5 and last5<e20_5
    bull1=e20 is not None and e50 is not None and last["close"]>e20>e50
    bear1=e20 is not None and e50 is not None and last["close"]<e20<e50
    above=last["close"]>vp
    below=last["close"]<vp
    rsi_ok_call=rr is not None and 50<rr<75
    rsi_ok_put=rr is not None and 25<rr<50

    call_score=(2 if bull5 else 0)+(2 if bull1 else 0)+(1 if above else 0)+(1 if bull_sweep else 0)+(1 if bull_candle else 0)+(1 if bull_confirm else 0)+(1 if room_call else 0)
    put_score=(2 if bear5 else 0)+(2 if bear1 else 0)+(1 if below else 0)+(1 if bear_sweep else 0)+(1 if bear_candle else 0)+(1 if bear_confirm else 0)+(1 if room_put else 0)

    # Fresh trigger requirement: avoid repeatedly printing the same directional state.
    call_trigger=bull_sweep or bull_confirm
    put_trigger=bear_sweep or bear_confirm

    if call_score>=8 and bull5 and bull1 and rsi_ok_call and call_trigger:
        signal="CALL"
        score=call_score
        reason="5M bullish trend + 1M alignment + fresh sweep/confirmation"
    elif put_score>=8 and bear5 and bear1 and rsi_ok_put and put_trigger:
        signal="PUT"
        score=put_score
        reason="5M bearish trend + 1M alignment + fresh sweep/confirmation"
    else:
        signal="WAIT"
        score=max(call_score,put_score)
        reason="No fresh A+ setup; wait for sweep + confirmation"

    return {
        "pair":pair, "symbol":symbol, "signal":signal, "score":score,
        "timeframe":"1M", "confirmation":"5M", "expiry":"1 minute",
        "candle_time":last["datetime"], "price":last["close"],
        "rsi":rr, "ema20":e20, "ema50":e50, "ema20_5m":e20_5, "ema50_5m":e50_5,
        "filter":vname, "filter_value":vp, "reason":reason,
        "levels":{"support":support,"resistance":resistance},
        "checks":{
            "5m_bull":bull5,"5m_bear":bear5,"1m_bull":bull1,"1m_bear":bear1,
            "sweep_call":bull_sweep,"sweep_put":bear_sweep,
            "confirmation_call":bull_confirm,"confirmation_put":bear_confirm,
            "room_call":room_call,"room_put":room_put
        },
        "generated_at":datetime.now(timezone.utc).isoformat()
    }

def telegram_send(text):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID): return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                      data={"chat_id":TELEGRAM_CHAT_ID,"text":text}, timeout=10)
    except Exception:
        pass

@app.get("/")
def index(): return render_template("index.html")

@app.get("/api/signal")
def api_signal():
    pair=request.args.get("pair","EURUSD").upper()
    try:
        result=analyze(pair)
        global last_telegram_key
        key=f'{pair}:{result["candle_time"]}:{result["signal"]}'
        if result["signal"] in ("CALL","PUT") and key != last_telegram_key:
            last_telegram_key=key
            telegram_send(f'Quotex A+ {result["signal"]}\nPair: {pair}\n1M | 5M confirmation\nScore: {result["score"]}/10\nPrice: {result["price"]}\nExpiry: 1 minute\nEducational/paper-trading signal — not guaranteed.')
        return jsonify(result)
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.get("/health")
def health(): return jsonify({"ok":True})

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","5000")))
