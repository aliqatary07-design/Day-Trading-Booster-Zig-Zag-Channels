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
# تنظيف الإدخال لضمان عدم وجود فراغات
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
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
def get_tv_candles(symbol, n_bars=200):
    # زودنا المدة لـ 30 يوم عشان نغطي الـ 10 جلسات براحتنا
    to_time = int(time.time())
    from_time = to_time - (30 * 24 * 60 * 60) 
    
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
            'close': data['c'],
            'open': data['o'] # بنحتاج الفتح أحياناً
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
    
    IS_HISTORY_MODE = False
    if GITHUB_EVENT_NAME == 'schedule':
        if not is_open:
            print(f"😴 تشغيل مجدول ولكن {status_msg}. (تجاهل)")
            return
        IS_HISTORY_MODE = False
    else:
        IS_HISTORY_MODE = True
    
    tickers = get_egx_symbols()
    mode_txt = 'تاريخي (آخر 10 جلسات)' if IS_HISTORY_MODE else 'لايف (لحظي)'
    print(f"📊 جاري التحليل.. المود: {mode_txt}")
    print(f"عدد الأسهم: {len(tickers)}")

    opportunities = []
    
    for symbol in tickers:
        try:
            # سحبنا 200 شمعة عشان الحسابات تبقى دقيقة
            data = get_tv_candles(symbol, n_bars=200)
            if data is None or len(data) < 50: continue

            # --- حساب القنوات (Channels) ---
            period = 20
            # القناة بتتحسب بناء على الـ 20 شمعة السابقة للشمعة الحالية
            data['Upper_Ch'] = data['high'].rolling(window=period).max().shift(1)
            data['Lower_Ch'] = data['low'].rolling(window=period).min().shift(1)
            
            # --- نافذة البحث ---
            # 10 جلسات × 5 ساعات = 50 شمعة
            search_window = 50 if IS_HISTORY_MODE else 1
            
            # اللف من الأحدث للأقدم
            # بنعمل check إننا مش بنخرج بره حدود الداتا
            loop_range = min(search_window, len(data) - period - 1)
            
            for i in range(len(data)-1, len(data)-1-loop_range, -1):
                row = data.iloc[i]
                c = row['close']
                h = row['high']
                l = row['low']
                up = row['Upper_Ch']
                dn = row['Lower_Ch']
                dt_str = row['dt'].strftime('%d/%m %I:%M%p')

                # تأكد إن القنوات محسوبة (مش NaN)
                if pd.isna(up) or pd.isna(dn):
                    continue

                signal_data = None
                
                # --- اللوجيك المعدل (حساسية عالية) ---
                
                # 1. كسر صريح للقناة السفلية (إشارة حمراء - بيع)
                if c < dn:
                    signal_data = {"type": "🔻 كسر دعم (بيع مؤكد)", "icon": "🔴"}
                
                # 2. اختراق صريح للقناة العلوية (إشارة خضراء - شراء)
                elif c > up:
                    signal_data = {"type": "🔥 اختراق (شراء مؤكد)", "icon": "🟢"}
                
                # 3. النجمة الزرقاء (ارتداد من القاع) 🔵⭐
                # الشرط: السعر نزل لمس الخط السفلي (أو كسره بـ Shadow) وقفل جواه أو فوقه
                # يعني Low أقل من أو يساوي الخط، والـ Close أكبر من أو يساوي الخط
                elif l <= dn and c >= dn:
                     signal_data = {"type": "🔵⭐ ارتداد محتمل (نجمة)", "icon": "🔵"}
                
                # 4. النجمة الزرقاء (تصحيح من القمة) 🔵⭐
                # الشرط: السعر طلع لمس الخط العلوي وقفل تحته
                elif h >= up and c <= up:
                     signal_data = {"type": "🔵⭐ تصحيح محتمل (نجمة)", "icon": "🔵"}

                # لو لقينا إشارة، نسجلها ونوقف بحث في السهم ده
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

        except Exception:
            continue

    # --- إرسال التقرير للتليجرام ---
    if opportunities:
        # ترتيب: نعرض الأحدث زمناً الأول
        if IS_HISTORY_MODE:
             # بما اننا لفينا من الأحدث للأقدم، فاللستة مترتبة أصلاً بالأحدث
             pass

        title = "📜 **تقرير الفرص (آخر 10 جلسات)**" if IS_HISTORY_MODE else "⚡ **تنبيهات حية (Live)** ⚡"
        
        msg = f"{title}\n🕒 {current_time}\n"
        msg += "ــــــــــــــــــــــــــــــــــــــــــــــــ\n"
        
        count = 0
        for op in opportunities:
            # نبعت أول 25 فرصة مثلاً
            if count >= 25: break 
            
            time_lbl = f" ({op['time']})" if IS_HISTORY_MODE else ""
            
            msg += f"{op['icon']} **{op['symbol']}**{time_lbl}\n"
            msg += f"القرار: {op['msg']}\n"
            msg += f"السعر: {op['price']} | القناة: {round(op['lower'], 2)} - {round(op['upper'], 2)}\n\n"
            count += 1
        
        msg += f"📈 إجمالي الإشارات المرصودة: {len(opportunities)}"
        
        print("📨 Sending Telegram Report...")
        send_message(msg)
        
    elif IS_HISTORY_MODE:
        send_message(f"🕵️‍♂️ **فحص يدوي**\n🕒 {current_time}\nلم يتم العثور على إشارات في آخر 10 جلسات.")
    
    print("✅ تم الانتهاء.")

if __name__ == "__main__":
    analyze_market()
