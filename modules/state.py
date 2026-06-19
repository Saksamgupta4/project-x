import threading
from datetime import datetime

class StateManager:
    def __init__(self, accounts: list, courses: list):
        self._state   = {}
        self._logs    = []
        self._sched   = {}
        self._control = {"running": False, "paused": False, "stop_requested": False}
        self._lock    = threading.Lock()
        self._courses = courses
        for acc in accounts:
            email = acc["email"]
            self._state[email] = {
                "email": email, "name": acc.get("name", email.split("@")[0]),
                "status": "queued", "status_label": "Ready",
                "proxy": acc.get("proxy_country", "in"),
                "modules_done": [], "progress": 0,
                "current_module": "", "current_lesson": 0,
                "total_lessons": 0, "time_spent": ""
            }

    def update(self, email: str, **kwargs):
        with self._lock:
            if email not in self._state:
                self._state[email] = {"email": email}
            self._state[email].update(kwargs)

    def get(self, email: str) -> dict:
        return self._state.get(email, {})

    def get_all(self) -> list:
        return list(self._state.values())

    def log(self, msg: str, level: str = "info", email: str = "system"):
        with self._lock:
            entry = {
                "time":    datetime.now().strftime("%H:%M:%S"),
                "email":   email,
                "message": msg,
                "level":   level
            }
            self._logs.append(entry)
            if len(self._logs) > 500:
                self._logs = self._logs[-500:]

    def get_logs(self, n: int = 200) -> list:
        return self._logs[-n:]

    def set_schedule(self, email: str, modules: list):
        self._sched[email] = modules

    def get_schedule(self) -> dict:
        return self._sched

    def is_running(self) -> bool:
        return self._control["running"]

    def is_paused(self) -> bool:
        return self._control["paused"]

    def is_stop_requested(self) -> bool:
        return self._control["stop_requested"]

    def start(self):
        with self._lock:
            self._control.update({"running": True, "paused": False, "stop_requested": False})

    def pause(self):
        with self._lock:
            self._control["paused"] = True

    def resume(self):
        with self._lock:
            self._control["paused"] = False

    def stop(self):
        with self._lock:
            self._control.update({"running": False, "stop_requested": True})

    def remove_account(self, email: str):
        with self._lock:
            self._state.pop(email, None)

    def reset_accounts(self, accounts: list):
        with self._lock:
            for acc in accounts:
                email = acc["email"]
                if email not in self._state:
                    self._state[email] = {
                        "email": email,
                        "name": acc.get("name", email.split("@")[0]),
                        "status": "queued", "status_label": "Ready",
                        "proxy": acc.get("proxy_country", "in"),
                        "modules_done": [], "progress": 0
                    }

    def export_csv(self) -> str:
        lines = ["Email,Name,Status,Modules Done,Proxy"]
        for a in self._state.values():
            done = "|".join(a.get("modules_done", []))
            lines.append(f"{a.get('email','')},{a.get('name','')},{a.get('status','')},{done},{a.get('proxy','')}")
        return "\n".join(lines)
