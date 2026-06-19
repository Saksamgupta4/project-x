import json
import os
import threading
import io
from flask import Flask, render_template, jsonify, request, send_file
from flask_socketio import SocketIO

app      = Flask(__name__)
app.config["SECRET_KEY"] = "greenpath-2024"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

INSTANCE_ID = os.environ.get("INSTANCE_ID", "default")
_state      = None
_db         = None
_simulator  = None
_custom_sched = {}

def init_dashboard(state, db=None):
    global _state, _db
    _state = state
    _db    = db

def set_simulator_runner(fn):
    global _simulator
    _simulator = fn

def push_update():
    if _state:
        try:
            socketio.emit("state_update", {
                "accounts": _state.get_all(),
                "logs":     _state.get_logs(200),
                "schedule": _state.get_schedule(),
                "running":  _state.is_running()
            })
        except:
            pass

def _load_config():
    env = os.environ.get("SCORM_CONFIG")
    if env:
        return json.loads(env)
    with open("config.json") as f:
        return json.load(f)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "instance": INSTANCE_ID})

@app.route("/api/state")
def get_state():
    if not _state:
        return jsonify({"error": "not ready"})
    accounts = _state.get_all()
    if not accounts and _db and _db.enabled:
        sb = _db.get_accounts(instance_id=INSTANCE_ID)
        accounts = [{
            "email": a["email"], "name": a.get("name", a["email"].split("@")[0]),
            "status": "queued", "status_label": "Ready",
            "proxy": a.get("proxy_country", "in"), "modules_done": [], "progress": 0
        } for a in sb]
    return jsonify({
        "accounts": accounts, "logs": _state.get_logs(200),
        "schedule": _state.get_schedule(), "running": _state.is_running()
    })

@app.route("/api/config")
def get_config():
    try:
        cfg      = _load_config()
        accounts = list(cfg.get("accounts", []))
        if _db and _db.enabled:
            sb = _db.get_accounts(instance_id=INSTANCE_ID)
            existing = {a["email"] for a in accounts}
            for a in sb:
                if a["email"] not in existing:
                    accounts.append(a)
        return jsonify({"accounts": accounts, "courses": cfg["courses"]})
    except Exception as e:
        return jsonify({"error": str(e), "accounts": [], "courses": []})

@app.route("/api/accounts", methods=["GET"])
def get_accounts():
    if _db and _db.enabled:
        return jsonify(_db.get_accounts(instance_id=INSTANCE_ID))
    return jsonify([])

@app.route("/api/accounts", methods=["POST"])
def add_account():
    data     = request.json or {}
    email    = data.get("email", "").strip().lower()
    password = data.get("password", "")
    proxy    = data.get("proxy_country", "in")
    name     = data.get("name", email.split("@")[0])
    modules     = data.get("modules", [])
    instance_id = data.get("instance_id", INSTANCE_ID)  # Allow specifying batch
    if not email or not password:
        return jsonify({"ok": False, "error": "Email and password required"})
    if _db and _db.enabled:
        ok = _db.add_account(email, password, proxy, name, modules, instance_id=instance_id)
        if ok:
            cfg = _load_config()
            _db.init_progress(email, cfg["courses"])
            if _state:
                _state.update(email, name=name, proxy=proxy, status="queued", status_label="Ready")
                push_update()
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Failed (may already exist)"})
    return jsonify({"ok": False, "error": "Database not configured"})

@app.route("/api/accounts/<email>", methods=["DELETE"])
def delete_account(email):
    if _db and _db.enabled:
        _db.delete_account(email)
    import glob
    safe = email.replace("@","_").replace(".","_")
    for f in glob.glob(f"cookies_{safe}*.json"):
        try: os.remove(f)
        except: pass
    if _state:
        _state.remove_account(email)
        push_update()
    return jsonify({"ok": True})

@app.route("/api/schedule", methods=["POST"])
def save_schedule():
    global _custom_sched
    _custom_sched = (request.json or {}).get("schedule", {})
    return jsonify({"ok": True})

@app.route("/api/control/start_one", methods=["POST"])
def start_one():
    if not _simulator:
        return jsonify({"ok": False, "error": "not ready"})
    email = (request.json or {}).get("email", "")
    if not email:
        return jsonify({"ok": False, "error": "email required"})
    def run():
        try:
            _simulator(custom_schedule=_custom_sched, single_email=email)
        except Exception as e:
            if _state:
                _state.log(f"Error: {e}", "error", email)
                push_update()
    threading.Thread(target=run, daemon=True).start()
    if _state:
        _state.log(f"▶️ Starting: {email}", "success")
        push_update()
    return jsonify({"ok": True})

@app.route("/api/control/<action>", methods=["POST"])
def control(action):
    if not _state:
        return jsonify({"error": "not ready"})
    if action == "start":
        _state.start()
        sched = _custom_sched if _custom_sched else None
        threading.Thread(target=_simulator, kwargs={"custom_schedule": sched}, daemon=True).start()
        _state.log("▶️ Simulator started!", "success")
    elif action == "pause":
        _state.pause()
        _state.log("⏸️ Paused", "warning")
    elif action == "resume":
        _state.resume()
        _state.log("▶️ Resumed", "success")
    elif action == "stop":
        _state.stop()
        _state.log("🛑 Stopped", "error")
    push_update()
    return jsonify({"ok": True})

@app.route("/api/export")
def export_csv():
    if not _state:
        return jsonify({"error": "not ready"})
    return send_file(io.BytesIO(_state.export_csv().encode()),
        mimetype="text/csv", as_attachment=True, download_name="report.csv")

# ── Master Dashboard API ──────────────────────────────────────
@app.route("/api/master")
def master():
    """Returns all accounts across all instances for master view"""
    if not _db or not _db.enabled:
        return jsonify({"error": "db not configured"})
    all_accounts = _db.get_all_accounts_master()
    all_progress = _db.get_all_progress()
    progress_map = {}
    for p in all_progress:
        e = p["email"]
        if e not in progress_map:
            progress_map[e] = []
        progress_map[e].append(p)
    result = []
    for acc in all_accounts:
        email    = acc["email"]
        progress = progress_map.get(email, [])
        done     = [p["course_name"] for p in progress if p["status"] == "completed"]
        running  = [p for p in progress if p["status"] == "running"]
        result.append({
            "email":       email,
            "name":        acc.get("name", email.split("@")[0]),
            "instance_id": acc.get("instance_id", "default"),
            "proxy":       acc.get("proxy_country", "in"),
            "modules_done": done,
            "status":      "running" if running else ("completed" if len(done) >= 3 else "in_progress")
        })
    return jsonify(result)

def run_dashboard(host="0.0.0.0", port=8080):
    socketio.run(app, host=host, port=port, debug=False,
                 allow_unsafe_werkzeug=True, log_output=False)