import os
import json
import threading

GCP_PROJECT = os.getenv("GCP_PROJECT", "opes-robinhood-executor")
FIRESTORE_COLLECTION = os.getenv("FIRESTORE_COLLECTION", "trades_v2")

# Start background trader thread if executor.py exists
try:
    import executor
    threading.Thread(target=executor.run_loop, daemon=True).start()
except Exception as e:
    print(f"Executor startup notice: {e}")

def handle_routes(path: str, method: str):
    clean = path.rstrip("/") or "/"

    if clean == "/healthz":
        return {
            "status": "healthy",
            "service": "opes-executor-2575",
            "project": GCP_PROJECT,
            "collection": FIRESTORE_COLLECTION
        }, 200

    if clean == "/api/trades":
        try:
            from google.cloud import firestore
            db = firestore.Client(project=GCP_PROJECT)
            docs = db.collection(FIRESTORE_COLLECTION).limit(100).stream()
            results = [dict(id=d.id, **d.to_dict()) for d in docs]
            return {"trades": results, "project": GCP_PROJECT, "collection": FIRESTORE_COLLECTION}, 200
        except Exception as e:
            return {"error": str(e)}, 500

    if clean == "/api/sync":
        try:
            if "executor" in globals():
                executor.process_sync()
            return {"status": "success"}, 200
        except Exception as e:
            return {"error": str(e)}, 500

    return {
        "service": "opes-executor-2575",
        "project": GCP_PROJECT,
        "collection": FIRESTORE_COLLECTION,
        "status": "online"
    }, 200

# 1. Entry point for Cloud Run Functions / Buildpacks
try:
    import functions_framework

    @functions_framework.http
    def main(request):
        data, status = handle_routes(request.path, request.method)
        return (json.dumps(data), status, {"Content-Type": "application/json"})
    
    hello_http = main
except ImportError:
    pass

# 2. Entry point for Dockerfile / Gunicorn
try:
    from flask import Flask, request, jsonify

    app = Flask(__name__)

    @app.route("/", defaults={"path": ""}, methods=["GET", "POST"])
    @app.route("/<path:path>", methods=["GET", "POST"])
    def catch_all(path):
        data, status = handle_routes("/" + path, request.method)
        return jsonify(data), status

    if __name__ == "__main__":
        port = int(os.environ.get("PORT", 8080))
        app.run(host="0.0.0.0", port=port)
except ImportError:
    pass
