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
# هنجيب البيانات من إعدادات GitHub Secrets
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
# هنا بنحول النص لليستة، افصل الايديهات بفاصلة في الـ Secret
# مثال للـ Secret: 929830200,1302442906
DESTINATIONS = os.environ.get("DESTINATIONS", "").split(",") 

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
# 2. التأكد من وقت الجلسة (بتوقيت القاهرة)
# ---------------------------------------------------------
def is_market_open():
    cairo_tz = pytz.timezone('Africa/Cairo')
    now = datetime.datetime.now(cairo_tz)
    
    # أيام العمل: الأحد=6 ... الخميس=3
    # الجمعة(4) والسبت(5) إجازة
    if now.weekday() in [4, 5]: 
        print("😴 اليوم عطلة رسمية.")
        return False

    # وقت الجلسة من 10:00 ص لـ 2:45 م (شاملة الجلسة الاستكشافية والمزاد)
    start = now.replace(hour=10, minute=0, second=0, microsecond=0)
    end = now.replace(hour=14, minute=45, second=0, microsecond=0)
    
    if start <= now <= end:
        return True
    
    print(f"😴 السوق مغلق حالياً. الساعة: {now.strftime('%I:%M %p')}")
    return False

# ---------------------------------------------------------
# 3. سحب قائمة الأسهم (طريقتك - Scanner API)
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
            symbol_code = d[0] # اسم السهم
            desc = d[2]        # الوصف
            
            # 🧹 فلتر النظافة (حقوق الاكتتاب)
            if "حق" in desc or "Right" in desc or "اكتتاب" in desc:
                continue
            
            # بنرجع الرمز بصيغة EGX:SYMBOL عشان المكتبة التانية تفهمه
            symbols.append(symbol_code)
            
        return symbols

    except Exception as e:
        print(f"❌ Error fetching symbols: {e}")
        return []

# ---------------------------------------------------------
# 4. تحليل الزيج زاج (ZigZag Channel)
# ---------------------------------------------------------
def analyze_market():
    if not is_market_open():
        return

    # 1. هات لستة الأسهم الحالية
    tickers = get_egx_symbols()
    print(f"📊 تم العثور على {len(tickers)} سهم نشط. جاري التحليل...")

    # تهيئة الاتصال لسحب الهيستوري
    tv = TvDatafeed() 
    
    opportunities = []

    for symbol in tickers:
        try:
            # نسحب داتا الساعة (آخر 60 شمعة تكفي لحساب القنوات)
            data = tv.get_hist(symbol=symbol, exchange='EGX', interval=Interval.in_1_hour, n_bars=60)
            
            if data is None or data.empty:
                continue

            # --- ZigZag / Channel Logic (Simulated) ---
            # بنحسب القناة بناء على أعلى قمة وأقل قاع في آخر 20 شمعة
            period = 20
            data['Upper_Channel'] = data['high'].rolling(window=period).max().shift(1)
            data['Lower_Channel'] = data['low'].rolling(window=period).min().shift(1)
            
            last_bar = data.iloc[-1]
            close = last_bar['close']
            upper = last_bar['Upper_Channel']
            lower = last_bar['Lower_Channel']
            
            # --- شروط الإشارة ---
            signal_type = None
            
            # 1. كسر القناة العلوية (اختراق قمة) -> شراء
            if close > upper:
                signal_type = "🔥 اختراق (شراء)"
            
            # 2. كسر القناة السفلية (كسر قاع) -> بيع
            elif close < lower:
                signal_type = "🔻 كسر دعم (بيع)"

            # لو في إشارة، ضيفها للفرص
            if signal_type:
                opportunities.append({
                    'symbol': symbol,
                    'price': close,
                    'signal': signal_type,
                    'upper': upper,
                    'lower': lower
                })
            
            # تريح السيرفر شوية عشان ميعملش بلوك
            # time.sleep(0.1) 

        except Exception as e:
            continue

    # ---------------------------------------------------------
    # 5. إرسال التقرير
    # ---------------------------------------------------------
    cairo_tz = pytz.timezone('Africa/Cairo')
    current_time = datetime.datetime.now(cairo_tz).strftime('%I:%M %p').replace("AM", "ص").replace("PM", "م")

    if opportunities:
        msg = f"⚡ **ZigZag Booster Signals** ⚡\n🕒 {current_time}\n"
        msg += "ــــــــــــــــــــــــــــــــــــــــــــــــ\n"
        
        # نبعت أول 15 فرصة بس عشان رسالة تليجرام متضربش
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
        print("😴 لا توجد إشارات اختراق للقنوات حالياً.")

if __name__ == "__main__":
    analyze_market()
