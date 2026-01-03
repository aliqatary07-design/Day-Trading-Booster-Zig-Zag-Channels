import os
import time
import json
import pandas as pd
import numpy as np
import requests
from tvDatafeed import TvDatafeed, Interval

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("CHAT_ID")

# TradingView Scanner API Endpoint for Egypt
SCANNER_URL = "https://scanner.tradingview.com/egypt/scan"

def send_telegram_message(message):
    """Sends a message to the defined Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Telegram credentials missing. Check environment variables.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"❌ Telegram send failed: {response.text}")
    except Exception as e:
        print(f"❌ Telegram Connection Error: {e}")

def get_egx_symbols_from_screener():
    """
    Scrapes the official TradingView Egypt Screener API to get ALL active stocks.
    Filters: Stock Type = Common/Preference, Status = Active.
    Sorts by: Volume (descending) to prioritize liquid stocks.
    """
    print("🌍 Scanning TradingView Egypt Screener for all active stocks...")
    
    # Payload replicates the actual TradingView Screener request
    payload = {
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "exchange", "operation": "equal", "right": "EGX"},
            {"left": "active_symbol", "operation": "equal", "right": true} 
        ],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}},
        "columns": ["name", "close", "volume", "recommendation_mark"],
        "sort": {"sortBy": "volume", "sortOrder": "desc"},
        "range": [0, 300]  # Get top 300 stocks (covers entire EGX)
    }

    try:
        response = requests.post(SCANNER_URL, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # Extract symbol names (The scanner returns 'EGX:SYMBOL', we just need 'SYMBOL')
        # The 'name' field in 'd' usually looks like "COMI" or "EGX:COMI"
        symbols = [row['d'][0] for row in data['data']]
        
        print(f"✅ Found {len(symbols)} active EGX stocks.")
        return symbols
    except Exception as e:
        print(f"❌ Error fetching screener data: {e}")
        # Fallback list in case scanner API fails temporarily
        return ['COMI', 'SWDY', 'ETEL', 'FWRY', 'HRHO']

def calculate_vwap(df):
    """Calculates daily resetting VWAP."""
    df = df.copy()
    df['date'] = df.index.date
    df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3
    df['tp_vol'] = df['typ_price'] * df['volume']
    
    cum_tp_vol = df.groupby('date')['tp_vol'].cumsum()
    cum_vol = df.groupby('date')['volume'].cumsum()
    return cum_tp_vol / cum_vol

def analyze_market():
    # 1. Get Dynamic Symbol List
    symbols = get_egx_symbols_from_screener()
    
    # 2. Initialize Datafeed
    print("🚀 Connecting to TradingView Datafeed...")
    tv = TvDatafeed(auto_login=False) 
    
    for symbol in symbols:
        try:
            # 3. Fetch Data (1 Hour Interval)
            # We use 'EGX' as exchange. Some symbols might need 'CASE' but EGX is standard on TV.
            data = tv.get_hist(symbol=symbol, exchange='EGX', interval=Interval.in_1_hour, n_bars=300)
            
            # Validation: Check if data is empty or insufficient
            if data is None or data.empty or len(data) < 200:
                # print(f"⚠️ Skipping {symbol}: Insufficient data") # Reduce noise
                continue

            # Standardize Columns
            data.columns = [col.split(':')[-1].lower() if ':' in col else col.lower() for col in data.columns]
            
            # 4. Indicators (Pure Math)
            data['ema50'] = data['close'].ewm(span=50, adjust=False).mean()
            data['ema200'] = data['close'].ewm(span=200, adjust=False).mean()
            data['vwap'] = calculate_vwap(data)
            
            # Pivot High/Low (Donchian 20-period, shifted 1 to avoid lookahead bias)
            data['pivot_high'] = data['high'].rolling(window=20).max().shift(1)
            data['pivot_low'] = data['low'].rolling(window=20).min().shift(1)
            
            # Volume Moving Average
            data['avg_vol'] = data['volume'].rolling(window=20).mean()

            # 5. Signal Logic
            curr = data.iloc[-1]
            
            close = curr['close']
            vol = curr['volume']
            
            # Skip if any indicator is NaN (e.g. recent IPOs)
            if pd.isna(curr['ema200']) or pd.isna(curr['pivot_high']):
                continue

            # BUY RULES
            buy_signal = (
                close > curr['ema50'] and
                close > curr['ema200'] and
                close > curr['vwap'] and
                close > curr['pivot_high'] and
                vol >= curr['avg_vol']
            )

            # SELL RULES
            sell_signal = (
                close < curr['ema50'] and
                close < curr['ema200'] and
                close < curr['vwap'] and
                close < curr['pivot_low'] and
                vol >= curr['avg_vol']
            )

            # 6. Telegram Alert
            if buy_signal:
                msg = (
                    f"📈 **STRONG BUY: {symbol}**\n"
                    f"Price: {close:.2f}\n"
                    f"Vol: {int(vol)} (Avg: {int(curr['avg_vol'])})\n"
                    f"Breakout: > {curr['pivot_high']:.2f}\n"
                    f"Trend: Above EMA200 & VWAP"
                )
                print(f"🔔 BUY SIGNAL: {symbol}")
                send_telegram_message(msg)

            elif sell_signal:
                msg = (
                    f"📉 **STRONG SELL: {symbol}**\n"
                    f"Price: {close:.2f}\n"
                    f"Vol: {int(vol)} (Avg: {int(curr['avg_vol'])})\n"
                    f"Breakdown: < {curr['pivot_low']:.2f}\n"
                    f"Trend: Below EMA200 & VWAP"
                )
                print(f"🔔 SELL SIGNAL: {symbol}")
                send_telegram_message(msg)
            
            # Rate limit protection (prevents TV ban)
            time.sleep(0.3)

        except Exception as e:
            # print(f"❌ Error on {symbol}: {e}")
            continue

if __name__ == "__main__":
    analyze_market()
