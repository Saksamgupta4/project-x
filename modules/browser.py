import asyncio
import json
import os

class BrowserManager:
    def __init__(self, config: dict, proxy: dict, db=None, state=None, email=None):
        self.config          = config
        self.proxy           = proxy
        self._state          = state
        self._email          = email
        self.lms_url         = config["lms"]["url"]
        self.lp_id           = config["lms"]["learning_plan_id"]
        self.lp_slug         = config["lms"]["learning_plan_slug"]
        self._last_api_debug = ""
        self._ls_token       = None
        self._db             = db

    def _log(self, msg: str, level: str = "info"):
        print(f"  [browser] {msg}")
        if self._state and self._email:
            self._state.log(msg, level, self._email)

    def _cookie_file(self, email: str) -> str:
        safe = email.replace("@", "_").replace(".", "_")
        return f"cookies_{safe}.json"

    def _save_cookies(self, email: str, cookies: list):
        path = self._cookie_file(email)
        with open(path, "w") as f:
            json.dump(cookies, f, indent=2)
        names = [c["name"] for c in cookies]
        print(f"  💾 Saved {len(cookies)} cookies: {names}")
        if self._db and self._db.enabled:
            self._db.save_cookies(email, cookies)

    def _load_cookies(self, email: str) -> list:
        raw = []
        if self._db and self._db.enabled:
            sb_cookies = self._db.load_cookies(email)
            if sb_cookies:
                print(f"  ☁️ Loaded {len(sb_cookies)} cookies from Supabase")
                raw = sb_cookies
        if not raw:
            path = self._cookie_file(email)
            if os.path.exists(path):
                with open(path) as f:
                    raw = json.load(f)
        if not raw:
            return []
        clean = []
        for c in raw:
            entry = {
                "name":   c["name"],
                "value":  c["value"],
                "domain": c.get("domain", "inco.docebosaas.com"),
                "path":   c.get("path", "/"),
            }
            if c.get("secure"):
                entry["secure"] = True
            clean.append(entry)
        return clean

    async def launch(self, playwright):
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True
        )
        return context, browser

    async def login(self, context, email: str, password: str):
        page = await context.new_page()

        # Try saved cookies first
        saved = self._load_cookies(email)
        if saved:
            print(f"  🍪 Trying {len(saved)} saved cookies")
            await context.add_cookies(saved)
            await page.goto(
                f"{self.lms_url}/learn/learning-plans/{self.lp_id}/{self.lp_slug}",
                wait_until="domcontentloaded", timeout=45000
            )
            await asyncio.sleep(3)
            if "signin" not in page.url and "login" not in page.url:
                print(f"  ✅ Cookie login works!")
                # Capture localStorage token
                try:
                    ls_raw = await page.evaluate(
                        "() => { try { const r=localStorage.getItem('access_token'); return r?JSON.parse(r).access_token:null; } catch(e){return null;} }"
                    )
                    if ls_raw:
                        self._ls_token = ls_raw
                        self._log(f"✅ localStorage token from cookies: {ls_raw[:10]}...")
                except:
                    pass
                return page
            print(f"  ⚠️ Cookies invalid — fresh login")
            await context.clear_cookies()

        # Fresh login
        print(f"  🔑 Logging in: {email}")
        await page.goto(f"{self.lms_url}/login", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(2)

        # Accept cookie banner
        try:
            accept = await page.wait_for_selector("button:has-text('ACCEPT')", timeout=3000)
            if accept:
                await accept.click()
                await asyncio.sleep(1)
        except:
            pass

        # Fill username
        username_selectors = [
            "input[placeholder*='sername']",
            "input[name='login[username]']",
            "input[name='username']",
            "input[type='text']:not([name*='search'])",
            "input[name='login[email]']",
            "input[type='email']",
        ]
        filled = False
        for sel in username_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    await page.fill(sel, email)
                    filled = True
                    print(f"  ✅ Username filled: {sel}")
                    break
            except:
                continue

        if not filled:
            try:
                await page.evaluate(f"""
                    const inputs = document.querySelectorAll('input');
                    for (const inp of inputs) {{
                        if (inp.type !== 'password' && inp.type !== 'hidden') {{
                            inp.value = '{email}';
                            inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                            inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                            break;
                        }}
                    }}
                """)
                print(f"  ✅ Username filled via JS")
            except Exception as e:
                print(f"  ❌ Could not fill username: {e}")

        await asyncio.sleep(0.5)

        # Fill password
        try:
            await page.fill("input[type='password']", password)
            print(f"  ✅ Password filled")
        except Exception as e:
            print(f"  ❌ Could not fill password: {e}")

        await asyncio.sleep(0.5)

        # Click submit
        try:
            await page.click("button:has-text('SIGN IN')")
            print(f"  ✅ Clicked SIGN IN")
        except:
            for sel in ["button[type='submit']", "input[type='submit']"]:
                try:
                    await page.click(sel)
                    print(f"  ✅ Clicked submit: {sel}")
                    break
                except:
                    continue

        await page.wait_for_load_state("networkidle", timeout=45000)
        await asyncio.sleep(3)

        self._log(f"After login URL: {page.url}")

        if "login" in page.url and "pages" not in page.url and "learn" not in page.url:
            raise Exception(f"Login failed for {email}")

        # Capture localStorage token immediately after login
        self._ls_token = None
        try:
            ls_raw = await page.evaluate(
                "() => { try { const r=localStorage.getItem('access_token'); return r?JSON.parse(r).access_token:null; } catch(e){return null;} }"
            )
            if ls_raw:
                self._ls_token = ls_raw
                self._log(f"✅ localStorage token captured: {ls_raw[:10]}...")
            else:
                self._log("ℹ️ No localStorage token after login")
        except Exception as e:
            self._log(f"localStorage error: {e}")

        # Save cookies
        cookies = await context.cookies(["https://inco.docebosaas.com"])
        self._save_cookies(email, cookies)

        self._log(f"✅ Login successful! Cookies: {[c['name'] for c in cookies]}")
        return page

    async def get_course_statuses(self, page) -> list:
        try:
            # Navigate to LP page
            lp_url = f"{self.lms_url}/learn/learning-plans/{self.lp_id}/{self.lp_slug}"
            await page.goto(lp_url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(3)

            self._log(f"LP page URL: {page.url}")

            if "signin" in page.url or "login" in page.url:
                self._last_api_debug = "Redirected to signin"
                return []

            # Get token - cookies first
            cookies = await page.context.cookies()
            token = next(
                (c["value"] for c in cookies if c["name"] == "hydra_access_token"),
                None
            )

            # Then localStorage
            if not token:
                try:
                    ls_token = await page.evaluate(
                        "() => { try { const r=localStorage.getItem('access_token'); return r?JSON.parse(r).access_token:null; } catch(e){return null;} }"
                    )
                    if ls_token:
                        token = ls_token
                        self._log(f"✅ Token from localStorage")
                except:
                    pass

            # Then saved _ls_token from login
            if not token and self._ls_token:
                token = self._ls_token
                self._log(f"✅ Using saved login token")

            self._log(f"Token: {'yes ' + token[:10] if token else 'NONE'}")

            api_url = f"{self.lms_url}/learn/v1/lp/{self.lp_id}?get_courses_instructors=1"
            headers = {"Accept": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            response = await page.context.request.get(api_url, headers=headers)
            token_src = "cookie" if next((c for c in cookies if c["name"] == "hydra_access_token"), None) else "localStorage" if token else "none"
            self._last_api_debug = f"HTTP:{response.status} token={token_src}"

            if response.status == 403:
                self._last_api_debug += f" body:{(await response.text())[:200]}"
                # Retry once
                await asyncio.sleep(3)
                cookies = await page.context.cookies()
                token = next((c["value"] for c in cookies if c["name"] == "hydra_access_token"), None)
                if not token:
                    try:
                        ls_raw = await page.evaluate(
                            "() => { try { const r=localStorage.getItem('access_token'); return r?JSON.parse(r).access_token:null; } catch(e){return null;} }"
                        )
                        if ls_raw:
                            token = ls_raw
                    except:
                        pass
                if not token and self._ls_token:
                    token = self._ls_token
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                response = await page.context.request.get(api_url, headers=headers)
                self._last_api_debug = f"Retry HTTP:{response.status} token={'yes' if token else 'no'}"

            if response.status != 200:
                self._last_api_debug += f" body:{(await response.text())[:200]}"
                return []

            data = await response.json()
            courses = data.get("data", {}).get("courses", [])
            self._last_api_debug += f" courses:{len(courses)}"
            return courses

        except Exception as e:
            self._last_api_debug = f"Exception: {str(e)[:100]}"
            return []

    def get_module_url(self, course_id: int, slug: str) -> str:
        return (
            f"{self.lms_url}/learn/learning-plans/{self.lp_id}"
            f"/{self.lp_slug}/courses/{course_id}/{slug}/lessons"
        )
