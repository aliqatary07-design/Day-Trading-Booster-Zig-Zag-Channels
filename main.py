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
# 3. سحب الأسهم (Scanner)
# ---------------------------------------------------------
def get_egx_symbols():
    print("🔎 جاري سحب قائمة الأسهم الحالية من TradingView...")
    url = "https://scanner.tradingview.com/egypt/scan"
    payload = {
        "filter": [{"left": "type", "operation": "in_range", "right": ["stock"]}],
        "options": {"lang": "ar"}, 
        "symbols": {"query": {"types": []}},
        "columns": ["name", "close", "description"], 
        "range": [0, 600] 
    }
    # بنستخدم هيدر متصفح حقيقي عشان نتفادى البلوك
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        data = response.json()
        symbols = []
        for item in data.get('data', []):
            d = item['d']
            symbol_code = d[0] # EGX:COMI
            desc = d[2]
            
            if "حق" in desc or "Right" in desc or "اكتتاب" in desc:
                continue
            
            symbols.append(symbol_code) # بنسيبها EGX:COMI زي ما هي عشان الـ UDF محتاجها كده
        return symbols
    except Exception as e:
        print(f"❌ Error fetching symbols: {e}")
        return []

# ---------------------------------------------------------
# 4. سحب الهيستوري (UDF API - الحل السحري) ✨
# ---------------------------------------------------------
def get_tv_candles(symbol, n_bars=60):
    # بنحسب التوقيت (Unix Timestamp)
    # عايزين آخر 5 أيام مثلاً عشان نضمن إن عندنا 60 شمعة ساعة
    to_time = int(time.time())
    from_time = to_time - (5 * 24 * 60 * 60) 
    
    # رابط الـ Widget API (سريع ومجاني)
    # Resolution 60 = 1 Hour
    url = f"https://udf-data-feed.tradingview.com/udf/history?symbol={symbol}&resolution=60&from={from_time}&to={to_time}"
    
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        
        if data['s'] != 'ok':
            return None
            
        # تحويل الداتا لـ DataFrame
        df = pd.DataFrame({
            'high': data['h'],
            'low': data['l'],
            'close': data['c']
        })
        
        return df.tail(n_bars) # نرجع آخر عدد شموع محتاجينه
        
    except Exception:
        return None

# ---------------------------------------------------------
# 5. التحليل (Main Logic)
# ---------------------------------------------------------
def analyze_market():
    is_open, status_msg = check_market_status()
    cairo_tz = pytz.timezone('Africa/Cairo')
    current_time = datetime.datetime.now(cairo_tz).strftime('%I:%M %p').replace("AM", "ص").replace("PM", "م")
    
    extra_note = ""
    # لو مجدول والسوق قافل -> الغي
    if GITHUB_EVENT_NAME == 'schedule' and not is_open:
        print(f"😴 تشغيل مجدول ولكن {status_msg}.")
        return

    # لو يدوي والسوق قافل -> كمل بس حط تنبيه
    if not is_open:
        print(f"⚠️ تشغيل يدوي ({status_msg}).")
        extra_note = f"\n🚫 **تنبيه:** السوق مغلق ({status_msg}).\n"

    tickers = get_egx_symbols()
    print(f"📊 تم العثور على {len(tickers)} سهم. جاري سحب الهيستوري من TradingView...")

    opportunities = []
    
    for symbol in tickers:
        try:
            # هنا بنستخدم الدالة الجديدة
            data = get_tv_candles(symbol, n_bars=25) # محتاجين آخر 20-25 شمعة بس
            
            if data is None or len(data) < 20:
                continue

            # ZigZag Simulation Logic
            period = 20
            # حساب القنوات
            data['Upper_Channel'] = data['high'].rolling(window=period).max().shift(1)
            data['Lower_Channel'] = data['low'].rolling(window=period).min().shift(1)
            
            last_bar = data.iloc[-1]
            close = last_bar['close']
            upper = last_bar['Upper_Channel']
            lower = last_bar['Lower_Channel']
            
            signal_type = None
            
            # الشروط
            if close > upper:
                signal_type = "🔥 اختراق (شراء)"
            elif close < lower:
                signal_type = "🔻 كسر دعم (بيع)"

            if signal_type:
                # بننظف اسم السهم للعرض (نشيل EGX:)
                clean_symbol = symbol.split(":")[1] if ":" in symbol else symbol
                
                opportunities.append({
                    'symbol': clean_symbol,
                    'price': close,
                    'signal': signal_type,
                    'upper': round(upper, 3),
                    'lower': round(lower, 3)
                })
                
            # تأخير بسيط جداً عشان السيرفر ميحسش بضغط (اختياري)
            # time.sleep(0.05)

        except Exception:
            continue

    # --- إرسال التقرير ---
    if opportunities:
        msg = f"⚡ **ZigZag TradingView Signals** ⚡\n"
        if extra_note: msg += extra_note
        msg += f"🕒 {current_time}\n"
        msg += "ــــــــــــــــــــــــــــــــــــــــــــــــ\n"
        
        # ترتيب الفرص (اختياري)
        # opportunities.sort(key=lambda x: x['symbol'])
        
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
