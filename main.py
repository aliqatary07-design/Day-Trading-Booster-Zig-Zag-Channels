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
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        data = response.json()
        symbols = []
        for item in data.get('data', []):
            d = item['d']
            symbol_code = d[0] 
            desc = d[2]
            
            if "حق" in desc or "Right" in desc or "اكتتاب" in desc:
                continue
            
            symbols.append(symbol_code) 
        return symbols
    except Exception as e:
        print(f"❌ Error fetching symbols: {e}")
        return []

# ---------------------------------------------------------
# 4. سحب الهيستوري (UDF API)
# ---------------------------------------------------------
def get_tv_candles(symbol, n_bars=100):
    # بنزود عدد الشموع عشان نقدر نرجع لورا في المانيوال
    to_time = int(time.time())
    from_time = to_time - (15 * 24 * 60 * 60) # آخر 15 يوم عشان نضمن داتا كافية
    
    url = f"https://udf-data-feed.tradingview.com/udf/history?symbol={symbol}&resolution=60&from={from_time}&to={to_time}"
    
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        
        if data['s'] != 'ok':
            return None
            
        df = pd.DataFrame({
            'time': data['t'],
            'high': data['h'],
            'low': data['l'],
            'close': data['c']
        })
        
        # تحويل الوقت لتوقيت القاهرة للقراءة
        cairo_tz = pytz.timezone('Africa/Cairo')
        df['dt'] = pd.to_datetime(df['time'], unit='s').dt.tz_localize('UTC').dt.tz_convert(cairo_tz)
        
        return df.tail(n_bars) 
        
    except Exception:
        return None

# ---------------------------------------------------------
# 5. التحليل (The Brain) 🧠
# ---------------------------------------------------------
def analyze_market():
    is_open, status_msg = check_market_status()
    cairo_tz = pytz.timezone('Africa/Cairo')
    current_time = datetime.datetime.now(cairo_tz).strftime('%I:%M %p')
    
    # تحديد المود: هل هو بحث تاريخي (Manual) ولا لايف (Auto)؟
    # لو مجدول والسوق فاتح -> لايف (آخر شمعة بس)
    # لو يدوي أو السوق قافل -> تاريخي (آخر 3 جلسات)
    
    IS_HISTORY_MODE = False
    
    if GITHUB_EVENT_NAME == 'schedule':
        if not is_open:
            print(f"😴 تشغيل مجدول ولكن {status_msg}. (تجاهل)")
            return
        IS_HISTORY_MODE = False # Auto Live
    else:
        # يدوي (workflow_dispatch)
        IS_HISTORY_MODE = True

    tickers = get_egx_symbols()
    print(f"📊 جاري التحليل.. المود: {'تاريخي (آخر 3 جلسات)' if IS_HISTORY_MODE else 'لايف (لحظي)'}")

    opportunities = []
    
    for symbol in tickers:
        try:
            # لو تاريخي بنحتاج داتا أكتر عشان نرجع لورا
            data = get_tv_candles(symbol, n_bars=100 if IS_HISTORY_MODE else 40)
            
            if data is None or len(data) < 20:
                continue

            # ZigZag Logic
            period = 20
            data['Upper_Channel'] = data['high'].rolling(window=period).max().shift(1)
            data['Lower_Channel'] = data['low'].rolling(window=period).min().shift(1)
            
            # --- الفلترة ---
            found_signal = None
            
            if IS_HISTORY_MODE:
                # بنلف من ورا لقدام (من أحدث شمعة لأقدم شمعة)
                # بنبحث في آخر 20 شمعة (حوالي 3-4 جلسات تداول)
                search_window = 20 
                for i in range(len(data)-1, len(data)-search_window, -1):
                    row = data.iloc[i]
                    close = row['close']
                    upper = row['Upper_Channel']
                    lower = row['Lower_Channel']
                    date_str = row['dt'].strftime('%d/%m %I:%M%p')

                    # شرط الزيج زاج
                    if close > upper:
                        found_signal = {
                            'symbol': symbol, 'price': close, 'signal': "🔥 شراء (سابق)", 
                            'upper': upper, 'lower': lower, 'time': date_str, 'is_fresh': (i == len(data)-1)
                        }
                        break # لقينا أحدث إشارة، نوقف تدوير في السهم ده
                    elif close < lower:
                        found_signal = {
                            'symbol': symbol, 'price': close, 'signal': "🔻 بيع (سابق)", 
                            'upper': upper, 'lower': lower, 'time': date_str, 'is_fresh': (i == len(data)-1)
                        }
                        break
            
            else:
                # Mode: Live Auto (Check ONLY last candle)
                row = data.iloc[-1]
                close = row['close']
                upper = row['Upper_Channel']
                lower = row['Lower_Channel']
                
                if close > upper:
                    found_signal = {'symbol': symbol, 'price': close, 'signal': "🔥 اختراق (شراء)", 'upper': upper, 'lower': lower, 'time': 'الآن'}
                elif close < lower:
                    found_signal = {'symbol': symbol, 'price': close, 'signal': "🔻 كسر دعم (بيع)", 'upper': upper, 'lower': lower, 'time': 'الآن'}

            # لو لقينا حاجة نضيفها
            if found_signal:
                # تنظيف الاسم
                clean_symbol = symbol.split(":")[1] if ":" in symbol else symbol
                found_signal['symbol'] = clean_symbol
                opportunities.append(found_signal)

        except Exception:
            continue

    # --- إرسال التقرير ---
    if opportunities:
        # لو مانيوال، رتبهم بالأحدث أولاً
        if IS_HISTORY_MODE:
            # بنحاول نرتب بالتاريخ التقريبي (مجازاً هنا هنعرضهم زي ما جم بس ممكن نرتبهم)
            opportunities.reverse() 

        title = "📜 **تقرير الفرص الأخيرة (آخر 3 جلسات)**" if IS_HISTORY_MODE else "⚡ **إشارات حية (Live)** ⚡"
        
        msg = f"{title}\n🕒 {current_time}\n"
        msg += "ــــــــــــــــــــــــــــــــــــــــــــــــ\n"
        
        count = 0
        for op in opportunities:
            # في المانيوال، مش عايزين نبعت كل حاجة، نبعت أهم 20 سهم مثلاً عشان الرسالة متطولش
            if count >= 20: break 
            
            icon = "🟢" if "شراء" in op['signal'] else "🔴"
            # لو الإشارة قديمة شوية نكتب وقتها
            time_label = f" ({op['time']})" if IS_HISTORY_MODE else ""
            
            msg += f"{icon} **{op['symbol']}**{time_label}\n"
            msg += f"القرار: {op['signal']}\n"
            msg += f"السعر: {op['price']} | القناة: {round(op['lower'], 2)} - {round(op['upper'], 2)}\n\n"
            count += 1
        
        msg += f"📈 إجمالي الفرص المرصودة: {len(opportunities)}"
        print("📨 Sending Telegram Report...")
        send_message(msg)
    else:
        # رسالة لو مفيش حاجة خالص
        if IS_HISTORY_MODE:
             send_message(f"🕵️‍♂️ **فحص يدوي**\n🕒 {current_time}\nلم يتم العثور على إشارات اختراق صريحة في آخر 3 جلسات.")
        print("😴 لا توجد فرص.")

if __name__ == "__main__":
    analyze_market()
