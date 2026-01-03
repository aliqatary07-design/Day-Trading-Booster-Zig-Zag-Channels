import os
import yfinance as yf
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta

# --- CONFIGURATION ---
# EGX Symbols to Monitor (Add more as needed)
SYMBOLS = [
    "COMI.CA",  # CIB
    "HRHO.CA",  # EFG Hermes
    "ETEL.CA",  # Telecom Egypt
    "AMOC.CA",  # AMOC
    "ESRS.CA",  # Ezz Steel
    "SWDY.CA",  # Elsewedy Electric
    "AREH.CA",  # Arpeggio
    "RMSK.CA",  # Rameda
    "FWRY.CA",  # Fawry
    "EKHO.CA",  # Egypt Kuwait Holding
]

# Telegram Secrets (Loaded from Environment Variables)
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def send_telegram_message(message):
    """Sends a formatted message to the configured Telegram chat."""
    if not BOT_TOKEN or not CHAT_ID:
        print("Error: BOT_TOKEN or CHAT_ID not found in environment variables.")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        print("✅ Telegram alert sent successfully.")
    except Exception as e:
        print(f"❌ Failed to send Telegram alert: {e}")

def calculate_vwap(df):
    """Calculates Volume Weighted Average Price (VWAP) resetting daily."""
    # We ensure the index is datetime to group by date
    df['Date'] = df.index.date
    
    # Calculate Typical Price
    df['Typical_Price'] = (df['High'] + df['Low'] + df['Close']) / 3
    df['TP_Volume'] = df['Typical_Price'] * df['Volume']
    
    # Cumulative Sums grouped by Date (resets every day)
    # Using transform to keep the original index structure
    cum_tp_vol = df.groupby('Date')['TP_Volume'].cumsum()
    cum_vol = df.groupby('Date')['Volume'].cumsum()
    
    df['VWAP'] = cum_tp_vol / cum_vol
    return df['VWAP']

def process_symbol(symbol):
    print(f"🔄 Analyzing {symbol}...")
    
    # 1. Fetch Data (1 Hour Interval, last 60 days to ensure enough data for EMA200)
    try:
        df = yf.download(symbol, period="60d", interval="1h", progress=False)
        if df.empty or len(df) < 200:
            print(f"⚠️ Not enough data for {symbol}")
            return
    except Exception as e:
        print(f"❌ Error fetching {symbol}: {e}")
        return

    # 2. Indicator Calculation (Pure Math)
    # EMA 50 & 200
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
    
    # VWAP (Intraday)
    df['VWAP'] = calculate_vwap(df)
    
    # Pivot High / Pivot Low (Rolling 20-period)
    df['Pivot_High_20'] = df['High'].rolling(window=20).max()
    df['Pivot_Low_20'] = df['Low'].rolling(window=20).min()
    
    # Average Volume (20-period)
    df['Avg_Vol_20'] = df['Volume'].rolling(window=20).mean()

    # 3. Get latest closed candle
    # We take the second to last row [-2] if the market is currently open and the last candle is forming.
    # However, for hourly scripts, usually the last completed row is desired. 
    # yfinance '1h' often includes the current partial candle. 
    # We will use the last available row [-1] but ensure we treat it as the "current signal" check.
    current = df.iloc[-1]
    
    # Extract values for readability
    close = current['Close']
    ema50 = current['EMA_50']
    ema200 = current['EMA_200']
    vwap = current['VWAP']
    pivot_high = current['Pivot_High_20']
    pivot_low = current['Pivot_Low_20']
    volume = current['Volume']
    avg_vol = current['Avg_Vol_20']
    timestamp = df.index[-1].strftime('%Y-%m-%d %H:%M')

    # 4. Signal Logic (Strict)
    
    # BUY CONDITIONS
    # 1. Close > EMA(50)
    # 2. Close > EMA(200)
    # 3. Close > VWAP
    # 4. Close > Pivot High (20-period)  -> Technically a breakout
    # 5. Volume >= 20-period average
    
    buy_cond = (
        (close > ema50) and
        (close > ema200) and
        (close > vwap) and
        (close > pivot_high) and # Note: Close > previous 20 max implies a breakout or at highs
        (volume >= avg_vol)
    )

    # SELL CONDITIONS
    # 1. Close < EMA(50)
    # 2. Close < EMA(200)
    # 3. Close < VWAP
    # 4. Close < Pivot Low (20-period)
    # 5. Volume >= 20-period average
    
    sell_cond = (
        (close < ema50) and
        (close < ema200) and
        (close < vwap) and
        (close < pivot_low) and
        (volume >= avg_vol)
    )

    # 5. Execute Alert
    signal_type = None
    emoji = ""
    
    if buy_cond:
        signal_type = "STRONG BUY"
        emoji = "📈 🟢"
    elif sell_cond:
        signal_type = "STRONG SELL"
        emoji = "📉 🔴"

    if signal_type:
        msg = (
            f"{emoji} **EGX SIGNAL ALERT** {emoji}\n"
            f"--------------------------------\n"
            f"**Symbol:** `{symbol}`\n"
            f"**Type:** {signal_type}\n"
            f"**Price:** {close:.2f} EGP\n"
            f"**Time:** {timestamp}\n"
            f"--------------------------------\n"
            f"📊 **Tech Stats:**\n"
            f"• Vol: {int(volume)} (Avg: {int(avg_vol)})\n"
            f"• VWAP: {vwap:.2f}\n"
            f"• EMA50: {ema50:.2f} | EMA200: {ema200:.2f}"
        )
        send_telegram_message(msg)
    else:
        # Optional: Log no signal found for debugging logs
        print(f"No signal for {symbol}.")

def main():
    print("🚀 Starting EGX Algorithmic Signal Engine...")
    for symbol in SYMBOLS:
        process_symbol(symbol)
    print("✅ Analysis Complete.")

if __name__ == "__main__":
    main()
