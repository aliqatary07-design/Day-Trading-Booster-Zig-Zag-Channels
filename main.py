import os
import requests
import pandas as pd
import numpy as np
import datetime
import pytz
import time

# ---------------------------------------------------------
# 1. إعدادات البوت
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
                print(f"❌ Error: {e}")

# ---------------------------------------------------------
# 2. فحص حالة السوق
# ---------------------------------------------------------
def check_market_status():
    cairo_tz = pytz.timezone('Africa/Cairo')
    now = datetime.datetime.now(cairo_tz)
    if now.weekday() in [4, 5]: return False, "عطلة أسبوعية"
    start = now.replace(hour=10, minute=0, second=0)
    end = now.replace(hour=14, minute=45, second=0)
    return (start <= now <= end), ("جلسة تداول" if start <= now <= end else "سوق مغلق")

# ---------------------------------------------------------
# 3. سحب الأسهم (Scanner)
# ---------------------------------------------------------
def get_egx_symbols():
    url = "https://scanner.tradingview.com/egypt/scan"
    payload = {
        "filter": [{"left": "type", "operation": "in_range", "right": ["stock"]}],
        "options": {"lang": "ar"}, 
        "symbols": {"query": {"types": []}},
        "columns": ["name", "close", "description"], 
        "range": [0, 600] 
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        data = response.json()
        return [item['d'][0] for item in data.get('data', []) if "حق" not in item['d'][2]]
    except: return []

# ---------------------------------------------------------
# 4. سحب الهيستوري (UDF API)
# ---------------------------------------------------------
def get_tv_candles(symbol, n_bars=100):
    to_time = int(time.time())
    from_time = to_time - (20 * 24 * 60 * 60) # 20 يوم لضمان الداتا
    url = f"https://udf-data-feed.tradingview.com/udf/history?symbol={symbol}&resolution=60&from={from_time}&to={to_time}"
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        if data['s'] != 'ok': return None
        df = pd.DataFrame({'time': data['t'], 'high': data['h'], 'low': data['l'], 'close': data['c']})
        cairo_tz = pytz.timezone('Africa/Cairo')
        df['dt'] = pd.to_datetime(df['time'], unit='s').dt.tz_localize('UTC').dt.tz_convert(cairo_tz)
        return df.tail(n_bars)
    except: return None

# ---------------------------------------------------------
# 5. التحليل (The Advanced Brain) 🧠
# ---------------------------------------------------------
def analyze_market():
    is_open, status_msg = check_market_status()
    cairo_tz = pytz.timezone('Africa/Cairo')
    current_time = datetime.datetime.now(cairo_tz).strftime('%I:%M %p')
    
    # الوضع اليدوي يبحث في الماضي، الأوتوماتيك يبحث في اللحظة الحالية
    IS_HISTORY_MODE = GITHUB_EVENT_NAME != 'schedule'
    
    tickers = get_egx_symbols()
    opportunities = []
    
    for symbol in tickers:
        try:
            data = get_tv_candles(symbol, n_bars=50)
            if data is None or len(data) < 30: continue

            # --- حسابات القنوات والزيج زاج ---
            period = 20
            data['High_Roll'] = data['high'].rolling(window=period).max().shift(1)
            data['Low_Roll'] = data['low'].rolling(window=period).min().shift(1)
            
            # بنبحث في آخر 15 شمعة لو مانيوال، أو آخر شمعة لو أوتوماتيك
            search_range = 15 if IS_HISTORY_MODE else 1
            
            for i in range(len(data)-1, len(data)-1-search_range, -1):
                row = data.iloc[i]
                prev_row = data.iloc[i-1]
                close = row['close']
                high = row['high']
                low = row['low']
                upper = row['High_Roll']
                lower = row['Low_Roll']
                date_str = row['dt'].strftime('%d/%m %I:%M%p')

                signal = None
                
                # 1. إشارة زرقاء مع نجمة (🔵⭐) - ارتداد قيد التكوين
                # لو السعر لمس القناة وبدأ يرتد في نفس الشمعة
                if low <= lower and close > lower:
                    signal = {"type": "🔵⭐ ارتداد محتمل (نجمة)", "icon": "🔵"}
                elif high >= upper and close < upper:
                    signal = {"type": "🔵⭐ تصحيح محتمل (نجمة)", "icon": "🔵"}
                
                # 2. إشارة خضراء/حمراء (🟢/🔴) - اختراق مؤكد
                elif close > upper:
                    signal = {"type": "🔥 اختراق شراء مؤكد", "icon": "🟢"}
                elif close < lower:
                    signal = {"type": "🔻 كسر بيع مؤكد", "icon": "🔴"}
                
                # 3. إشارة سوداء (⚫) - قمة أو قاع تاريخي ثابت
                # لو الشمعة دي هي الأعلى أو الأقل في الـ 20 ساعة اللي فاتوا
                elif high == upper:
                    signal = {"type": "⚫ قمة تاريخية ثابتة", "icon": "⚫"}
                elif low == lower:
                    signal = {"type": "⚫ قاع تاريخي ثابت", "icon": "⚫"}

                if signal:
                    clean_name = symbol.split(":")[1] if ":" in symbol else symbol
                    opportunities.append({
                        "symbol": clean_name,
                        "price": close,
                        "msg": signal['type'],
                        "icon": signal['icon'],
                        "time": date_str if IS_HISTORY_MODE else "الآن"
                    })
                    break # لقينا أحدث إشارة للسهم ده، انقل على اللي بعده

        except: continue

    # --- الإرسال ---
    if opportunities:
        msg = f"{'📜 تقرير الفرص' if IS_HISTORY_MODE else '⚡ تنبيهات حية'}\n🕒 {current_time}\n"
        msg += "ــــــــــــــــــــــــــــــــــــــــــــــــ\n"
        for op in opportunities[:20]:
            msg += f"{op['icon']} **{op['symbol']}** ({op['time']})\n"
            msg += f"القرار: {op['msg']}\n"
            msg += f"السعر الحالي: {op['price']}\n\n"
        
        send_message(msg)
    elif IS_HISTORY_MODE:
        send_message(f"🕵️‍♂️ تم الفحص يا قطري، مفيش إشارات قوية حالياً.")

if __name__ == "__main__":
    analyze_market()
