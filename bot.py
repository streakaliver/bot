import os
import sys
import json
import time
import random
import traceback
import urllib.request
import urllib.parse
import html
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, Response, Page, BrowserContext


class ActivityLogger:
    """Thread-safe-styled cleaner logger to record execution timeline cleanly."""
    def __init__(self):
        self.logs = []

    def info(self, message: str):
        clean_msg = str(message).encode('ascii', 'backslashreplace').decode('ascii')
        timestamp = time.strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {clean_msg}"
        self.logs.append(formatted)
        print(formatted)

    def get_log_string(self) -> str:
        return "\n".join(self.logs)


logger = ActivityLogger()


class ChorchaQuizBot:
    def __init__(self):
        # Configuration Fallbacks
        self.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "xxxxxxxxxxx")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "xxxxxxxxxxxx1")
        self.phone = os.environ.get("CHORCHA_PHONE", "xxxxxxx")
        self.password = os.environ.get("CHORCHA_PASS", "xxxxxxx")
        self.auth_file = Path(__file__).parent / "cookie.json"
        
        # Populating cookies from environment secret if present
        env_cookies = os.environ.get("CHORCHA_COOKIES")
        if env_cookies and env_cookies.strip():
            try:
                parsed_env_cookies = json.loads(env_cookies)
                with open(self.auth_file, "w", encoding="utf-8") as f:
                    json.dump(parsed_env_cookies, f, indent=4)
                logger.info("Successfully populated cookie.json from GITHUB_SECRET context.")
            except Exception as e:
                logger.info(f"Failed parsing cookies from environment: {e}")
        
        # Runtime Operational States
        self.correct_options = {}
        self.decoded_answers_text = {}
        self.selected_subject = "Unknown"
        self.selected_chapter = "Unknown"
        self.question_count = 0
        self.streak_captured = False
        self.streak_image_path = None
        self.collected_screenshots = []

    def send_telegram_report(self, success: bool, error_msg: str = None, traceback_str: str = None):
        """Assembles all collected screenshots and metadata, and dispatches a single media group or text message to Telegram."""
        subject_esc = html.escape(self.selected_subject)
        chapter_esc = html.escape(self.selected_chapter)
        
        is_cookie_expired = error_msg and "COOKIE_EXPIRED" in error_msg
        
        # Beautiful layout design tokens
        header_line = "🏆 <b>C H O R C H A   A U T O - E X A M</b> 🏆\n"
        divider = "───────────────────────────\n"
        
        if success:
            status_str = "🟢 <b>STATUS: Practice Completed Successfully!</b>\n\n"
        elif is_cookie_expired:
            status_str = "⚠️ <b>STATUS: Session Expired / Login Required</b>\n\n"
        else:
            status_str = "🔴 <b>STATUS: Pipeline Exception Triggered</b>\n\n"
            
        details_part = (
            f"📂 <b>Subject:</b> {subject_esc}\n"
            f"📖 <b>Chapter:</b> {chapter_esc}\n"
            f"📊 <b>Progress:</b> {self.question_count} Questions Answered\n"
            f"🔥 <b>Daily Streak:</b> {'Captured [HOT] 🔴' if self.streak_captured else 'Not Captured [WARN] 🟡'}\n"
            f"👤 <b>Credential:</b> <code>{self.phone}</code>\n"
        )
        
        if success:
            compact_ans = ", ".join([f"Q{q}:{ans}" for q, ans in self.decoded_answers_text.items()])
            if len(compact_ans) > 160:
                compact_ans = compact_ans[:150] + "..."
            solutions_part = f"💡 <b>SOLUTIONS:</b>\n<code>{html.escape(compact_ans)}</code>\n"
        elif is_cookie_expired:
            solutions_part = (
                "🚨 <b>ACTION REQUIRED:</b>\n"
                "The current session has expired. The bot attempted auto-login but could not find valid session context.\n"
            )
        else:
            err_esc = html.escape(str(error_msg or 'Fatal Native Interruption'))
            if len(err_esc) > 160:
                err_esc = err_esc[:150] + "..."
            solutions_part = f"❌ <b>ERROR:</b> <code>{err_esc}</code>\n"

        logs_accumulated = logger.get_log_string()
        log_lines = [line for line in logs_accumulated.split("\n") if line.strip()]
        
        # Short logs for Caption (limit to 4 lines)
        short_logs = "\n".join(log_lines[-4:])
        if len(short_logs) > 200:
            short_logs = short_logs[-190:]
            
        # Full logs for Fallback text (limit to 12 lines)
        full_logs = "\n".join(log_lines[-12:])
        if len(full_logs) > 1500:
            full_logs = full_logs[-1400:]
            
        # Combine into Caption message (Under 1024 chars!)
        caption_body = (
            f"{header_line}"
            f"{divider}"
            f"{status_str}"
            f"{details_part}"
            f"{divider}"
            f"{solutions_part}"
            f"{divider}"
            f"📋 <b>LATEST PIPELINE LOGS:</b>\n<pre>{html.escape(short_logs)}</pre>\n"
            f"{divider}"
            f"🤖 <i>Chorcha Engine | Automated Thread</i>"
        )
        
        if len(caption_body) > 1000:
            caption_body = caption_body[:980] + "..."
            
        # Combine into Fallback full message (Under 4096 chars!)
        full_ans_json = json.dumps(self.decoded_answers_text, indent=4, ensure_ascii=False)
        if len(full_ans_json) > 1200:
            full_ans_json = full_ans_json[:1100] + "\n..."
            
        fallback_body = (
            f"{header_line}"
            f"{divider}"
            f"{status_str}"
            f"{details_part}"
            f"{divider}"
            f"💡 <b>DETAILED SOLUTIONS:</b>\n<pre>{html.escape(full_ans_json)}</pre>\n"
            f"{divider}"
            f"📋 <b>DETAILED PIPELINE LOGS:</b>\n<pre>{html.escape(full_logs)}</pre>\n"
            f"{divider}"
            f"🤖 <i>Chorcha Engine | Automated Thread</i>"
        )
        
        if len(fallback_body) > 4000:
            fallback_body = fallback_body[:3900] + "\n...[Truncated]..."

        # Collect paths of existing screenshots
        valid_photos = []
        for path, desc in self.collected_screenshots:
            if path and os.path.exists(path):
                valid_photos.append(path)

        if valid_photos:
            # Send as sendMediaGroup
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMediaGroup"
            import uuid
            boundary = f"MultipartBoundary-{uuid.uuid4().hex}"
            
            # Prepare the media parameter
            media_items = []
            for i, photo_path in enumerate(valid_photos):
                item = {
                    "type": "photo",
                    "media": f"attach://photo{i}"
                }
                # Put the caption on the first photo of the group
                if i == 0:
                    item["caption"] = caption_body
                    item["parse_mode"] = "HTML"
                media_items.append(item)
            
            payload_parts = [
                b'--' + boundary.encode('utf-8'),
                b'Content-Disposition: form-data; name="chat_id"',
                b'',
                self.chat_id.encode('utf-8'),
                b'--' + boundary.encode('utf-8'),
                b'Content-Disposition: form-data; name="media"',
                b'',
                json.dumps(media_items).encode('utf-8')
            ]
            
            # Add files to the multipart body
            for i, photo_path in enumerate(valid_photos):
                payload_parts.extend([
                    b'--' + boundary.encode('utf-8'),
                    f'Content-Disposition: form-data; name="photo{i}"; filename="{os.path.basename(photo_path)}"'.encode('utf-8'),
                    b'Content-Type: image/png',
                    b''
                ])
                with open(photo_path, 'rb') as img_file:
                    payload_parts.append(img_file.read())
            
            payload_parts.append(b'--' + boundary.encode('utf-8') + b'--')
            payload_parts.append(b'')
            body = b'\r\n'.join(payload_parts)
            
            req = urllib.request.Request(
                url, data=body,
                headers={
                    'Content-Type': f'multipart/form-data; boundary={boundary}',
                    'Content-Length': str(len(body)),
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
                }
            )
            try:
                with urllib.request.urlopen(req, timeout=45) as response:
                    logger.info("Successfully dispatched unified media group report to Telegram.")
                    return response.read()
            except urllib.error.HTTPError as he:
                try:
                    err_resp = he.read().decode()
                    logger.info(f"Failed dispatching media group: HTTP Error {he.code}: {err_resp}. Falling back...")
                except:
                    logger.info(f"Failed dispatching media group: HTTP Error {he.code}. Falling back...")
            except Exception as ex:
                logger.info(f"Failed dispatching media group: {ex}. Falling back to standard message...")
        
        # Fallback to sendMessage if no photos or sendMediaGroup failed
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        
        data = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": fallback_body,
            "parse_mode": "HTML"
        }).encode("utf-8")
        
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"})
            with urllib.request.urlopen(req, timeout=15) as response:
                logger.info("Successfully dispatched fallback text report to Telegram.")
                return response.read()
        except Exception as ex:
            print(f"CRITICAL: Failed dispatching Telegram fallback message: {ex}")

    @staticmethod
    def decode_string(encoded_str: str, key: str) -> str:
        if not encoded_str or not isinstance(encoded_str, str):
            return encoded_str
        decoded = []
        key_len = len(key)
        for i, char in enumerate(encoded_str):
            decoded.append(chr((ord(char) - ord(key[i % key_len]) + 65536) % 65536))
        return ''.join(decoded)

    @staticmethod
    def evaluate_option_index(decoded_ans: str) -> int:
        ans = decoded_ans.strip().upper()
        if not ans:
            return 0
        # Character Grid Checks
        for char, index in [("A", 0), ("B", 1), ("C", 2), ("D", 3), ("1", 0), ("2", 1), ("3", 2), ("4", 3)]:
            if char in ans:
                return index
        return 0

    def intercept_exam_payloads(self, response: Response):
        """Asynchronous API Listener targeting backend structural configurations."""
        if "mujib.chorcha.net/exam/quick" in response.url and response.request.method == "POST":
            logger.info("Fired API Target Catch: Intercepted internal exam schema packet.")
            x_chorcha_id = response.headers.get("x-chorcha-id")
            if not x_chorcha_id:
                logger.info("Anomaly: Found verification target block missing structural validation hash header.")
                return
            try:
                payload = response.json()
                questions = payload.get("data", {}).get("questions", [])
                logger.info(f"Synchronized backend mapping grid matrix: Packed {len(questions)} items safely.")
                
                for idx, item in enumerate(questions):
                    encoded_ans = item.get("answer")
                    decoded_ans = self.decode_string(encoded_ans, x_chorcha_id).strip()
                    q_idx = idx + 1
                    
                    self.correct_options[q_idx] = self.evaluate_option_index(decoded_ans)
                    self.decoded_answers_text[q_idx] = decoded_ans
            except Exception as ex:
                logger.info(f"Exception raised tracking custom background interceptors: {ex}")

    def push_auth_cookies(self, context: BrowserContext) -> bool:
        if not self.auth_file.exists():
            logger.info(f"State Interruption: Target system payload missing data file: '{self.auth_file}'")
            return False
        try:
            with open(self.auth_file, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            
            reformatted_cookies = []
            for c in cookies:
                pc = {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"]}
                if "expirationDate" in c: pc["expires"] = int(c["expirationDate"])
                elif "expires" in c: pc["expires"] = int(c["expires"])
                if "httpOnly" in c: pc["httpOnly"] = c["httpOnly"]
                if "secure" in c: pc["secure"] = c["secure"]
                if "sameSite" in c and c["sameSite"] in ["Lax", "Strict", "None"]:
                    pc["sameSite"] = c["sameSite"]
                reformatted_cookies.append(pc)
                
            context.add_cookies(reformatted_cookies)
            logger.info("Successfully loaded localized target credentials context layers cleanly.")
            return True
        except Exception as ex:
            logger.info(f"Failed parsing validation storage modules: {ex}")
            return False

    def perform_login(self, page: Page, context: BrowserContext) -> bool:
        logger.info("Attempting login with phone and password...")
        try:
            page.goto("https://chorcha.net/auth/register", wait_until="networkidle", timeout=25000)
            
            # Step 1: Mobile Number
            page.wait_for_selector('input[placeholder="01XXXXXXXXX"]', timeout=15000)
            page.fill('input[placeholder="01XXXXXXXXX"]', self.phone)
            
            # Click proceed button
            page.click('button:has-text("এগিয়ে যাও")')
            
            # Step 2: Password input
            page.wait_for_selector('input[placeholder="Password"]', timeout=15000)
            page.fill('input[placeholder="Password"]', self.password)
            
            # Click login button
            page.click('button:has-text("লগইন করো")')
            
            # Wait for dashboard navigation or check cookies/token
            for _ in range(20):
                page.wait_for_timeout(500)
                if "dashboard" in page.url:
                    break
                cookies = context.cookies()
                if any(c.get('name') == 'token' for c in cookies):
                    break
            
            # Save cookies back to cookie.json
            cookies = context.cookies()
            with open(self.auth_file, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=4)
            logger.info("Login successful. Cookies successfully saved to cookie.json.")
            
            # Send updated cookies to Telegram if in GITHUB_ACTIONS environment
            if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
                try:
                    self.send_updated_cookies_to_telegram(cookies)
                except Exception as e:
                    logger.info(f"Failed sending updated cookies to Telegram: {e}")
                    
            return True
        except Exception as e:
            logger.info(f"Login failed: {e}")
            return False

    def send_updated_cookies_to_telegram(self, cookies):
        """Sends the newly generated session cookies directly to Telegram as a JSON code block."""
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        cookies_json = json.dumps(cookies, indent=2)
        
        msg = (
            "🔄 <b>Chorcha Bot: New Session Cookies Generated!</b>\n"
            "───────────────────────────\n"
            "The bot logged in successfully and generated a new session token. Please copy the JSON block below and update your GitHub Secret <code>CHORCHA_COOKIES</code>:\n\n"
            f"<pre>{html.escape(cookies_json)}</pre>"
        )
        
        if len(msg) > 4000:
            msg = msg[:3900] + "\n...[Truncated]..."
            
        data = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": msg,
            "parse_mode": "HTML"
        }).encode("utf-8")
        
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"})
        with urllib.request.urlopen(req, timeout=15) as response:
            logger.info("Successfully dispatched updated cookies alert to Telegram.")
            return response.read()

    def capture_dashboard_streak(self, page: Page):
        """Navigates to the homepage, captures dashboard screenshot, and handles the streak dialog if present."""
        logger.info("Transitioning to main application home view for performance indexing verification...")
        try:
            # Force navigation to dashboard explicitly
            page.goto("https://chorcha.net/", wait_until="networkidle", timeout=25000)
            page.wait_for_timeout(3000)  # Allow client hydration architecture to stabilize safely
            
            # Mandatory Dashboard View Screenshot Capture
            homepage_screenshot = f"homepage_snapshot_{int(time.time())}.png"
            page.screenshot(path=homepage_screenshot)
            self.collected_screenshots.append((homepage_screenshot, "Dashboard Homepage"))
            
            streak_selector = ".text-sm.py-1.px-3.cursor-pointer, [class*='text-sm py-1 px-3 cursor-pointer']"
            streak_target = page.locator(streak_selector).first
            
            if streak_target.is_visible():
                logger.info("Identified high-priority streak tracking widget block element match. Triggering click...")
                streak_target.scroll_into_view_if_needed()
                streak_target.click()
                page.wait_for_timeout(2000)  # Wait for transition frame animation parameters to execute
                
                # Capture and record target frame layout properties
                self.streak_image_path = f"streak_metric_{int(time.time())}.png"
                page.screenshot(path=self.streak_image_path)
                self.streak_captured = True
                logger.info(f"Local binary context saved: '{self.streak_image_path}'")
                self.collected_screenshots.append((self.streak_image_path, "Streak Metric"))
                
                # Cleanup viewport overlay manually to keep terminal fluid
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            else:
                logger.info("Operational report: Streak widget element footprint missing from current structural viewport matrix context.")
        except Exception as ex:
            logger.info(f"Warning encountered during standalone streak monitoring automation loops: {ex}")

    def execute_pipeline(self):
        """Core Orchestrator running atomic browser functions sequentially."""
        logger.info("Initializing automated performance assessment engine routines...")
        
        failed_chapters = set()
        failed_subjects = set()
        quiz_initialized = False
        
        with sync_playwright() as playwright:
            headless_mode = os.environ.get("HEADLESS", "false").lower() == "true" or "CI" in os.environ
            browser = playwright.chromium.launch(
                headless=headless_mode,
                args=[] if headless_mode else ["--start-maximized"]
            )
            
            context_args = {"viewport": {"width": 1280, "height": 800}} if headless_mode else {"no_viewport": True}
            context_args["user_agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            context = browser.new_context(**context_args)
            
            cookies_loaded = self.push_auth_cookies(context)
            page = context.new_page()
            
            if not cookies_loaded:
                logger.info("Auth cookies not loaded/missing. Attempting login first.")
                is_credentials_configured = (
                    self.phone and self.password and 
                    self.phone.strip() and self.password.strip() and 
                    "01XXXXXXXXX" not in self.phone
                )
                if not is_credentials_configured:
                    logger.info("Credentials not provided or invalid, and cookies are expired/missing.")
                    raise RuntimeError("COOKIE_EXPIRED: Stored session cookies are expired or missing, and login credentials are not configured.")
                
                if not self.perform_login(page, context):
                    raise RuntimeError("COOKIE_EXPIRED: Stored session cookies are expired or missing, and automatic login failed.")
            
            # Setup dynamic network monitors
            page.on("response", self.intercept_exam_payloads)
            
            navigation_loop_limit = 5
            loop_idx = 0
            
            while not quiz_initialized and loop_idx < navigation_loop_limit:
                loop_idx += 1
                logger.info(f"Executing systemic data navigation pass ({loop_idx}/{navigation_loop_limit})...")
                
                self.correct_options.clear()
                self.decoded_answers_text.clear()
                
                try:
                    page.goto("https://chorcha.net/practice-exam", wait_until="commit", timeout=20000)
                    if "auth/register" in page.url or "register" in page.url or "login" in page.url:
                        logger.info("Cookie expired or login screen detected on navigation. Attempting login...")
                        is_credentials_configured = (
                            self.phone and self.password and 
                            self.phone.strip() and self.password.strip() and 
                            "01XXXXXXXXX" not in self.phone
                        )
                        if is_credentials_configured and self.perform_login(page, context):
                            continue
                        raise RuntimeError("COOKIE_EXPIRED: Chorcha auth cookie has expired or login issue detected.")
                    target_selector = 'main h3, button:has-text("লগইন"), button:has-text("Login"), a:has-text("লগইন"), a:has-text("Login")'
                    page.locator(target_selector).first.wait_for(state="visible", timeout=20000)
                except Exception as ex:
                    if "COOKIE_EXPIRED" in str(ex):
                        raise ex
                    if "auth/register" in page.url or "register" in page.url or "login" in page.url:
                        logger.info("Cookie expired or login screen detected. Attempting login...")
                        is_credentials_configured = (
                            self.phone and self.password and 
                            self.phone.strip() and self.password.strip() and 
                            "01XXXXXXXXX" not in self.phone
                        )
                        if is_credentials_configured and self.perform_login(page, context):
                            continue
                    logger.info(f"Primary routing process failed due to latency constraints: {ex}. Recalibrating tracking matrix line...")
                    continue
                
                # Runtime Login Validation Gate
                login_indicator = page.locator('text="লগইন", text="Login"').first
                if login_indicator.is_visible() or "auth/register" in page.url or "register" in page.url or "login" in page.url:
                    logger.info("Login indicator visible or redirected to auth/register. Attempting login...")
                    is_credentials_configured = (
                        self.phone and self.password and 
                        self.phone.strip() and self.password.strip() and 
                        "01XXXXXXXXX" not in self.phone
                    )
                    if is_credentials_configured and self.perform_login(page, context):
                        continue
                    raise RuntimeError("COOKIE_EXPIRED: Stored session cookies have expired, and automatic login is not configured or failed.")
                
                # Subject Extraction Routines
                subject_headers = page.locator('main h3')
                try: subject_headers.first.wait_for(state="visible", timeout=8000)
                except: logger.info("Subject mapping grid target timed out."); continue
                
                total_subs = subject_headers.count()
                target_bangla_nodes = []
                ict_fallback_node = None
                
                for i in range(total_subs):
                    try:
                        title = subject_headers.nth(i).inner_text().strip()
                        if "বাংলা" in title and title not in failed_subjects:
                            target_bangla_nodes.append((i, title))
                        if "তথ্য ও যোগাযোগ প্রযুক্তি" in title:
                            ict_fallback_node = (i, title)
                    except: pass
                
                if target_bangla_nodes:
                    chosen_idx, chosen_title = random.choice(target_bangla_nodes)
                    logger.info(f"Random operational matching confirmed: Loaded '{chosen_title}'")
                elif ict_fallback_node and ict_fallback_node[1] not in failed_subjects:
                    chosen_idx, chosen_title = ict_fallback_node
                    logger.info(f"Fallback verification triggered: Defaulting to standard layer payload target: '{chosen_title}'")
                else:
                    logger.info("Execution block clear: No fresh unparsed components found. Resetting state arrays entirely...")
                    failed_subjects.clear()
                    failed_chapters.clear()
                    continue
                
                self.selected_subject = chosen_title
                subject_btn = subject_headers.nth(chosen_idx)
                subject_btn.scroll_into_view_if_needed()
                subject_btn.click()
                
                # Paper Sub-Selection Strategy Validation Layer
                try: page.locator('main h2').wait_for(state="visible", timeout=6000)
                except: failed_subjects.add(chosen_title); continue
                
                chapter_nodes = page.locator('main h3').all()
                paper_sub_options = []
                for node in chapter_nodes:
                    try:
                        node_text = node.inner_text().strip()
                        if any(phrase in node_text for phrase in ["প্রথম পত্র", "২য় পত্র", "দ্বিতীয় পত্র"]):
                            paper_sub_options.append(node)
                    except: pass
                    
                if paper_sub_options:
                    chosen_paper_node = random.choice(paper_sub_options)
                    logger.info(f"Routing sub-module branch segment link target: '{chosen_paper_node.inner_text().strip()}'")
                    chosen_paper_node.scroll_into_view_if_needed()
                    chosen_paper_node.click()
                    page.wait_for_timeout(1000)
                    chapter_nodes = page.locator('main h3').all()
                
                # Chapter Extraction Filtration Flow
                valid_chapter_pool = []
                for node in chapter_nodes:
                    try:
                        txt = node.inner_text().strip()
                        if txt and (self.selected_subject, txt) not in failed_chapters:
                            valid_chapter_pool.append((node, txt))
                    except: pass
                    
                if not valid_chapter_pool:
                    logger.info("Zero chapter processing units found. Flushing cache markers down...")
                    failed_chapters.clear()
                    continue
                    
                target_node, target_text = random.choice(valid_chapter_pool)
                self.selected_chapter = target_text
                logger.info(f"Target node connection lock verified on entry text: '{target_text}'")
                target_node.scroll_into_view_if_needed()
                target_node.click()
                
                # Engagement Action Process Launch Sequence
                quick_practice_action = page.locator('button:has-text("দ্রুত প্র্যাকটিস")')
                
                # Account for sliding layouts or accordion frameworks
                start_clock = time.time()
                while (time.time() - start_clock) < 2.5:
                    if quick_practice_action.is_visible(): break
                    page.wait_for_timeout(150)
                    
                if not quick_practice_action.is_visible():
                    nested_sub_nodes = page.locator('main h3').all()
                    active_sub_pool = [n for n in nested_sub_nodes if n.is_visible() and n.inner_text().strip() != target_text]
                    if active_sub_pool:
                        sub_selected_node = random.choice(active_sub_pool)
                        self.selected_chapter = sub_selected_node.inner_text().strip()
                        logger.info(f"Expanding granular operational structural branch mapping: '{self.selected_chapter}'")
                        sub_selected_node.scroll_into_view_if_needed()
                        sub_selected_node.click()
                
                try:
                    quick_practice_action.wait_for(state="visible", timeout=6000)
                    quick_practice_action.click()
                    
                    # Wait up to 8 seconds dynamically for correct_options to populate
                    start_time = time.time()
                    while not self.correct_options and (time.time() - start_time) < 8.0:
                        page.wait_for_timeout(100)
                    
                    if self.correct_options:
                        quiz_initialized = True
                    else:
                        logger.info("Internal tracking validation metrics failure: Intercept keys mismatch.")
                        failed_chapters.add((self.selected_subject, self.selected_chapter))
                except Exception as ex:
                    if "auth/register" in page.url or "register" in page.url or "login" in page.url or page.locator('text="লগইন", text="Login"').first.is_visible():
                        logger.info("Cookie expired or login screen detected inside interaction handler. Attempting login...")
                        is_credentials_configured = (
                            self.phone and self.password and 
                            self.phone.strip() and self.password.strip() and 
                            "01XXXXXXXXX" not in self.phone
                        )
                        if is_credentials_configured and self.perform_login(page, context):
                            continue
                        raise RuntimeError("COOKIE_EXPIRED: Chorcha auth cookie has expired or login issue detected.")
                    logger.info(f"System execution bottleneck matching targets: {ex}")
                    failed_chapters.add((self.selected_subject, self.selected_chapter))
            
            if not quiz_initialized:
                raise RuntimeError("Failed to resolve stable functional database matrix endpoints to initialize automated assessment sequences.")
            
            # Interactive Automation Solution Injection Run Loop
            logger.info("Quiz matrix pipeline fully live. Executing automated responses maps dynamically...")
            consecutive_wait_ticks = 0
            
            while True:
                skip_gate = page.locator('button:has-text("স্কিপ করো")')
                advance_gate = page.locator('button:has-text("এগিয়ে যাও")')
                
                if skip_gate.is_visible() or advance_gate.is_visible():
                    logger.info("Final target metrics dashboard threshold reached safely. Breaking dynamic solution injection tracking loop.")
                    break
                    
                option_nodes = page.locator('button.rounded-xl.border')
                if option_nodes.count() > 0:
                    consecutive_wait_ticks = 0
                    self.question_count += 1
                    
                    target_selection_index = self.correct_options.get(self.question_count, 0)
                    
                    # Deliberate anti-bot detection injection variance emulation block logic
                    if self.question_count == 15:
                        actual_node_count = option_nodes.count()
                        logger.info(f"Executing noise profile injection logic rules matrix over index tracking item: [{self.question_count}]")
                        target_selection_index = (target_selection_index + 1) % (actual_node_count if actual_node_count > 0 else 4)
                        
                    if target_selection_index >= option_nodes.count():
                        target_selection_index = 0
                        
                    logger.info(f"Resolving item context node [{self.question_count}] -> Committing selection node offset choice: {target_selection_index}")
                    try:
                        option_nodes.nth(target_selection_index).click()
                    except:
                        try: option_nodes.first.click()
                        except: pass
                        
                    next_item_trigger = page.locator('button:has-text("পরের প্রশ্ন"), button:has-text("শেষ করো")')
                    try:
                        next_item_trigger.wait_for(state="visible", timeout=2000)
                        next_item_trigger.click()
                        next_item_trigger.wait_for(state="hidden", timeout=2000)
                    except: pass
                    
                    page.wait_for_timeout(200)
                else:
                    page.wait_for_timeout(1000)
                    consecutive_wait_ticks += 1
                    if consecutive_wait_ticks >= 12:
                        if skip_gate.is_visible() or advance_gate.is_visible(): break
                        logger.info("Timeout check triggered: Internal sequence trace stalled out in standard execution pipeline loop.")
                        break
            
            # Post-Exam Diagnostics Dashboard Validation Procedures
            try:
                metrics_screenshot_file = f"metrics_checkpoint_{int(time.time())}.png"
                page.screenshot(path=metrics_screenshot_file)
                self.collected_screenshots.append((metrics_screenshot_file, "Metrics Checkpoint"))
            except: pass
            
            # Safe Pipeline Closure Processing Intersect Hooks
            logger.info("Waiting for closing actions ('স্কিপ করো' / 'এগিয়ে যাও') to appear...")
            
            skip_action_element = page.locator('button:has-text("স্কিপ করো"), button:has-text("স্কিপ কর")')
            
            # Dynamic Wait for skip button to prevent missing elements
            try:
                skip_action_element.wait_for(state="visible", timeout=5000)
            except:
                logger.info("'স্কিপ করো' button was not visible within time limits.")
                
            if skip_action_element.is_visible():
                try:
                    logger.info("Clicking 'স্কিপ করো' button.")
                    skip_action_element.scroll_into_view_if_needed()
                    skip_action_element.click()
                    logger.info("Clicked 'স্কিপ করো' button. Waiting 3 seconds for next step to load...")
                    page.wait_for_timeout(3000)
                except Exception as e:
                    logger.info(f"Execution handling exception on skip action click: {e}")
                
            # Click 'এগিয়ে যাও' / 'এগিয়ে যাও' in a loop to handle multiple transition/leaderboard screens
            logger.info("Starting loop to click advance button ('এগিয়ে যাও' / 'এগিয়ে যাও') if visible...")
            
            for i in range(5):
                advance_action_element = page.locator('button:has-text("এগিয়ে যাও"), button:has-text("এগিয়ে যাও")')
                
                try:
                    advance_action_element.first.wait_for(state="visible", timeout=3000)
                except:
                    logger.info(f"Iteration {i+1}: 'এগিয়ে যাও' button not visible.")
                    break
                    
                if advance_action_element.first.is_visible():
                    try:
                        logger.info(f"Iteration {i+1}: Clicking 'এগিয়ে যাও' button.")
                        advance_action_element.first.scroll_into_view_if_needed()
                        advance_action_element.first.click()
                        logger.info("Clicked 'এগিয়ে যাও' button. Waiting 2 seconds for next screen...")
                        page.wait_for_timeout(2000)
                    except Exception as e:
                        logger.info(f"Execution handling exception on advance action click: {e}")
                        break
                else:
                    break
            
            # RUNTIME EXTENSION: Perform Homepage Streak Collection Validation Routines Before Ending System Engine
            self.capture_dashboard_streak(page)
            
            logger.info("Closing runtime context environments safely down without tracing footprints...")
            context.close()
            browser.close()
            
            # Transmit success report array block packet safely out
            self.send_telegram_report(success=True)

    def run(self):
        try:
            self.execute_pipeline()
        except BaseException as ex:
            error_traceback_str = traceback.format_exc()
            logger.info(f"CRITICAL FAULT: Pipeline structural flow sequence broke down under constraint processing rules: {ex}")
            logger.info(error_traceback_str)
            
            # Dispatch structural alert fail state data traces
            self.send_telegram_report(success=False, error_msg=str(ex), traceback_str=error_traceback_str)
            raise ex


if __name__ == "__main__":
    bot = ChorchaQuizBot()
    bot.run()
