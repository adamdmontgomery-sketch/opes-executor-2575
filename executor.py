"""
OpesSignals Strategy 2575 (RH Momentum Scalper) Dedicated Executor
Target Project: opes-robinhood-executor
Collection: trades_v2
"""

import time
import requests
import json
import os
from datetime import datetime

# ==========================================
# CONFIGURATION
# ==========================================
GCP_PROJECT = os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT", "opes-robinhood-executor")
FIRESTORE_COLLECTION = os.getenv("FIRESTORE_COLLECTION", "trades_v2")
STRATEGY_ID = int(os.getenv("STRATEGY_ID", 2575))
BUY_AMOUNT_USD = float(os.getenv("BUY_AMOUNT_USD", 5.00))
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", 20))

OPES_TOKEN = os.getenv("OPES_TOKEN", "")
if OPES_TOKEN and "token=" not in OPES_TOKEN:
    OPES_MCP_URL = f"https://opessignals.com/mcp.php?token={OPES_TOKEN}"
else:
    OPES_MCP_URL = os.getenv("OPES_MCP_URL", "https://opessignals.com/mcp.php")

# Initialize Firestore
db = None
FIRESTORE_ENABLED = False
try:
    from google.cloud import firestore
    db = firestore.Client(project=GCP_PROJECT)
    FIRESTORE_ENABLED = True
    print(f"Connected to Cloud Firestore (Project: {GCP_PROJECT}, Collection: {FIRESTORE_COLLECTION})")
except Exception as e:
    print(f"Firestore initialization warning: {e}")

positions = {}

def load_open_positions():
    """Restores active trades from Firestore so state survives service restarts."""
    if not FIRESTORE_ENABLED or not db:
        return
    try:
        docs = db.collection(FIRESTORE_COLLECTION).where("status", "==", "open").stream()
        for doc in docs:
            positions[doc.id] = doc.to_dict()
        print(f"Restored {len(positions)} active positions from Firestore ({FIRESTORE_COLLECTION})")
    except Exception as e:
        print(f"Error restoring positions: {e}")

def get_opes_strategy_trades(limit=15):
    """Fetches Strategy 2575 trades using the Opes MCP API."""
    payload = {
        "method": "tools/call",
        "params": {
            "name": "strategy_trades",
            "arguments": {"strategy_id": STRATEGY_ID, "limit": limit}
        }
    }
    try:
        res = requests.post(OPES_MCP_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=10).json()
        raw = res.get("result", "{}")
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data.get("trades", [])
    except Exception as e:
        print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] Polling error from Opes API: {e}")
        return []

def record_trade_to_firestore(ca, trade_data):
    if FIRESTORE_ENABLED and db:
        try:
            db.collection(FIRESTORE_COLLECTION).document(ca).set(trade_data, merge=True)
            print(f" [FIRESTORE] Recorded {trade_data.get('token_symbol', ca)} -> {trade_data.get('status')}")
        except Exception as e:
            print(f"Firestore write error: {e}")

def execute_onchain_buy(symbol, ca, amount_usd):
    print(f"\n >>> [BUY EXECUTED] {symbol} ({ca[:10]}...) | Amount: ${amount_usd:.2f}")
    return {"status": "success", "tx_hash": "0x_simulated_buy", "entry_price": 0.0001, "entry_time": time.time()}

def execute_onchain_sell(symbol, ca, pct, reason):
    print(f" >>> [SELL EXECUTED] {symbol} | Portion: {pct}% | Reason: {reason}")
    return {"status": "success", "tx_hash": "0x_simulated_sell"}

def process_sync():
    """Runs one iteration of signal evaluation, order placement, and exit ladder updates."""
    trades = get_opes_strategy_trades(limit=15)
    
    for t in trades:
        ca = t.get("contract_address") or t.get("token")
        symbol = t.get("token", "UNKNOWN")
        status = t.get("status")
        peak = float(t.get("peak_multiple", 1.0) or 1.0)
        
        # 1. New Signal Entry
        if status == "open" and ca not in positions:
            tx = execute_onchain_buy(symbol, ca, BUY_AMOUNT_USD)
            trade_record = {
                "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
                "token_symbol": symbol,
                "chain": "Robinhood EVM",
                "contract_address": ca,
                "caller": t.get("source", f"Strategy {STRATEGY_ID}"),
                "buy_amount": BUY_AMOUNT_USD,
                "entry_time": tx["entry_time"],
                "status": "open",
                "tp1_hit": False,
                "current_sl": 0.70,       # Initial hard SL (-30%)
                "highest_peak": 1.00,
                "realized_pnl": 0.00,
                "unrealized_pnl": 0.00,
                "total_pnl": 0.00
            }
            positions[ca] = trade_record
            record_trade_to_firestore(ca, trade_record)
            continue

        # 2. Manage Active Position Exit Ladder
        if ca in positions:
            pos = positions[ca]
            pos["highest_peak"] = max(pos["highest_peak"], peak)
            curr_peak = pos["highest_peak"]
            updated = False
            
            # Breakeven: at 1.15x (+15%), move stop-loss to entry (1.0x)
            if curr_peak >= 1.15 and pos["current_sl"] < 1.0:
                pos["current_sl"] = 1.0
                updated = True
                print(f" [BREAKEVEN] {pos['token_symbol']} reached +15%. SL moved to entry.")

            # TP1: at 1.5x, take 60% profit
            if curr_peak >= 1.5 and not pos["tp1_hit"]:
                execute_onchain_sell(pos["token_symbol"], ca, pct=60, reason="TP1 1.5x hit (Take 60%)")
                pos["tp1_hit"] = True
                pos["realized_pnl"] = round(BUY_AMOUNT_USD * 0.6 * (1.5 - 1.0), 2)
                updated = True

            # TP2: at 2.5x, full close remaining 40%
            if curr_peak >= 2.5:
                execute_onchain_sell(pos["token_symbol"], ca, pct=100, reason="TP2 2.5x hit (Full Close)")
                pos["status"] = "won"
                pos["realized_pnl"] = round(pos["realized_pnl"] + (BUY_AMOUNT_USD * 0.4 * (2.5 - 1.0)), 2)
                pos["total_pnl"] = pos["realized_pnl"]
                record_trade_to_firestore(ca, pos)
                del positions[ca]
                continue

            # Trailing Stop: 20% drawdown from peak once past 1.2x
            trailing_exit_floor = curr_peak * 0.80
            if curr_peak > 1.2 and peak <= trailing_exit_floor:
                execute_onchain_sell(pos["token_symbol"], ca, pct=100, reason=f"Trailing Stop hit (-20% from {curr_peak:.2f}x)")
                pos["status"] = "won" if pos["realized_pnl"] > 0 else "lost"
                pos["total_pnl"] = pos["realized_pnl"]
                record_trade_to_firestore(ca, pos)
                del positions[ca]
                continue

            # Hard Stop-Loss / Breakeven Stop
            if peak <= pos["current_sl"]:
                sl_type = "Breakeven" if pos["current_sl"] >= 1.0 else "Hard Stop-Loss (-30%)"
                execute_onchain_sell(pos["token_symbol"], ca, pct=100, reason=f"Hit {sl_type}")
                pos["status"] = "won" if pos["current_sl"] >= 1.0 else "lost"
                if pos["status"] == "lost":
                    pos["realized_pnl"] = -round(BUY_AMOUNT_USD * 0.30, 2)
                pos["total_pnl"] = pos["realized_pnl"]
                record_trade_to_firestore(ca, pos)
                del positions[ca]
                continue

            # Upstream resolution marked won/lost
            if status in ["won", "lost"]:
                execute_onchain_sell(pos["token_symbol"], ca, pct=100, reason=f"Upstream Opes closed as {status.upper()}")
                pos["status"] = status
                record_trade_to_firestore(ca, pos)
                del positions[ca]
                continue

            if updated:
                record_trade_to_firestore(ca, pos)

def run_loop():
    """Background loop daemon for continuous execution."""
    print("=" * 65)
    print("  OPESSIGNALS EXECUTOR: STRATEGY #2575 (RH MOMENTUM SCALPER)")
    print(f"  Target Project: {GCP_PROJECT} | Collection: {FIRESTORE_COLLECTION}")
    print("=" * 65)
    
    load_open_positions()
    while True:
        try:
            process_sync()
        except Exception as e:
            print(f"Loop iteration error: {e}")
        time.sleep(POLL_INTERVAL_SEC)
