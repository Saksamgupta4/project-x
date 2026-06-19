import json
import os
import random
from datetime import datetime, timedelta


class SupabaseDB:
    def __init__(self):
        self.url     = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self.key     = os.environ.get("SUPABASE_KEY", "")
        self.enabled = bool(self.url and self.key)
        if self.enabled:
            print(f"✅ Supabase: {self.url[:40]}...")
        else:
            print("⚠️  Supabase not configured")

    def _headers(self):
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }

    def _get(self, table, params=""):
        import urllib.request
        url = f"{self.url}/rest/v1/{table}?{params}"
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def _post(self, table, data):
        import urllib.request
        url  = f"{self.url}/rest/v1/{table}"
        body = json.dumps(data).encode()
        req  = urllib.request.Request(url, data=body, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read() or b"[]")
        except:
            return None

    def _patch(self, table, params, data):
        import urllib.request
        url     = f"{self.url}/rest/v1/{table}?{params}"
        body    = json.dumps(data).encode()
        headers = {**self._headers(), "Prefer": "return=representation"}
        req     = urllib.request.Request(url, data=body, headers=headers, method="PATCH")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read() or b"[]")
        except:
            return None

    def _delete(self, table, params):
        import urllib.request
        url = f"{self.url}/rest/v1/{table}?{params}"
        req = urllib.request.Request(url, headers=self._headers(), method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=10):
                return True
        except:
            return False

    def _now(self):
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── Accounts ─────────────────────────────────────────────
    def get_accounts(self, instance_id=None):
        if not self.enabled:
            return []
        try:
            if instance_id:
                return self._get("accounts", f"instance_id=eq.{instance_id}&order=created_at.asc")
            return self._get("accounts", "order=created_at.asc")
        except:
            return []

    def add_account(self, email, password, proxy_country, name, modules, instance_id="default"):
        if not self.enabled:
            return False
        try:
            self._post("accounts", {
                "email": email, "password": password,
                "proxy_country": proxy_country, "name": name,
                "modules": json.dumps(modules), "instance_id": instance_id
            })
            return True
        except:
            return False

    def delete_account(self, email):
        if not self.enabled:
            return False
        try:
            self._delete("accounts", f"email=eq.{email}")
            self._delete("account_cookies", f"email=eq.{email}")
            self._delete("account_progress", f"email=eq.{email}")
            return True
        except:
            return False

    # ── Cookies ───────────────────────────────────────────────
    def save_cookies(self, email, cookies):
        if not self.enabled:
            return
        try:
            import urllib.request
            url     = f"{self.url}/rest/v1/account_cookies"
            headers = {**self._headers(), "Prefer": "resolution=merge-duplicates"}
            body    = json.dumps({"email": email, "cookies": json.dumps(cookies),
                                  "updated_at": self._now()}).encode()
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            urllib.request.urlopen(req, timeout=10)
        except:
            pass

    def load_cookies(self, email):
        if not self.enabled:
            return []
        try:
            rows = self._get("account_cookies", f"email=eq.{email}&select=cookies")
            if rows:
                raw = rows[0].get("cookies", "[]")
                return json.loads(raw) if isinstance(raw, str) else (raw or [])
        except:
            pass
        return []

    # ── Progress ──────────────────────────────────────────────
    def get_progress(self, email):
        if not self.enabled:
            return []
        try:
            return self._get("account_progress", f"email=eq.{email}&order=course_id.asc")
        except:
            return []

    def init_progress(self, email, courses):
        if not self.enabled:
            return
        existing    = {r["course_id"] for r in self.get_progress(email)}
        FAR_FUTURE  = "2099-01-01T00:00:00Z"
        for idx, course in enumerate(courses):
            if course["id"] not in existing:
                try:
                    self._post("account_progress", {
                        "email": email, "course_id": course["id"],
                        "course_name": course["name"], "status": "pending",
                        "next_run_at": self._now() if idx == 0 else FAR_FUTURE,
                        "attempts": 0
                    })
                except:
                    pass

    def claim_next_account(self, instance_id, courses):
        """
        Atomically claim next account ready to run for this instance.
        Returns email or None.
        """
        if not self.enabled:
            return None
        try:
            now  = self._now()
            rows = self._get("account_progress",
                f"status=eq.pending&next_run_at=lte.{now}&order=next_run_at.asc&limit=10")
            for row in rows:
                email = row["email"]
                # Check if this account belongs to this instance
                accs = self._get("accounts", f"email=eq.{email}&select=instance_id")
                if not accs:
                    continue
                if accs[0].get("instance_id") != instance_id:
                    continue
                # Try to claim it (set to running)
                result = self._patch("account_progress",
                    f"email=eq.{email}&course_id=eq.{row['course_id']}&status=eq.pending",
                    {"status": "running", "started_at": self._now()})
                if result:
                    return email
            return None
        except Exception as e:
            print(f"claim_next error: {e}")
            return None

    def get_ready_accounts(self, courses, max_concurrent=1, instance_id=None):
        if not self.enabled:
            return []
        try:
            now  = self._now()
            rows = self._get("account_progress",
                f"status=eq.pending&next_run_at=lte.{now}&order=next_run_at.asc")
            seen = []
            for row in rows:
                email = row["email"]
                if email in seen:
                    continue
                if instance_id:
                    accs = self._get("accounts", f"email=eq.{email}&select=instance_id")
                    if not accs or accs[0].get("instance_id") != instance_id:
                        continue
                seen.append(email)
                if len(seen) >= max_concurrent:
                    break
            return seen
        except Exception as e:
            print(f"get_ready_accounts error: {e}")
            return []

    def get_next_module(self, email):
        if not self.enabled:
            return None
        try:
            now  = self._now()
            rows = self._get("account_progress",
                f"email=eq.{email}&status=eq.pending&next_run_at=lte.{now}&order=course_id.asc&limit=1")
            return rows[0] if rows else None
        except:
            return None

    def get_next_module_any(self, email):
        if not self.enabled:
            return None
        try:
            rows = self._get("account_progress",
                f"email=eq.{email}&status=eq.pending&order=next_run_at.asc&limit=1")
            return rows[0] if rows else None
        except:
            return None

    def mark_module_running(self, email, course_id):
        if not self.enabled:
            return
        try:
            self._patch("account_progress",
                f"email=eq.{email}&course_id=eq.{course_id}",
                {"status": "running", "started_at": self._now()})
        except:
            pass

    def mark_module_done(self, email, course_id):
        if not self.enabled:
            return
        try:
            self._patch("account_progress",
                f"email=eq.{email}&course_id=eq.{course_id}",
                {"status": "completed", "completed_at": self._now()})
        except:
            pass

    def mark_module_failed(self, email, course_id, locked=False):
        if not self.enabled:
            return
        try:
            rows     = self._get("account_progress",
                f"email=eq.{email}&course_id=eq.{course_id}&select=attempts")
            attempts = (rows[0]["attempts"] if rows else 0) + 1
            retry    = (datetime.utcnow() + timedelta(hours=24 if locked else 0, minutes=0 if locked else 30)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._patch("account_progress",
                f"email=eq.{email}&course_id=eq.{course_id}",
                {"status": "pending", "next_run_at": retry, "attempts": attempts})
        except:
            pass

    def schedule_next_module_at(self, email, next_course_id, run_at):
        if not self.enabled:
            return
        try:
            self._patch("account_progress",
                f"email=eq.{email}&course_id=eq.{next_course_id}",
                {"status": "pending", "next_run_at": run_at.strftime("%Y-%m-%dT%H:%M:%SZ")})
        except:
            pass

    # ── Logs ──────────────────────────────────────────────────
    def save_log(self, email, message, level="info"):
        if not self.enabled:
            return
        try:
            self._post("sim_logs", {"email": email, "message": message, "level": level})
        except:
            pass

    # ── Live state ────────────────────────────────────────────
    def save_live_state(self, instance_id, state_data):
        if not self.enabled:
            return
        try:
            self.save_cookies(f"_live_{instance_id}", [{"name": "state", "value": json.dumps(state_data)}])
        except:
            pass

    # ── Master dashboard ──────────────────────────────────────
    def get_all_progress(self):
        """Get progress for ALL accounts across all instances"""
        if not self.enabled:
            return []
        try:
            return self._get("account_progress", "order=email.asc")
        except:
            return []

    def get_all_accounts_master(self):
        """Get all accounts for master dashboard"""
        if not self.enabled:
            return []
        try:
            return self._get("accounts", "order=created_at.asc")
        except:
            return []
