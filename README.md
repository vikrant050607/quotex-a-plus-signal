# Quotex A+ Signal PWA

Mobile-first analytical dashboard. It does NOT place trades on Quotex.

## What it does
- 1-minute closed-candle analysis
- Separate 5-minute trend confirmation
- EMA 20/50, RSI, VWAP when usable / typical-price mean proxy for FX feeds without volume
- liquidity sweep, displacement, confirmation, support/resistance room
- CALL / PUT / WAIT
- A+ threshold 8/10
- local paper-trading journal
- optional Telegram notifications from the server

## Required environment variable
`TWELVE_DATA_API_KEY`

Optional:
`TELEGRAM_BOT_TOKEN`
`TELEGRAM_CHAT_ID`

## Local run
Python 3.10+:
1. `pip install -r requirements.txt`
2. Set `TWELVE_DATA_API_KEY`
3. `python app.py`
4. Open `http://127.0.0.1:5000`

## Android installation
After deploying over HTTPS, open the site in Chrome on Android and use the browser's Install/Add to Home Screen option. It behaves like an app.

## Important
The score is a filter, not a probability. No 95% guarantee is claimed. Test with paper/demo trades and measure real results before risking money.
