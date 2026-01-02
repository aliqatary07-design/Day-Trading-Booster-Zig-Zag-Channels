import os
import requests
import pandas as pd
import numpy as np
import datetime
import pytz
import time
from tvDatafeed import TvDatafeed, Interval

# ---------------------------------------------------------
# 1. إعدادات البوت والبيئة
# ---------------------------------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
DESTINATIONS = os.environ.get("DESTINATIONS", "").split(",") 

GITHUB_EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch")

def send_message(text):
    for chat_id in DESTINATIONS:
        if chat_id.strip():
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            data = {'chat_id': chat_id.strip(), 'text': text, 'parse_mode': 'Markdown'}
            try:
                requests.post(url, data=data, timeout=10)
            except Exception as e:
                print(f"❌ Error sending to {chat_id}: {e}")

# ---------------------------------------------------------
# 2. فحص حالة السوق
# ---------------------------------------------------------
def check_market_status():
    cairo_tz = pytz.timezone('Africa/Cairo')
    now = datetime.datetime.now(cairo_tz)
    
    if now.weekday() in [4, 5]: 
        return False, "عطلة أسبوعية"

    start = now.replace(hour=10, minute=0, second=0, microsecond=0)
    end = now.replace(hour=14, minute=45, second=0, microsecond=0)
    
    if start <= now <= end:
        return True, "جلسة تداول"
    
    return False, "سوق مغلق"

# ---------------------------------------------------------
# 3. سحب قائمة الأسهم (Fix applied here)
# ---------------------------------------------------------
def get_egx_symbols():
    print("🔎 جاري سحب قائمة الأسهم من TradingView Scanner...")
    url = "https://scanner.tradingview.com/egypt/scan"
    payload = {
        "filter": [{"left": "type", "operation": "in_range", "right": ["stock"]}],
        "options": {"lang": "ar"}, 
        "symbols": {"query": {"types": []}},
        "columns": ["name", "close", "description", "change"], 
        "range": [0, 600] 
    }
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        data = response.json()
        if not data or 'data' not in data: return []

        symbols = []
        for item in data['data']:
            d = item['d']
            symbol_full = d[0] # ده بييجي شكله كده EGX:COMI
            desc = d[2]
            
            if "حق" in desc or "Right" in desc or "اكتتاب" in desc:
                continue
            
            # 🔥 التعديل هنا: بنفصل اسم البورصة عن اسم السهم
            if ":" in symbol_full:
                symbol_code = symbol_full.split(":")[1] # بناخد اللي بعد النقطتين بس
            else:
                symbol_code = symbol_full

            symbols.append(symbol_code)
            
        return symbols

    except Exception as e:
        print(f"❌ Error fetching symbols: {e}")
        return []

# ---------------------------------------------------------
# 4. التحليل الرئيسي
# ---------------------------------------------------------
def analyze_market():
    is_open, status_msg = check_market_status()
    cairo_tz = pytz.timezone('Africa/Cairo')
    current_time = datetime.datetime.now(cairo_tz).strftime('%I:%M %p').replace("AM", "ص").replace("PM", "م")
    
    extra_note = ""
    
    if GITHUB_EVENT_NAME == 'schedule' and not is_open:
        print(f"😴 تشغيل مجدول ولكن {status_msg}. (لن يتم السحب)")
        return

    if not is_open:
        print(f"⚠️ تشغيل يدوي في وقت الإغلاق ({status_msg}). جاري سحب آخر بيانات...")
        extra_note = f"\n🚫 **تنبيه:** السوق مغلق ({status_msg}).\n📊 **هذه البيانات بناءً على آخر إغلاق للسوق.**\n"

    tickers = get_egx_symbols()
    print(f"📊 تم العثور على {len(tickers)} سهم. جاري التحليل...")

    tv = TvDatafeed() 
    opportunities = []

    for symbol in tickers:
        try:
            # هنا بنبعت الرمز صافي (COMI) والبورصة (EGX)
            data = tv.get_hist(symbol=symbol, exchange='EGX', interval=Interval.in_1_hour, n_bars=60)
            
            if data is None or data.empty:
                # print(f"⚠️ No data for {symbol}") # شيل الكومنت لو عايز تشوف مين اللي بايظ
                continue

            # ZigZag Logic
            period = 20
            data['Upper_Channel'] = data['high'].rolling(window=period).max().shift(1)
            data['Lower_Channel'] = data['low'].rolling(window=period).min().shift(1)
            
            last_bar = data.iloc[-1]
            close = last_bar['close']
            upper = last_bar['Upper_Channel']
            lower = last_bar['Lower_Channel']
            
            signal_type = None
            if close > upper:
                signal_type = "🔥 اختراق (شراء)"
            elif close < lower:
                signal_type = "🔻 كسر دعم (بيع)"

            if signal_type:
                opportunities.append({
                    'symbol': symbol,
                    'price': close,
                    'signal': signal_type,
                    'upper': upper,
                    'lower': lower
                })

        except Exception as e:
            continue

    if opportunities:
        msg = f"⚡ **ZigZag Booster Signals** ⚡\n"
        if extra_note:
            msg += extra_note
        msg += f"🕒 {current_time}\n"
        msg += "ــــــــــــــــــــــــــــــــــــــــــــــــ\n"
        
        for op in opportunities[:15]: 
            icon = "🟢" if "شراء" in op['signal'] else "🔴"
            msg += f"{icon} **{op['symbol']}**\n"
            msg += f"القرار: {op['signal']}\n"
            msg += f"السعر: {op['price']}\n"
            msg += f"القناة: {op['lower']} - {op['upper']}\n\n"
        
        msg += f"📈 إجمالي الفرص: {len(opportunities)}"
        
        print("📨 Sending Telegram Report...")
        send_message(msg)
    else:
        if GITHUB_EVENT_NAME != 'schedule':
            no_op_msg = f"⚡ **ZigZag Booster** ⚡\n{extra_note}🕒 {current_time}\n✅ تم المسح، لا توجد إشارات اختراق حالياً."
            send_message(no_op_msg)
        print("😴 لا توجد فرص.")

if __name__ == "__main__":
    analyze_market()
