"""
Cloud Run Entrypoint for opes-executor-2575
Listens on $PORT, serves health checks, JSON API, and executes background loops.
"""

import os
import threading
import json
from flask import Flask, jsonify, request
from google.cloud import firestore
import executor

app = Flask(__name__)

GCP_PROJECT = os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT", "opes-robinhood-executor")
FIRESTORE_COLLECTION = os.getenv("FIRESTORE_COLLECTION", "trades_v2")

# Start the executor loop in a background daemon thread
executor_thread = threading.Thread(target=executor.run_loop, daemon=True)
executor_thread.start()

@app.route("/healthz", methods=["GET"])
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "opes-executor-2575",
        "project": GCP_PROJECT,
        "collection": FIRESTORE_COLLECTION,
        "active_positions_count": len(executor.positions)
    }), 200

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "opes-executor-2575",
        "project": GCP_PROJECT,
        "collection": FIRESTORE_COLLECTION,
        "active_positions": list(executor.positions.values())
    }), 200

@app.route("/api/trades", methods=["GET"])
def get_trades():
    """Direct API endpoint for trades stored in Firestore."""
    if not executor.FIRESTORE_ENABLED or not executor.db:
        return jsonify({"trades": list(executor.positions.values()), "source": "memory"}), 200
    try:
        docs = executor.db.collection(FIRESTORE_COLLECTION).order_by("timestamp", direction=firestore.Query.DESCENDING).limit(100).stream()
        results = [dict(id=d.id, **d.to_dict()) for d in docs]
        return jsonify({"trades": results, "project": GCP_PROJECT, "collection": FIRESTORE_COLLECTION}), 200
    except Exception as e:
        # Fallback without ordering index requirement
        try:
            docs = executor.db.collection(FIRESTORE_COLLECTION).limit(100).stream()
            results = [dict(id=d.id, **d.to_dict()) for d in docs]
            return jsonify({"trades": results, "project": GCP_PROJECT, "collection": FIRESTORE_COLLECTION}), 200
        except Exception as inner_e:
            return jsonify({"error": str(inner_e)}), 500

@app.route("/api/sync", methods=["POST", "GET"])
def trigger_sync():
    """Manual sync trigger endpoint."""
    try:
        executor.process_sync()
        return jsonify({
            "status": "success",
            "active_positions": len(executor.positions)
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
