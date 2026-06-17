import os
import datetime
import time
import threading
from dotenv import load_dotenv
from kiteconnect import KiteConnect, KiteTicker

load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")
LOT = int(os.getenv("TRADE_QTY", "65"))

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

mode = input("Select Mode (1=Paper, 2=Real): ")
strategy = input("Select Strategy (1=Breakout, 2=Reversal): ")

if strategy == "1":
    side = input("Select Side (1=Upper Breakout, 2=Lower Breakout): ")
else:
    side = input("Select Side (1=Upper Reversal, 2=Lower Reversal): ")

# ================= 2 ENTRY PRICES =================
entry1_trigger = float(input("Enter ENTRY 1 TRIGGER PRICE: "))
entry2_trigger = float(input("Enter ENTRY 2 TRIGGER PRICE: "))

# ================= 2 TARGETS =================
target1 = float(input("Enter TARGET 1 (for Lot 1): "))
target2 = float(input("Enter TARGET 2 (for Lot 2): "))

# ================= SINGLE SL =================
stop_loss = float(input("Enter STOP LOSS (for both lots): "))

upper_trigger = lower_trigger = None
upper_target1 = upper_target2 = upper_sl = None
lower_target1 = lower_target2 = lower_sl = None

if side == "1":
    upper_trigger = entry1_trigger
    upper_target1 = target1
    upper_target2 = target2
    upper_sl = stop_loss
else:
    lower_trigger = entry1_trigger
    lower_target1 = target1
    lower_target2 = target2
    lower_sl = stop_loss


def now():
    return datetime.datetime.now().strftime("%H:%M:%S")


def log(msg):
    print(f"[{now()}] {msg}")


def place_order(symbol, side, price, qty=LOT):
    if mode == "1":
        log(f"[PAPER] {side} {symbol} qty:{qty} at {price}")
        return price
    else:
        try:
            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                tradingsymbol=symbol,
                exchange="NFO",
                transaction_type=side,
                quantity=qty,
                order_type="LIMIT",
                price=price + 0.5 if side == "BUY" else price - 0.5,
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


instruments = kite.instruments("NFO")


def get_atm_symbols(spot_price):
    atm = round(spot_price / 50) * 50
    today = datetime.date.today()

    nearest = None
    ce_symbol = pe_symbol = None
    ce_token = pe_token = None

    for i in instruments:
        if i["name"] != "NIFTY":
            continue

        if i["strike"] != atm:
            continue

        if i["expiry"] < today:
            continue

        if nearest is None or i["expiry"] < nearest:
            nearest = i["expiry"]

    for i in instruments:
        if i["name"] != "NIFTY":
            continue

        if i["strike"] != atm:
            continue

        if i["expiry"] != nearest:
            continue

        if i["instrument_type"] == "CE":
            ce_symbol = i["tradingsymbol"]
            ce_token = i["instrument_token"]

        elif i["instrument_type"] == "PE":
            pe_symbol = i["tradingsymbol"]
            pe_token = i["instrument_token"]

    return ce_symbol, pe_symbol, ce_token, pe_token


spot_token = kite.ltp("NSE:NIFTY 50")["NSE:NIFTY 50"]["instrument_token"]

kws = KiteTicker(API_KEY, ACCESS_TOKEN)
entry_lock = threading.Lock()

# ================= ENHANCED STATE =================
state = {
    # LOT 1 (Entry 1)
    "entry1": None,
    "entry1_price": None,
    "entry1_symbol": None,
    "entry1_token": None,
    "entry1_triggered": False,
    "lot1_exited": False,

    # LOT 2 (Entry 2)
    "entry2": None,
    "entry2_price": None,
    "entry2_symbol": None,
    "entry2_token": None,
    "entry2_triggered": False,
    "lot2_exited": False,

    # Common
    "trade_side": None,
    "exit_done": False,
    "broke_upper": False,
    "broke_lower": False,
    "entry1_spotted": False,
    "entry2_spotted": False,
    "last_spot_price": None,
    
    # ✅ DYNAMIC SL - will be updated when Target 1 hit
    "dynamic_upper_sl": None,
    "dynamic_lower_sl": None,
    "sl_adjusted": False,
}


def reset_state():
    state["broke_upper"] = False
    state["broke_lower"] = False
    state["entry1_spotted"] = False
    state["entry2_spotted"] = False


def get_option_ltp(symbol):
    try:
        return kite.ltp(f"NFO:{symbol}")[f"NFO:{symbol}"]["last_price"]
    except:
        time.sleep(0.2)
        try:
            return kite.ltp(f"NFO:{symbol}")[f"NFO:{symbol}"]["last_price"]
        except:
            return state.get("entry1_price", 0) or 0


def safe_exit_lot1(ws, reason, price):
    if state["lot1_exited"]:
        return

    state["lot1_exited"] = True

    try:
        ltp = get_option_ltp(state["entry1_symbol"])
        if not ltp or ltp == 0:
            log("LTP failed for Lot1, using entry price fallback")
            ltp = state["entry1_price"]

        try:
            place_order(state["entry1_symbol"], "SELL", ltp, LOT)
        except:
            log("Retrying LOT1 SELL...")
            time.sleep(0.2)
            place_order(state["entry1_symbol"], "SELL", ltp, LOT)

        log(f"LOT 1 EXIT: {reason} at {price}")

    except Exception as e:
        log(f"LOT1 EXIT ERROR: {e}")


def safe_exit_lot2(ws, reason, price):
    if state["lot2_exited"]:
        return

    state["lot2_exited"] = True

    try:
        ltp = get_option_ltp(state["entry2_symbol"])
        if not ltp or ltp == 0:
            log("LTP failed for Lot2, using entry price fallback")
            ltp = state["entry2_price"]

        try:
            place_order(state["entry2_symbol"], "SELL", ltp, LOT)
        except:
            log("Retrying LOT2 SELL...")
            time.sleep(0.2)
            place_order(state["entry2_symbol"], "SELL", ltp, LOT)

        log(f"LOT 2 EXIT: {reason} at {price}")

    except Exception as e:
        log(f"LOT2 EXIT ERROR: {e}")


def safe_exit_both(ws, reason, price):
    if not state["lot1_exited"]:
        safe_exit_lot1(ws, reason, price)

    if not state["lot2_exited"]:
        safe_exit_lot2(ws, reason, price)

    state["exit_done"] = True
    ws.close()


def close_bot(reason):
    """Close bot and script"""
    log(f"🛑 CLOSING BOT: {reason}")
    state["exit_done"] = True
    try:
        kws.close()
    except:
        pass
    exit(0)


def on_ticks(ws, ticks):
    if state["exit_done"]:
        return

    for tick in ticks:
        token = tick["instrument_token"]
        price = tick["last_price"]

        if token != spot_token:
            continue

        prev_price = state["last_spot_price"]
        state["last_spot_price"] = price

        if prev_price is None:
            continue

        # ================= ENTRY LOGIC =================

        # ================= BREAKOUT =================
        if strategy == "1":

            # ---------- UPPER BREAKOUT ----------
            if side == "1" and upper_trigger is not None:

                # ENTRY 1
                if not state["entry1_triggered"] and prev_price < entry1_trigger and price >= entry1_trigger:
                    with entry_lock:
                        if state["entry1_triggered"]:
                            continue

                        state["entry1_triggered"] = True
                        state["entry1_spotted"] = True

                        ce_symbol, _, ce_token, _ = get_atm_symbols(price)

                        state["entry1_token"] = ce_token
                        state["entry1_symbol"] = ce_symbol
                        state["trade_side"] = "UPPER"

                        ws.subscribe([ce_token])
                        ws.set_mode(ws.MODE_FULL, [ce_token])

                        ltp = get_option_ltp(ce_symbol)
                        fill = place_order(ce_symbol, "BUY", ltp, LOT)

                        state["entry1"] = fill
                        state["entry1_price"] = fill

                        log(f"🔼 LOT 1 BUY CE {ce_symbol} at {fill}")
                        log(f"TARGET 1: {upper_target1} | TARGET 2: {upper_target2} | SL: {upper_sl}")

                # ENTRY 2
                if state["entry1_spotted"] and not state["entry2_triggered"] and prev_price < entry2_trigger and price >= entry2_trigger:
                    with entry_lock:
                        if state["entry2_triggered"]:
                            continue

                        state["entry2_triggered"] = True

                        ce_symbol, _, ce_token, _ = get_atm_symbols(price)

                        state["entry2_token"] = ce_token
                        state["entry2_symbol"] = ce_symbol

                        ws.subscribe([ce_token])
                        ws.set_mode(ws.MODE_FULL, [ce_token])

                        ltp = get_option_ltp(ce_symbol)
                        fill = place_order(ce_symbol, "BUY", ltp, LOT)

                        state["entry2"] = fill
                        state["entry2_price"] = fill

                        log(f"🔼 LOT 2 BUY CE {ce_symbol} at {fill}")

                # LOT 1 EXIT AT TARGET 1
                if state["entry1_triggered"] and not state["lot1_exited"] and price >= upper_target1:
                    safe_exit_lot1(ws, "TARGET 1 HIT", price)
                    
                    # ✅ IF ENTRY 2 ALREADY TRIGGERED, ADJUST SL TO ENTRY 2 PRICE
                    if state["entry2_triggered"] and not state["sl_adjusted"]:
                        state["dynamic_upper_sl"] = state["entry2_price"]
                        state["sl_adjusted"] = True
                        log(f"📍 SL ADJUSTED TO ENTRY 2 PRICE: {state['entry2_price']} (BREAKEVEN PROTECTION)")
                    elif not state["entry2_triggered"]:
                        # ✅ NO ENTRY 2, CLOSE BOT
                        close_bot("TARGET 1 HIT - NO ENTRY 2 - CLOSING BOT")
                        return

                # LOT 2 EXIT AT TARGET 2 OR ADJUSTED SL
                if state["entry2_triggered"] and not state["lot2_exited"]:
                    # Use dynamic SL if it's been adjusted
                    current_sl = state["dynamic_upper_sl"] if state["sl_adjusted"] else upper_sl
                    
                    if price >= upper_target2:
                        safe_exit_lot2(ws, "TARGET 2 HIT", price)
                        # ✅ TARGET 2 HIT - CLOSE BOT
                        close_bot("TARGET 2 HIT - CLOSING BOT")
                        return
                    elif price <= current_sl:
                        safe_exit_lot2(ws, f"STOPLOSS HIT (Adjusted SL: {current_sl})", price)
                        # ✅ SL HIT - CLOSE BOT
                        close_bot("STOPLOSS HIT ON LOT 2 - CLOSING BOT")
                        return

                # IF ENTRY1 ONLY, EXIT AT SL
                if state["entry1_triggered"] and not state["entry2_triggered"] and not state["lot1_exited"] and price <= upper_sl:
                    safe_exit_lot1(ws, "STOPLOSS HIT", price)
                    # ✅ SL HIT ON LOT1 ONLY - CLOSE BOT
                    close_bot("STOPLOSS HIT ON LOT 1 - CLOSING BOT")
                    return

            # ---------- LOWER BREAKOUT ----------
            elif side == "2" and lower_trigger is not None:

                # ENTRY 1
                if not state["entry1_triggered"] and prev_price > entry1_trigger and price <= entry1_trigger:
                    with entry_lock:
                        if state["entry1_triggered"]:
                            continue

                        state["entry1_triggered"] = True
                        state["entry1_spotted"] = True

                        _, pe_symbol, _, pe_token = get_atm_symbols(price)

                        state["entry1_token"] = pe_token
                        state["entry1_symbol"] = pe_symbol
                        state["trade_side"] = "LOWER"

                        ws.subscribe([pe_token])
                        ws.set_mode(ws.MODE_FULL, [pe_token])

                        ltp = get_option_ltp(pe_symbol)
                        fill = place_order(pe_symbol, "BUY", ltp, LOT)

                        state["entry1"] = fill
                        state["entry1_price"] = fill

                        log(f"🔼 LOT 1 BUY PE {pe_symbol} at {fill}")
                        log(f"TARGET 1: {lower_target1} | TARGET 2: {lower_target2} | SL: {lower_sl}")

                # ENTRY 2
                if state["entry1_spotted"] and not state["entry2_triggered"] and prev_price > entry2_trigger and price <= entry2_trigger:
                    with entry_lock:
                        if state["entry2_triggered"]:
                            continue

                        state["entry2_triggered"] = True

                        _, pe_symbol, _, pe_token = get_atm_symbols(price)

                        state["entry2_token"] = pe_token
                        state["entry2_symbol"] = pe_symbol

                        ws.subscribe([pe_token])
                        ws.set_mode(ws.MODE_FULL, [pe_token])

                        ltp = get_option_ltp(pe_symbol)
                        fill = place_order(pe_symbol, "BUY", ltp, LOT)

                        state["entry2"] = fill
                        state["entry2_price"] = fill

                        log(f"🔼 LOT 2 BUY PE {pe_symbol} at {fill}")

                # LOT 1 EXIT AT TARGET 1
                if state["entry1_triggered"] and not state["lot1_exited"] and price <= lower_target1:
                    safe_exit_lot1(ws, "TARGET 1 HIT", price)
                    
                    # ✅ IF ENTRY 2 ALREADY TRIGGERED, ADJUST SL TO ENTRY 2 PRICE
                    if state["entry2_triggered"] and not state["sl_adjusted"]:
                        state["dynamic_lower_sl"] = state["entry2_price"]
                        state["sl_adjusted"] = True
                        log(f"📍 SL ADJUSTED TO ENTRY 2 PRICE: {state['entry2_price']} (BREAKEVEN PROTECTION)")
                    elif not state["entry2_triggered"]:
                        # ✅ NO ENTRY 2, CLOSE BOT
                        close_bot("TARGET 1 HIT - NO ENTRY 2 - CLOSING BOT")
                        return

                # LOT 2 EXIT AT TARGET 2 OR ADJUSTED SL
                if state["entry2_triggered"] and not state["lot2_exited"]:
                    # Use dynamic SL if it's been adjusted
                    current_sl = state["dynamic_lower_sl"] if state["sl_adjusted"] else lower_sl
                    
                    if price <= lower_target2:
                        safe_exit_lot2(ws, "TARGET 2 HIT", price)
                        # ✅ TARGET 2 HIT - CLOSE BOT
                        close_bot("TARGET 2 HIT - CLOSING BOT")
                        return
                    elif price >= current_sl:
                        safe_exit_lot2(ws, f"STOPLOSS HIT (Adjusted SL: {current_sl})", price)
                        # ✅ SL HIT - CLOSE BOT
                        close_bot("STOPLOSS HIT ON LOT 2 - CLOSING BOT")
                        return

                # IF ENTRY1 ONLY, EXIT AT SL
                if state["entry1_triggered"] and not state["entry2_triggered"] and not state["lot1_exited"] and price >= lower_sl:
                    safe_exit_lot1(ws, "STOPLOSS HIT", price)
                    # ✅ SL HIT ON LOT1 ONLY - CLOSE BOT
                    close_bot("STOPLOSS HIT ON LOT 1 - CLOSING BOT")
                    return

            else:
                reset_state()

        # ================= REVERSAL =================
        elif strategy == "2":

            # ---------- UPPER REVERSAL ----------
            if side == "1" and upper_trigger is not None:

                # Track breakout
                if price > entry1_trigger:
                    state["broke_upper"] = True

                # ENTRY 1 (reversal trigger)
                if state["broke_upper"] and not state["entry1_triggered"] and prev_price > entry1_trigger and price <= entry1_trigger:
                    with entry_lock:
                        if state["entry1_triggered"]:
                            continue

                        state["entry1_triggered"] = True
                        state["entry1_spotted"] = True

                        _, pe_symbol, _, pe_token = get_atm_symbols(price)

                        state["entry1_token"] = pe_token
                        state["entry1_symbol"] = pe_symbol
                        state["trade_side"] = "UPPER"

                        ws.subscribe([pe_token])
                        ws.set_mode(ws.MODE_FULL, [pe_token])

                        ltp = get_option_ltp(pe_symbol)
                        fill = place_order(pe_symbol, "BUY", ltp, LOT)

                        state["entry1"] = fill
                        state["entry1_price"] = fill

                        log(f"🔼 LOT 1 BUY PE {pe_symbol} at {fill}")
                        log(f"TARGET 1: {upper_target1} | TARGET 2: {upper_target2} | SL: {upper_sl}")

                # ENTRY 2 (at entry2_trigger after entry1)
                if state["entry1_spotted"] and not state["entry2_triggered"] and prev_price < entry2_trigger and price >= entry2_trigger:
                    with entry_lock:
                        if state["entry2_triggered"]:
                            continue

                        state["entry2_triggered"] = True

                        _, pe_symbol, _, pe_token = get_atm_symbols(price)

                        state["entry2_token"] = pe_token
                        state["entry2_symbol"] = pe_symbol

                        ws.subscribe([pe_token])
                        ws.set_mode(ws.MODE_FULL, [pe_token])

                        ltp = get_option_ltp(pe_symbol)
                        fill = place_order(pe_symbol, "BUY", ltp, LOT)

                        state["entry2"] = fill
                        state["entry2_price"] = fill

                        log(f"🔼 LOT 2 BUY PE {pe_symbol} at {fill}")

                # LOT 1 EXIT AT TARGET 1 (below entry for reversal)
                if state["entry1_triggered"] and not state["lot1_exited"] and price <= upper_target1:
                    safe_exit_lot1(ws, "TARGET 1 HIT", price)
                    
                    # ✅ IF ENTRY 2 ALREADY TRIGGERED, ADJUST SL TO ENTRY 2 PRICE
                    if state["entry2_triggered"] and not state["sl_adjusted"]:
                        state["dynamic_upper_sl"] = state["entry2_price"]
                        state["sl_adjusted"] = True
                        log(f"📍 SL ADJUSTED TO ENTRY 2 PRICE: {state['entry2_price']} (BREAKEVEN PROTECTION)")
                    elif not state["entry2_triggered"]:
                        # ✅ NO ENTRY 2, CLOSE BOT
                        close_bot("TARGET 1 HIT - NO ENTRY 2 - CLOSING BOT")
                        return

                # LOT 2 EXIT AT TARGET 2 OR ADJUSTED SL
                if state["entry2_triggered"] and not state["lot2_exited"]:
                    # Use dynamic SL if it's been adjusted
                    current_sl = state["dynamic_upper_sl"] if state["sl_adjusted"] else upper_sl
                    
                    if price <= upper_target2:
                        safe_exit_lot2(ws, "TARGET 2 HIT", price)
                        # ✅ TARGET 2 HIT - CLOSE BOT
                        close_bot("TARGET 2 HIT - CLOSING BOT")
                        return
                    elif price >= current_sl:
                        safe_exit_lot2(ws, f"STOPLOSS HIT (Adjusted SL: {current_sl})", price)
                        # ✅ SL HIT - CLOSE BOT
                        close_bot("STOPLOSS HIT ON LOT 2 - CLOSING BOT")
                        return

                # IF ENTRY1 ONLY, EXIT AT SL
                if state["entry1_triggered"] and not state["entry2_triggered"] and not state["lot1_exited"] and price >= upper_sl:
                    safe_exit_lot1(ws, "STOPLOSS HIT", price)
                    # ✅ SL HIT ON LOT1 ONLY - CLOSE BOT
                    close_bot("STOPLOSS HIT ON LOT 1 - CLOSING BOT")
                    return

            # ---------- LOWER REVERSAL ----------
            elif side == "2" and lower_trigger is not None:

                # Track breakout
                if price < entry1_trigger:
                    state["broke_lower"] = True

                # ENTRY 1 (reversal trigger)
                if state["broke_lower"] and not state["entry1_triggered"] and prev_price < entry1_trigger and price >= entry1_trigger:
                    with entry_lock:
                        if state["entry1_triggered"]:
                            continue

                        state["entry1_triggered"] = True
                        state["entry1_spotted"] = True

                        ce_symbol, _, ce_token, _ = get_atm_symbols(price)

                        state["entry1_token"] = ce_token
                        state["entry1_symbol"] = ce_symbol
                        state["trade_side"] = "LOWER"

                        ws.subscribe([ce_token])
                        ws.set_mode(ws.MODE_FULL, [ce_token])

                        ltp = get_option_ltp(ce_symbol)
                        fill = place_order(ce_symbol, "BUY", ltp, LOT)

                        state["entry1"] = fill
                        state["entry1_price"] = fill

                        log(f"🔼 LOT 1 BUY CE {ce_symbol} at {fill}")
                        log(f"TARGET 1: {lower_target1} | TARGET 2: {lower_target2} | SL: {lower_sl}")

                # ENTRY 2 (at entry2_trigger after entry1)
                if state["entry1_spotted"] and not state["entry2_triggered"] and prev_price > entry2_trigger and price <= entry2_trigger:
                    with entry_lock:
                        if state["entry2_triggered"]:
                            continue

                        state["entry2_triggered"] = True

                        ce_symbol, _, ce_token, _ = get_atm_symbols(price)

                        state["entry2_token"] = ce_token
                        state["entry2_symbol"] = ce_symbol

                        ws.subscribe([ce_token])
                        ws.set_mode(ws.MODE_FULL, [ce_token])

                        ltp = get_option_ltp(ce_symbol)
                        fill = place_order(ce_symbol, "BUY", ltp, LOT)

                        state["entry2"] = fill
                        state["entry2_price"] = fill

                        log(f"🔼 LOT 2 BUY CE {ce_symbol} at {fill}")

                # LOT 1 EXIT AT TARGET 1 (above entry for reversal)
                if state["entry1_triggered"] and not state["lot1_exited"] and price >= lower_target1:
                    safe_exit_lot1(ws, "TARGET 1 HIT", price)
                    
                    # ✅ IF ENTRY 2 ALREADY TRIGGERED, ADJUST SL TO ENTRY 2 PRICE
                    if state["entry2_triggered"] and not state["sl_adjusted"]:
                        state["dynamic_lower_sl"] = state["entry2_price"]
                        state["sl_adjusted"] = True
                        log(f"📍 SL ADJUSTED TO ENTRY 2 PRICE: {state['entry2_price']} (BREAKEVEN PROTECTION)")
                    elif not state["entry2_triggered"]:
                        # ✅ NO ENTRY 2, CLOSE BOT
                        close_bot("TARGET 1 HIT - NO ENTRY 2 - CLOSING BOT")
                        return

                # LOT 2 EXIT AT TARGET 2 OR ADJUSTED SL
                if state["entry2_triggered"] and not state["lot2_exited"]:
                    # Use dynamic SL if it's been adjusted
                    current_sl = state["dynamic_lower_sl"] if state["sl_adjusted"] else lower_sl
                    
                    if price >= lower_target2:
                        safe_exit_lot2(ws, "TARGET 2 HIT", price)
                        # ✅ TARGET 2 HIT - CLOSE BOT
                        close_bot("TARGET 2 HIT - CLOSING BOT")
                        return
                    elif price <= current_sl:
                        safe_exit_lot2(ws, f"STOPLOSS HIT (Adjusted SL: {current_sl})", price)
                        # ✅ SL HIT - CLOSE BOT
                        close_bot("STOPLOSS HIT ON LOT 2 - CLOSING BOT")
                        return

                # IF ENTRY1 ONLY, EXIT AT SL
                if state["entry1_triggered"] and not state["entry2_triggered"] and not state["lot1_exited"] and price <= lower_sl:
                    safe_exit_lot1(ws, "STOPLOSS HIT", price)
                    # ✅ SL HIT ON LOT1 ONLY - CLOSE BOT
                    close_bot("STOPLOSS HIT ON LOT 1 - CLOSING BOT")
                    return


def on_connect(ws, response):
    log("Websocket connected 🔼")
    ws.subscribe([spot_token])
    ws.set_mode(ws.MODE_FULL, [spot_token])


kws.on_ticks = on_ticks
kws.on_connect = on_connect

log("Starting Trading Bot...")
log(f"Mode: {'PAPER' if mode == '1' else 'REAL'}")
log(f"Strategy: {'BREAKOUT' if strategy == '1' else 'REVERSAL'}")
log(f"Entry 1: {entry1_trigger} | Entry 2: {entry2_trigger}")
log(f"Target 1: {target1} | Target 2: {target2}")
log(f"Stop Loss: {stop_loss}")

kws.connect()
