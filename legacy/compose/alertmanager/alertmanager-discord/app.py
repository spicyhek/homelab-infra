from flask import Flask, request, jsonify
import os
import requests

app = Flask(__name__)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

@app.post("/alert")
def alert():
    if not DISCORD_WEBHOOK_URL:
        return jsonify({"error": "DISCORD_WEBHOOK_URL not set"}), 500

    payload = request.get_json(force=True, silent=True) or {}
    alerts = payload.get("alerts", [])

    if not alerts:
        msg = "(Alertmanager webhook received, but no alerts were present.)"
    else:
        parts = []
        for a in alerts:
            status = a.get("status", "unknown").upper()
            labels = a.get("labels", {})
            ann = a.get("annotations", {})

            name = labels.get("alertname", "Alert")
            severity = labels.get("severity", "unknown")
            instance = labels.get("instance", "")
            summary = ann.get("summary", "")
            desc = ann.get("description", "")

            parts.append(
                f"**{status}** `{name}` (severity: `{severity}`)\n"
                f"instance: `{instance}`\n"
                f"{summary}\n{desc}".strip()
            )

        msg = "\n\n---\n\n".join(parts)

    r = requests.post(DISCORD_WEBHOOK_URL, json={"content": msg[:1900]})
    if r.status_code >= 300:
        return jsonify({"error": "Discord rejected", "status": r.status_code, "body": r.text}), 502

    return jsonify({"ok": True})

@app.get("/healthz")
def healthz():
    return "ok", 200
