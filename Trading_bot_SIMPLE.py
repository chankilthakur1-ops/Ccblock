import os
import datetime
import time
from dotenv import load_dotenv
from kiteconnect import KiteConnect, KiteTicker

load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")
LOT = int(os.getenv("TRADE_QTY", "65"))

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

# ================= USER INPUT =================
mode = input("Select Mode (1=Paper, 2=Real): ")
side = input("Select Side (1=Upper/Buy, 2=Lower/Sell): ")

entry_price = float(input("Enter ENTRY PRICE: "))
target_price = float(input("Enter TARGET PRICE: "))
stop_loss_price = float(input("Enter STOP LOSS PRICE: "))


def now():
    return datetime.datetime.now().strftime("%H:%M:%S")


def log(msg):
    print(f"[{now()}] {msg}")


def place_order(symbol, order_side, price, qty=LOT):
    if mode == "1":
        log(f"[PAPER] {order_side} {symbol} qty:{qty} at {price}")
        return price
    else:
        try:
            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                tradingsymbol=symbol,
                exchange="NFO",
                transaction_type=order_side,
                quantity=qty,
                order_type="LIMIT",
                price=price + 0.5 if order_side == "BUY" else price - 0.5,
                product="MIS"
            )

            for _ in range(15):
                time.sleep(0.2)
                hist = kite.order_history(order_id)
                avg = hist[-1]["average_price"]
                if avg and avg > 0:
                    return avg

            return price
        except Exception as e:
            log(f"ORDER ERROR: {e}")
            return price


# Get all NFO instruments
instruments = kite.instruments("NFO")


def get_option_symbol(spot_price, instrument_type):
    """Get CE or PE symbol for current price"""
    atm = round(spot_price / 50) * 50
    today = datetime.date.today()
    
    # Find nearest expiry
    nearest = None
    for i in instruments:
        if i["name"] != "NIFTY":
            continue
        if i["strike"] != atm:
            continue
        if i["expiry"] < today:
            continue
        if nearest is None or i["expiry"] < nearest:
            nearest = i["expiry"]
    
    # Get symbol
    for i in instruments:
        if i["name"] != "NIFTY":
            continue
        if i["strike"] != atm:
            continue
        if i["expiry"] != nearest:
            continue
        if i["instrument_type"] == instrument_type:
            return i["tradingsymbol"], i["instrument_token"]
    
    return None, None


def get_option_ltp(symbol):
    """Get live price of option"""
    try:
        return kite.ltp(f"NFO:{symbol}")[f"NFO:{symbol}"]["last_price"]
    except:
        time.sleep(0.2)
        try:
            return kite.ltp(f"NFO:{symbol}")[f"NFO:{symbol}"]["last_price"]
        except:
            return 0


# State
state = {
    "entry_done": False,
    "exit_done": False,
    "entry_price_filled": None,
    "entry_symbol": None,
    "entry_token": None,
}

spot_token = kite.ltp("NSE:NIFTY 50")["NSE:NIFTY 50"]["instrument_token"]

kws = KiteTicker(API_KEY, ACCESS_TOKEN)


def on_ticks(ws, ticks):
    if state["exit_done"]:
        return

    for tick in ticks:
        token = tick["instrument_token"]
        price = tick["last_price"]

        if token != spot_token:
            continue

        log(f"NIFTY: {price}")

        # ================= ENTRY LOGIC =================
        
        # UPPER (BUY CALL)
        if side == "1" and not state["entry_done"]:
            if price >= entry_price:
                log(f"✅ ENTRY PRICE HIT! Price: {price}")
                
                symbol, token_val = get_option_symbol(price, "CE")
                if symbol:
                    ltp = get_option_ltp(symbol)
                    fill = place_order(symbol, "BUY", ltp, LOT)
                    
                    state["entry_done"] = True
                    state["entry_price_filled"] = fill
                    state["entry_symbol"] = symbol
                    state["entry_token"] = token_val
                    
                    log(f"🔼 BUY CE {symbol} at {fill}")
                    log(f"TARGET: {target_price} | SL: {stop_loss_price}")
                    
                    ws.subscribe([token_val])
                    ws.set_mode(ws.MODE_FULL, [token_val])

        # LOWER (BUY PUT)
        elif side == "2" and not state["entry_done"]:
            if price <= entry_price:
                log(f"✅ ENTRY PRICE HIT! Price: {price}")
                
                symbol, token_val = get_option_symbol(price, "PE")
                if symbol:
                    ltp = get_option_ltp(symbol)
                    fill = place_order(symbol, "BUY", ltp, LOT)
                    
                    state["entry_done"] = True
                    state["entry_price_filled"] = fill
                    state["entry_symbol"] = symbol
                    state["entry_token"] = token_val
                    
                    log(f"🔼 BUY PE {symbol} at {fill}")
                    log(f"TARGET: {target_price} | SL: {stop_loss_price}")
                    
                    ws.subscribe([token_val])
                    ws.set_mode(ws.MODE_FULL, [token_val])

        # ================= EXIT LOGIC =================
        
        # UPPER - Exit at target or SL
        if side == "1" and state["entry_done"] and not state["exit_done"]:
            if price >= target_price:
                log(f"🎯 TARGET HIT at {price}")
                opt_ltp = get_option_ltp(state["entry_symbol"])
                place_order(state["entry_symbol"], "SELL", opt_ltp, LOT)
                state["exit_done"] = True
                ws.close()
                exit(0)
            elif price <= stop_loss_price:
                log(f"🛑 STOP LOSS HIT at {price}")
                opt_ltp = get_option_ltp(state["entry_symbol"])
                place_order(state["entry_symbol"], "SELL", opt_ltp, LOT)
                state["exit_done"] = True
                ws.close()
                exit(0)

        # LOWER - Exit at target or SL
        if side == "2" and state["entry_done"] and not state["exit_done"]:
            if price <= target_price:
                log(f"🎯 TARGET HIT at {price}")
                opt_ltp = get_option_ltp(state["entry_symbol"])
                place_order(state["entry_symbol"], "SELL", opt_ltp, LOT)
                state["exit_done"] = True
                ws.close()
                exit(0)
            elif price >= stop_loss_price:
                log(f"🛑 STOP LOSS HIT at {price}")
                opt_ltp = get_option_ltp(state["entry_symbol"])
                place_order(state["entry_symbol"], "SELL", opt_ltp, LOT)
                state["exit_done"] = True
                ws.close()
                exit(0)


def on_connect(ws, response):
    log("Websocket connected ✅")
    ws.subscribe([spot_token])
    ws.set_mode(ws.MODE_FULL, [spot_token])


kws.on_ticks = on_ticks
kws.on_connect = on_connect

log("🚀 Starting Simple Trading Bot...")
log(f"Mode: {'PAPER' if mode == '1' else 'REAL'}")
log(f"Side: {'UPPER (CALL)' if side == '1' else 'LOWER (PUT)'}")
log(f"Entry Price: {entry_price}")
log(f"Target: {target_price}")
log(f"Stop Loss: {stop_loss_price}")

kws.connect()
