import asyncio
import json
import os
import random
import sys
import threading
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

sys.path.insert(0, ".")
from modules.state      import StateManager
from modules.proxy      import ProxyManager
from modules.browser    import BrowserManager
from modules.scorm      import SCORMSimulator
from modules.supabase_db import SupabaseDB
from dashboard.app      import init_dashboard, set_simulator_runner, run_dashboard, push_update

# ── Config ────────────────────────────────────────────────────
def load_config():
    env = os.environ.get("SCORM_CONFIG")
    if env:
        cfg = json.loads(env)
    else:
        with open("config.json") as f:
            cfg = json.load(f)
    if os.environ.get("NORDVPN_USER"):
        cfg["proxy"]["username"] = os.environ["NORDVPN_USER"]
    if os.environ.get("NORDVPN_PASS"):
        cfg["proxy"]["password"] = os.environ["NORDVPN_PASS"]
    return cfg

CONFIG      = load_config()
COURSES     = CONFIG["courses"]
SIM         = CONFIG["simulation"]
TEST_MODE   = CONFIG.get("test_mode", {})
SPEED       = TEST_MODE.get("speed_multiplier", 3600) if TEST_MODE.get("enabled") else 1
INSTANCE_ID = os.environ.get("INSTANCE_ID", "default")

db    = SupabaseDB()
ACCS  = []
state = None

def real_wait(s): return s / SPEED

def get_accounts():
    accounts = list(CONFIG.get("accounts", []))
    if db.enabled:
        sb = db.get_accounts(instance_id=INSTANCE_ID)
        existing = {a["email"] for a in accounts}
        for a in sb:
            if a["email"] not in existing:
                accounts.append(a)
    return accounts

def _schedule_next(email, current_course_id, custom_schedule=None):
    idx = next((i for i, c in enumerate(COURSES) if c["id"] == current_course_id), None)
    if idx is None or idx + 1 >= len(COURSES):
        state.log(f"🎉 All modules done!", "success", email)
        state.update(email, status="completed", status_label="✅ All Done!")
        return
    next_course = COURSES[idx + 1]
    days        = random.randint(SIM.get("days_between_modules_min", 4),
                                 SIM.get("days_between_modules_max", 4))
    hours       = random.randint(8, 20)
    next_run    = datetime.utcnow() + timedelta(days=days, hours=hours)
    db.schedule_next_module_at(email, next_course["id"], next_run)
    display = next_run + timedelta(hours=5, minutes=30) if os.environ.get("RAILWAY_ENVIRONMENT") else next_run
    state.log(f"⏰ Next: {next_course['name']} at {display.strftime('%b %d %H:%M IST')}", "info", email)
    state.update(email, status="waiting", status_label=f"⏰ {next_course['name']}")

async def process_account(account, playwright, custom_schedule=None):
    email    = account["email"]
    password = account["password"]
    country  = account.get("proxy_country", "in")

    next_mod = db.get_next_module(email) if db.enabled else None
    if not next_mod:
        state.update(email, status="completed", status_label="✅ All Done!")
        return False

    course_id = next_mod["course_id"]
    course    = next((c for c in COURSES if c["id"] == course_id), None)
    if not course:
        return False

    state.update(email, status="running", current_module=course["name"],
                 status_label="🌐 Starting", started_at=datetime.now().strftime("%H:%M:%S"))
    state.log(f"Starting via {country.upper()} proxy", "info", email)
    push_update()

    proxy_mgr   = ProxyManager(CONFIG)
    proxy       = proxy_mgr.get_proxy(country)
    browser_mgr = BrowserManager(CONFIG, proxy, db=db)
    context, browser = None, None

    try:
        context, browser = await browser_mgr.launch(playwright)
        page = await browser_mgr.login(context, email, password)
        state.log("✅ Login successful!", "success", email)
        push_update()

        courses_status = await browser_mgr.get_course_statuses(page)
        debug = getattr(browser_mgr, "_last_api_debug", "")
        state.log(f"Got {len(courses_status)} courses | {debug}", "info", email)

        # Find correct module to run
        target_course = None
        target_current = None

        for c in COURSES:
            cur = next((x for x in courses_status if x["idCourse"] == c["id"]), None)
            if not cur:
                continue
            status    = cur.get("status", "")
            can_enter = cur.get("can_enter", True)
            if status == "completed":
                db.mark_module_done(email, c["id"])
                continue
            if status == "locked" or not can_enter:
                continue
            target_course  = c
            target_current = cur
            break

        if len(courses_status) == 0:
            state.log("⚠️ API returned 0 courses — retrying in 30min", "warning", email)
            db.mark_module_failed(email, course_id)
            return False

        if not target_course:
            state.log("🎉 All modules completed!", "success", email)
            state.update(email, status="completed", status_label="✅ All Done!")
            return True

        completed_les = target_current.get("competed_lessons", 0)
        state.log(f"▶ Running: {target_course['name']} ({target_current.get('status')} {completed_les}/{target_current.get('all_lessons',0)})", "info", email)
        db.mark_module_running(email, target_course["id"])

        module_url = browser_mgr.get_module_url(target_course["id"], target_course["slug"])
        state.update(email, current_module=target_course["name"], status_label=f"📖 {target_course['name']}")
        state.log(f"🚀 Starting: {target_course['name']}", "info", email)
        push_update()

        scorm   = SCORMSimulator(page, CONFIG, state, email, speed=SPEED)
        success = await scorm.run_module(module_url, target_course, completed_lessons=completed_les)

        if success:
            done = state.get(email).get("modules_done", [])
            if target_course["name"] not in done:
                done.append(target_course["name"])
            state.update(email, modules_done=done)
            state.log(f"✅ Completed: {target_course['name']}", "success", email)
            db.mark_module_done(email, target_course["id"])
            _schedule_next(email, target_course["id"], custom_schedule)
        else:
            state.log(f"❌ Failed: {target_course['name']}", "error", email)
            db.mark_module_failed(email, target_course["id"])

        push_update()
        return success

    except Exception as e:
        state.log(f"ERROR: {e}", "error", email)
        db.mark_module_failed(email, course_id)
        return False
    finally:
        if context:
            try: await context.close()
            except: pass
        if browser:
            try: await browser.close()
            except: pass

async def queue_worker(custom_schedule=None, single_email=None):
    global ACCS
    ACCS = get_accounts()
    for acc in ACCS:
        state.update(acc["email"],
            name=acc.get("name", acc["email"].split("@")[0]),
            proxy=acc.get("proxy_country", "in"),
            status="queued", status_label="Ready")
        if db.enabled:
            db.init_progress(acc["email"], COURSES)

    if single_email:
        acc = next((a for a in ACCS if a["email"] == single_email), None)
        if not acc:
            state.log(f"❌ Not found: {single_email}", "error")
            return
        state.log(f"▶️ Starting: {single_email}", "success")
        push_update()
        async with async_playwright() as pw:
            await process_account(acc, pw, custom_schedule)
        push_update()
        return

    mode = f"⚡ TEST ({SPEED}x)" if TEST_MODE.get("enabled") else "🕐 Real"
    state.log(f"🚀 Queue started! {len(ACCS)} accounts | {mode} | Instance: {INSTANCE_ID}", "success")
    push_update()

    async with async_playwright() as pw:
        while not state.is_stop_requested():
            while state.is_paused():
                await asyncio.sleep(5)

            if not db.enabled:
                for acc in ACCS:
                    if state.is_stop_requested(): break
                    await process_account(acc, pw, custom_schedule)
                break

            ready = db.get_ready_accounts(COURSES, max_concurrent=1, instance_id=INSTANCE_ID)
            state.log(f"Queue check: {len(ready)} ready | db={db.enabled}", "info")

            if ready:
                email = ready[0]
                acc   = next((a for a in ACCS if a["email"] == email), None)
                if not acc:
                    # Account might be new, refresh list
                    ACCS = get_accounts()
                    acc  = next((a for a in ACCS if a["email"] == email), None)
                if acc:
                    state.log(f"▶ Running: {email}", "info")
                    push_update()
                    await process_account(acc, pw, custom_schedule)
                    push_update()
                    await asyncio.sleep(5)
                else:
                    await asyncio.sleep(30)
            else:
                # Show next scheduled
                try:
                    next_rows = db._get("account_progress",
                        f"status=eq.pending&order=next_run_at.asc&limit=1")
                    if next_rows:
                        nr = next_rows[0]
                        # Filter by instance
                        accs_check = db._get("accounts", f"email=eq.{nr['email']}&select=instance_id")
                        if accs_check and accs_check[0].get("instance_id") == INSTANCE_ID:
                            state.log(f"Next: {nr['email'].split('@')[0]} at {nr['next_run_at'][:16]}", "info")
                    else:
                        state.log("No pending modules", "info")
                except:
                    pass
                push_update()
                await asyncio.sleep(60)

    state.log("✅ Queue stopped", "success")
    push_update()

def simulator_thread(custom_schedule=None, single_email=None):
    asyncio.run(queue_worker(custom_schedule=custom_schedule, single_email=single_email))

if __name__ == "__main__":
    global state
    ACCS  = get_accounts()
    state = StateManager(ACCS, COURSES)

    for acc in ACCS:
        state.update(acc["email"],
            name=acc.get("name", acc["email"].split("@")[0]),
            proxy=acc.get("proxy_country", "in"),
            status="queued", status_label="Ready")

    init_dashboard(state, db=db)
    set_simulator_runner(simulator_thread)

    port = int(os.environ.get("PORT", 8080))
    print(f"\n🌿 GREENPATH | Instance: {INSTANCE_ID} | Accounts: {len(ACCS)} | Port: {port}")
    run_dashboard(port=port)
