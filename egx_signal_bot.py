import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import yfinance as yf

# ------------------------
# إعدادات Telegram
BOT_TOKEN = "YOUR_BOT_TOKEN"
CHAT_ID   = "YOUR_CHAT_ID"

# ------------------------
# قائمة الأسهم EGX كمثال
EGX_STOCKS = [
    "AREH.CA",  # المجموعة المصرية العقارية
    "RMSK.CA",  # ريماس
    # اضف باقي الأسهم حسب الحاجة
]

# ------------------------
# دوال حساب المؤشرات
def EMA(series, period):
    return series.ewm(span=period, adjust=False).mean()

def pivot_high_low(df, length=20):
    df['Pivot_High'] = df['High'].rolling(length).max()
    df['Pivot_Low']  = df['Low'].rolling(length).min()
    return df

def vwap(df):
    return (df['Close'] * df['Volume']).cumsum() / df['Volume'].cumsum()

# ------------------------
# دوال الإشارات
def generate_signals(df):
    df['EMA50'] = EMA(df['Close'], 50)
    df['EMA200'] = EMA(df['Close'], 200)
    df = pivot_high_low(df, length=20)
    df['VWAP'] = vwap(df)
    
    df['Buy']  = (df['Close'] > df['EMA50']) & (df['Close'] > df['EMA200']) & (df['Close'] > df['Pivot_High']) & (df['Close'] > df['VWAP'])
    df['Sell'] = (df['Close'] < df['EMA50']) & (df['Close'] < df['EMA200']) & (df['Close'] < df['Pivot_Low']) & (df['Close'] < df['VWAP'])
    return df

# ------------------------
# دالة إرسال رسالة Telegram
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    requests.post(url, json=payload)

# ------------------------
# فحص جميع الأسهم
def check_egx_stocks():
    for symbol in EGX_STOCKS:
        df = yf.download(symbol, period="10d", interval="1h")  # بيانات EGX ساعة بساعة
        df = generate_signals(df)
        last = df.iloc[-1]
        
        if last['Buy']:
            send_telegram_message(f"📈 Buy Signal\nStock: {symbol}\nPrice: {last['Close']}\nTime: {last.name}")
        elif last['Sell']:
            send_telegram_message(f"📉 Sell Signal\nStock: {symbol}\nPrice: {last['Close']}\nTime: {last.name}")

# ------------------------
if name == "main":
    check_egx_stocks()
