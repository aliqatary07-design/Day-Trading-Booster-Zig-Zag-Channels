import os
import requests
import pandas as pd
import numpy as np
import datetime
import pytz
import time

# ---------------------------------------------------------
# 1. إعدادات البوت والبيئة
# ---------------------------------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
# تحويل النص إلى قائمة IDs مع تنظيف الفراغات
DESTINATIONS = [x.strip() for x in os.environ.get("DESTINATIONS", "").split(",") if x.strip()]
GITHUB_EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch")

def send_message(text):
    for chat_id in DESTINATIONS:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
        try:
            requests.post(url, data=data, timeout=20)
        except Exception as e:
            print(f"❌ Error sending to {chat_id}: {e}")

# ---------------------------------------------------------
# 2. فحص حالة السوق (بتوقيت القاهرة)
# ---------------------------------------------------------
def check_market_status():
    cairo_tz = pytz.timezone('Africa/Cairo')
    now = datetime.datetime.now(cairo_tz)
    
    # الإجازة: الجمعة (4) والسبت (5)
    if now.weekday() in [4, 5]: 
        return False, "عطلة أسبوعية"

    # وقت الجلسة: من 10:00 ص لـ 2:45 م
    start = now.replace(hour=10, minute=0, second=0, microsecond=0)
    end = now.replace(hour=14, minute=45, second=0, microsecond=0)
    
    if start <= now <= end:
        return True, "جلسة تداول"
    return False, "سوق مغلق"

# ---------------------------------------------------------
# 3. سحب قائمة الأسهم (TradingView Scanner API)
# ---------------------------------------------------------
def get_egx_symbols():
    print("🔎 جاري سحب قائمة الأسهم الحالية من TradingView Scanner...")
    url = "https://scanner.tradingview.com/egypt/scan"
    payload = {
        "filter": [{"left": "type", "operation": "in_range", "right": ["stock"]}],
        "options": {"lang": "ar"}, 
        "symbols": {"query": {"types": []}},
        "columns": ["name", "close", "description"], 
        "range": [0, 600] 
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        data = response.json()
        
        symbols = []
        if 'data' in data:
            for item in data['data']:
                d = item['d']
                symbol_full = d[0] # EGX:COMI
                desc = d[2]
                # استبعاد الحقوق والاكتتابات
                if "حق" in desc or "Right" in desc or "اكتتاب" in desc:
                    continue
                symbols.append(symbol_full)
        return symbols
    except Exception as e:
        print(f"❌ Error fetching symbols: {e}")
        return []

# ---------------------------------------------------------
# 4. سحب الهيستوري (UDF Widget API)
# ---------------------------------------------------------
def get_tv_candles(symbol, n_bars=100):
    # نسحب داتا بزيادة (20 يوم) عشان نضمن وجود شموع كافية للحسابات
    to_time = int(time.time())
    from_time = to_time - (20 * 24 * 60 * 60) 
    
    # رابط الـ UDF المباشر (سريع ومجاني ولا يحتاج Login)
    url = f"https://udf-data-feed.tradingview.com/udf/history?symbol={symbol}&resolution=60&from={from_time}&to={to_time}"
    
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        
        if data['s'] != 'ok':
            return None
            
        df = pd.DataFrame({
            'time': data['t'],
            'high': data['h'],
            'low': data['l'],
            'close': data['c']
        })
        
        # ضبط التوقيت للقاهرة
        cairo_tz = pytz.timezone('Africa/Cairo')
        df['dt'] = pd.to_datetime(df['time'], unit='s').dt.tz_localize('UTC').dt.tz_convert(cairo_tz)
        
        return df.tail(n_bars)
    except:
        return None

# ---------------------------------------------------------
# 5. التحليل الرئيسي (Logic) 🧠
# ---------------------------------------------------------
def analyze_market():
    is_open, status_msg = check_market_status()
    cairo_tz = pytz.timezone('Africa/Cairo')
    current_time = datetime.datetime.now(cairo_tz).strftime('%I:%M %p')
    
    # تحديد الوضع:
    # 1. لو مجدول (Schedule): يشتغل لايف فقط وقت الجلسة.
    # 2. لو يدوي (Workflow): يشتغل تاريخي (آخر 3 جلسات) في أي وقت.
    
    IS_HISTORY_MODE = False
    if GITHUB_EVENT_NAME == 'schedule':
        if not is_open:
            print(f"😴 تشغيل مجدول ولكن {status_msg}. (تجاهل)")
            return
        IS_HISTORY_MODE = False
    else:
        IS_HISTORY_MODE = True
    
    tickers = get_egx_symbols()
    mode_txt = 'تاريخي (آخر 3 جلسات)' if IS_HISTORY_MODE else 'لايف (لحظي)'
    print(f"📊 جاري التحليل.. المود: {mode_txt}")
    print(f"عدد الأسهم: {len(tickers)}")

    opportunities = []
    
    for symbol in tickers:
        try:
            # سحب الداتا
            data = get_tv_candles(symbol, n_bars=100)
            if data is None or len(data) < 30: continue

            # --- حساب القنوات (Channels) ---
            # القناة العلوية = أعلى قمة في آخر 20 شمعة
            # القناة السفلية = أقل قاع في آخر 20 شمعة
            # shift(1) عشان القناة تتحسب بناءً على ما سبق، مش الشمعة الحالية
            period = 20
            data['Upper_Ch'] = data['high'].rolling(window=period).max().shift(1)
            data['Lower_Ch'] = data['low'].rolling(window=period).min().shift(1)
            
            # --- البحث عن الإشارات ---
            # في الوضع اليدوي: نبحث في آخر 15 شمعة (3 جلسات)
            # في الوضع اللايف: نبحث في آخر شمعة فقط
            search_window = 15 if IS_HISTORY_MODE else 1
            
            # بنلف من الأحدث للأقدم
            for i in range(len(data)-1, len(data)-1-search_window, -1):
                row = data.iloc[i]
                c = row['close']
                h = row['high']
                l = row['low']
                up = row['Upper_Ch']
                dn = row['Lower_Ch']
                dt_str = row['dt'].strftime('%d/%m %I:%M%p')

                signal_data = None
                
                # --- اللوجيك (الأولوية للكسر، ثم الارتداد) ---
                
                # 1. كسر القناة السفلية (إشارة حمراء - بيع)
                if c < dn:
                    signal_data = {"type": "🔻 كسر دعم (بيع مؤكد)", "icon": "🔴"}
                
                # 2. اختراق القناة العلوية (إشارة خضراء - شراء)
                elif c > up:
                    signal_data = {"type": "🔥 اختراق (شراء مؤكد)", "icon": "🟢"}
                
                # 3. النجمة الزرقاء (ارتداد من القاع) 🔵⭐
                # الشرط: السعر نزل لمس القناة السفلية (l <= dn)
                # وبما إنه مش كسر (لأن الـ c >= dn من الشرط الأولاني)، يبقى ده ارتداد!
                elif l <= dn:
                     signal_data = {"type": "🔵⭐ ارتداد محتمل (نجمة)", "icon": "🔵"}
                
                # 4. النجمة الزرقاء (تصحيح من القمة) 🔵⭐
                # الشرط: السعر طلع لمس القناة العلوية (h >= up)
                elif h >= up:
                     signal_data = {"type": "🔵⭐ تصحيح محتمل (نجمة)", "icon": "🔵"}

                # لو لقينا إشارة، نسجلها ونوقف بحث في السهم ده (عشان نجيب أحدث إشارة بس)
                if signal_data:
                    clean_name = symbol.split(":")[1] if ":" in symbol else symbol
                    opportunities.append({
                        "symbol": clean_name,
                        "price": c,
                        "msg": signal_data['type'],
                        "icon": signal_data['icon'],
                        "lower": dn,
                        "upper": up,
                        "time": dt_str if IS_HISTORY_MODE else "الآن"
                    })
                    break 

            # تأخير بسيط جداً (Optional)
            # time.sleep(0.01)

        except Exception:
            continue

    # --- إرسال التقرير للتليجرام ---
    if opportunities:
        # ترتيب النتائج: الأخضر والأزرق (شراء) الأول، وبعدين الأحمر
        # opportunities.sort(key=lambda x: x['icon'], reverse=True) 

        title = "📜 **تقرير الفرص (آخر 3 جلسات)**" if IS_HISTORY_MODE else "⚡ **تنبيهات حية (Live)** ⚡"
        
        msg = f"{title}\n🕒 {current_time}\n"
        msg += "ــــــــــــــــــــــــــــــــــــــــــــــــ\n"
        
        count = 0
        for op in opportunities:
            # نبعت أول 20 فرصة فقط عشان نتفادى ليميت التليجرام
            if count >= 20: break 
            
            # تنسيق الوقت لو مانيوال
            time_lbl = f" ({op['time']})" if IS_HISTORY_MODE else ""
            
            msg += f"{op['icon']} **{op['symbol']}**{time_lbl}\n"
            msg += f"القرار: {op['msg']}\n"
            msg += f"السعر: {op['price']} | القناة: {round(op['lower'], 2)} - {round(op['upper'], 2)}\n\n"
            count += 1
        
        msg += f"📈 إجمالي الإشارات المرصودة: {len(opportunities)}"
        
        print("📨 Sending Telegram Report...")
        send_message(msg)
        
    elif IS_HISTORY_MODE:
        send_message(f"🕵️‍♂️ **فحص يدوي**\n🕒 {current_time}\nلم يتم العثور على إشارات في آخر 3 جلسات.")
    
    print("✅ تم الانتهاء.")

if __name__ == "__main__":
    analyze_market()
