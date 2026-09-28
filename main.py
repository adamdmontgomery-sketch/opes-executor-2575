"""
opes-executor-2575 Cloud Run Entrypoint
Compatible with both Dockerfile (Flask/Gunicorn) and Cloud Run Functions (functions-framework).
"""

import os
import json
import threading
from datetime import datetime
import executor

GCP_PROJECT = os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT", "opes-robinhood-executor")
FIRESTORE_COLLECTION = os.getenv("FIRESTORE_COLLECTION", "trades_v2")

# Start background scalper executor daemon once on cold start
try:
    executor_thread = threading.Thread(target=executor.run_loop, daemon=True)
    executor_thread.start()
    print("Background Strategy 2575 executor loop started.")
except Exception as e:
    print(f"Background thread warning: {e}")

def handle_routes(path: str, method: str):
    """Core request router supporting all endpoints."""
    clean_path = path.rstrip("/") or "/"

    # 1. Health check endpoint
    if clean_path == "/healthz":
        return {
            "status": "healthy",
            "service": "opes-executor-2575",
            "project": GCP_PROJECT,
            "collection": FIRESTORE_COLLECTION,
            "active_positions_count": len(executor.positions)
        }, 200

    # 2. Trades retrieval endpoint
    if clean_path == "/api/trades":
        if not executor.FIRESTORE_ENABLED or not executor.db:
            return {
                "trades": list(executor.positions.values()),
                "source": "memory",
                "collection": FIRESTORE_COLLECTION
            }, 200
        try:
            from google.cloud import firestore
            docs = executor.db.collection(FIRESTORE_COLLECTION).limit(100).stream()
            results = [dict(id=d.id, **d.to_dict()) for d in docs]
            return {
                "trades": results,
                "project": GCP_PROJECT,
                "collection": FIRESTORE_COLLECTION
            }, 200
        except Exception as e:
            return {"error": str(e)}, 500

    # 3. Manual sync trigger endpoint
    if clean_path == "/api/sync":
        try:
            executor.process_sync()
            return {
                "status": "success",
                "active_positions": len(executor.positions)
            }, 200
        except Exception as e:
            return {"error": str(e)}, 500

    # 4. Root service status
    return {
        "service": "opes-executor-2575",
        "project": GCP_PROJECT,
        "collection": FIRESTORE_COLLECTION,
        "status": "online",
        "active_positions": list(executor.positions.values())
    }, 200

# -------------------------------------------------------------
# Google Functions Framework entry point (if deployed as a Cloud Function)
# -------------------------------------------------------------
try:
    import functions_framework

    @functions_framework.http
    def main(request):
        data, status_code = handle_routes(request.path, request.method)
        return (json.dumps(data), status_code, {"Content-Type": "application/json"})
except ImportError:
    pass

# -------------------------------------------------------------
# Flask / Gunicorn entry point (if deployed via Dockerfile)
# -------------------------------------------------------------
try:
    from flask import Flask, request, jsonify

    app = Flask(__name__)

    @app.route("/", defaults={"path": ""}, methods=["GET", "POST"])
    @app.route("/<path:path>", methods=["GET", "POST"])
    def catch_all(path):
        data, status_code = handle_routes("/" + path, request.method)
        return jsonify(data), status_code

    if __name__ == "__main__":
        port = int(os.environ.get("PORT", 8080))
        app.run(host="0.0.0.0", port=port)
except ImportError:
    pass
