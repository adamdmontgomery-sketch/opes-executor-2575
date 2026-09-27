import os
import time
import json
import requests
import threading
from datetime import datetime
from flask import Flask, jsonify

app = Flask(__name__)

# Config
STRATEGY_ID = int(os.getenv("STRATEGY_ID", 2575))
BUY_AMOUNT_USD = float(os.getenv("BUY_AMOUNT_USD", 5.00))
OPES_API_URL = os.getenv("OPES_API_URL", "https://opessignals.com/mcp.php")
OPES_TOKEN = os.getenv("OPES_TOKEN", "")
POLL_INTERVAL_SEC = 20

positions = {}

def get_opes_trades():
    payload = {
        "method": "tools/call",
        "params": {
            "name": "strategy_trades",
            "arguments": {"strategy_id": STRATEGY_ID, "limit": 15}
        }
    }
    headers = {"Content-Type": "application/json"}
    if OPES_TOKEN:
        headers["Authorization"] = f"Bearer {OPES_TOKEN}"
    try:
        res = requests.post(OPES_API_URL, json=payload, headers=headers, timeout=10).json()
        raw = res.get("result", "{}")
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data.get("trades", [])
    except Exception as e:
        print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] Polling error: {e}")
        return []

def execute_onchain_buy(symbol, ca, amount_usd):
    print(f"🚀 [BUY EXECUTED] {symbol} ({ca[:8]}...) | Amount: ${amount_usd:.2f}")
    return {"status": "success", "entry_time": time.time()}

def execute_onchain_sell(symbol, ca, pct, reason):
    print(f"🎯 [SELL EXECUTED] {symbol} | {pct}% | Reason: {reason}")
    return {"status": "success"}

def sync_cycle():
    trades = get_opes_trades()
    for t in trades:
        ca = t.get("contract_address") or t.get("token")
        symbol = t.get("token", "UNKNOWN")
        status = t.get("status")
        peak = float(t.get("peak_multiple", 1.0) or 1.0)

        # 1. New Buy
        if status == "open" and ca not in positions:
            tx = execute_onchain_buy(symbol, ca, BUY_AMOUNT_USD)
            positions[ca] = {
                "symbol": symbol,
                "buy_amount": BUY_AMOUNT_USD,
                "tp1_hit": False,
                "current_sl": 0.70,
                "highest_peak": 1.00
            }
            continue

        # 2. Manage Exits
        if ca in positions:
            pos = positions[ca]
            pos["highest_peak"] = max(pos["highest_peak"], peak)
            curr_peak = pos["highest_peak"]

            # Breakeven @ +15%
            if curr_peak >= 1.15 and pos["current_sl"] < 1.0:
                pos["current_sl"] = 1.0
                print(f"🛡️ [BREAKEVEN] {pos['symbol']} SL moved to 1.0x.")

            # TP1 @ 1.5x (Sell 60%)
            if curr_peak >= 1.5 and not pos["tp1_hit"]:
                execute_onchain_sell(pos["symbol"], ca, 60, "TP1 1.5x reached")
                pos["tp1_hit"] = True

            # TP2 @ 2.5x (Sell 40% - 100% Closed)
            if curr_peak >= 2.5:
                execute_onchain_sell(pos["symbol"], ca, 100, "TP2 2.5x reached (Full Close)")
                del positions[ca]
                continue

            # Trailing Stop (20% pullback)
            if curr_peak > 1.2 and peak <= (curr_peak * 0.80):
                execute_onchain_sell(pos["symbol"], ca, 100, f"Trailing Stop 20% drop from {curr_peak:.2f}x")
                del positions[ca]
                continue

            # Hard Stop-Loss (-30%) or Breakeven
            if peak <= pos["current_sl"]:
                execute_onchain_sell(pos["symbol"], ca, 100, "Hit SL / Breakeven")
                del positions[ca]
                continue

            # Opes closed
            if status in ["won", "lost"]:
                execute_onchain_sell(pos["symbol"], ca, 100, f"Opes Marked {status.upper()}")
                del positions[ca]

def background_worker():
    while True:
        try:
            sync_cycle()
        except Exception as e:
            print(f"Worker exception: {e}")
        time.sleep(POLL_INTERVAL_SEC)

# Start background polling thread on container boot
threading.Thread(target=background_worker, daemon=True).start()

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "service": "opes-executor-2575",
        "status": "running",
        "active_positions": len(positions),
        "strategy_id": STRATEGY_ID,
        "buy_amount_usd": BUY_AMOUNT_USD
    })

@app.route("/sync", methods=["POST"])
def manual_sync():
    sync_cycle()
    return jsonify({"status": "synced", "positions": len(positions)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

from google.cloud import firestore

# Initialize Firestore (uses default Cloud Run credentials automatically)
db = firestore.Client(project=os.getenv("GCP_PROJECT", "opes-robinhood-executor"))
COLLECTION = "trades-2575"

# 1. On Container Start: Restore any open positions from Firestore
def load_open_positions():
    docs = db.collection(COLLECTION).where("status", "==", "open").stream()
    for doc in docs:
        positions[doc.id] = doc.to_dict()
    print(f"Loaded {len(positions)} active positions from Firestore.")

# 2. When a Buy Executes: Save to Firestore
def record_buy(ca, symbol, buy_amount, tx_hash):
    trade_data = {
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        "symbol": symbol,
        "contract_address": ca,
        "buy_amount": buy_amount,
        "status": "open",
        "tp1_hit": False,
        "current_sl": 0.70,
        "highest_peak": 1.00,
        "realized_pnl": 0.00
    }
    db.collection(COLLECTION).document(ca).set(trade_data)
    positions[ca] = trade_data

# 3. When an Exit / Progress Updates: Update Firestore
def record_update(ca, update_dict):
    db.collection(COLLECTION).document(ca).update(update_dict)
