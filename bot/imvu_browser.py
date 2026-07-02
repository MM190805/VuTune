import asyncio
import logging
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)

class IMVUBrowserClient:
    def __init__(self, session_data, credentials=None):
        self.session_data = session_data
        self.credentials = credentials or {}
        self.two_factor_code = None
        self.two_factor_event = asyncio.Event()
        self.playwright = None
        self.browser = None
        self.context = None
        self.pages = {} # room_id -> page
        self.is_running = False
        self.is_logged_in = False
        self.tasks = {} # room_id -> task
        
    def provide_2fa(self, code):
        self.two_factor_code = code
        self.two_factor_event.set()

    async def start(self):
        self.is_running = True
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--use-gl=swiftshader",
                "--enable-webgl",
                "--ignore-gpu-blocklist",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                '--disable-setuid-sandbox',
                '--js-flags=--max-old-space-size=256'
            ]
        )
        
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        import os
        state_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'state.json')
        
        if os.path.exists(state_path):
            self.context = await self.browser.new_context(
                viewport={'width': 1280, 'height': 720},
                user_agent=user_agent,
                storage_state=state_path
            )
        else:
            self.context = await self.browser.new_context(
                viewport={'width': 1280, 'height': 720},
                user_agent=user_agent
            )
        # INTERCEPT AND BLOCK HEAVY 3D ASSETS to prevent CPU/RAM crashes on free tier!
        async def block_heavy_assets(route):
            url = route.request.url.lower()
            resource_type = route.request.resource_type
            
            # Allow essential API requests and websockets
            if resource_type in ["fetch", "xhr", "websocket", "document", "script"]:
                # Check if it's a 3D asset fetch disguised as XHR
                if any(ext in url for ext in [".cfl", ".chkn", ".xmf", ".xrf", ".xsf", ".crg", "texture", "mesh"]):
                    await route.abort()
                else:
                    await route.continue_()
            # Block all images, media, fonts, and stylesheets to save massive amounts of RAM
            elif resource_type in ["image", "media", "font", "stylesheet"]:
                await route.abort()
            else:
                await route.continue_()
                
        await self.context.route("**/*", block_heavy_assets)
        await stealth_async(self.context)
        
        self.username = self.credentials.get('username', 'VuTune')
        self.is_logged_in = True

    async def join_room(self, room_id, on_message_callback):
        if not self.context:
            return False
            
        try:
            page = await self.context.new_page()
            await Stealth().apply_stealth_async(page)
            self.pages[room_id] = page
            
            async def abort_route(route):
                if route.request.resource_type in ["media"]:
                    await route.abort()
                else:
                    await route.continue_()
                    
            await page.route("**/*", abort_route)
            
            logger.info("Checking authentication...")
            await page.goto("https://www.imvu.com/next/login/", timeout=60000)
            await page.wait_for_timeout(4000)
            await page.screenshot(path="debug.jpg", type="jpeg", quality=50)
            logger.info("Saved initial page screenshot to debug.jpg")
            
            if "login" in page.url.lower() or "welcome" in page.title().lower():
                logger.info("Session invalid on Cloud. Attempting automated login...")
                try:
                    logger.info("Opening login modal...")
                    try:
                        # Use the specific class from the HTML inspector
                        login_trigger = page.locator('button.sign-in').first
                        await login_trigger.click(timeout=5000, force=True)
                        await page.wait_for_timeout(2000)
                    except Exception as e:
                        logger.warning(f"Standard locator failed, trying JS fallback: {e}")
                        try:
                            await page.evaluate("""
                                const el = Array.from(document.querySelectorAll('a, button, span')).find(e => e.innerText && e.innerText.toLowerCase().includes('log in'));
                                if(el) el.click();
                            """)
                            await page.wait_for_timeout(2000)
                        except:
                            logger.info("No login modal trigger found, assuming form is visible.")
                        
                    user_input = page.locator('input[name="avatarname"]:visible, form[name="login_form"] input[type="text"]:visible').first
                    logger.info("Waiting for username input...")
                    await user_input.wait_for(timeout=15000)
                    await user_input.fill(self.credentials.get('username', 'VuTune'), timeout=5000)
                    
                    logger.info("Filling password...")
                    await page.locator('input[name="password"]:visible, form[name="login_form"] input[type="password"]:visible').first.fill(self.credentials.get('password', ''), timeout=5000)
                    
                    logger.info("Clicking login...")
                    try:
                        submit_btn = page.locator('form[name="login_form"] .submit:visible, form[name="login_form"] button:visible, .submit:visible').first
                        await submit_btn.click(timeout=5000, force=True)
                    except Exception as e:
                        logger.warning(f"Standard submit failed, trying JS fallback: {e}")
                        await page.evaluate("document.querySelector('form[name=\"login_form\"] .submit, .submit, form[name=\"login_form\"] button').click()")
                    
                    logger.info("Waiting after login click for URL to change...")
                    try:
                        await page.wait_for_url(lambda u: "login" not in u.lower() and "welcome" not in u.lower(), timeout=20000)
                        logger.info(f"Login successful! URL changed to {page.url}")
                    except Exception as e:
                        logger.warning("URL did not change after 20 seconds. Checking if stuck on 2FA/Captcha.")
                        
                    await page.wait_for_timeout(2000)
                    await page.screenshot(path="debug.jpg", type="jpeg", quality=50)
                    logger.info("Saved post-login screenshot to debug.jpg")
                    
                    if "login" in page.url.lower() or "welcome" in page.url.lower():
                        logger.warning("Still on login page! Likely hit 2FA or Captcha. Waiting for user input via /debug...")
                        await self.two_factor_event.wait()
                        code = self.two_factor_code
                        logger.info(f"Received 2FA code! Submitting...")
                        await page.locator('input[type="text"], input[type="number"], input[name="code"]').first.fill(code, timeout=5000)
                        await page.locator('button[type="submit"], button:has-text("Submit"), button:has-text("Verify")').first.click(timeout=5000)
                        await page.wait_for_timeout(5000)
                        self.two_factor_event.clear()
                    
                    logger.info(f"Post-login URL: {page.url}")
                except Exception as e:
                    logger.error(f"Automated login failed: {e}")
                    
            await page.screenshot(path="debug.jpg", type="jpeg", quality=50)

            logger.info(f"Navigating to room {room_id}...")
            try:
                await page.goto(f"https://www.imvu.com/next/chat/room-{room_id}/", wait_until="domcontentloaded", timeout=120000)
            except Exception as e:
                logger.warning(f"Goto room timed out or failed, but we might still be there: {e}")
            
            logger.info("Waiting 30 seconds for room to load...")
            await page.wait_for_timeout(30000)
            
            try:
                url = page.url
                title = await page.title()
                logger.info(f"DEBUG - Current URL: {url}")
                logger.info(f"DEBUG - Page Title: {title}")
                await page.screenshot(path="debug.jpg", type="jpeg", quality=50)
            except Exception as e:
                logger.error(f"Failed to take debug screenshot: {e}")
                
            try:
                logger.info("Looking for Join button...")
                join_btn = page.locator('button:has-text("Join"), button:has-text("JOIN")').first
                await join_btn.wait_for(timeout=30000)
                await join_btn.click()
                logger.info("Clicked Join button!")
                await page.wait_for_timeout(5000)
                await page.screenshot(path="debug.jpg", type="jpeg", quality=50)
            except Exception as e:
                logger.error(f"Could not find or click Join button: {e}")
            
            # Start polling chat and updating live camera
            logger.info(f"Started live camera and chat listener for {room_id}...")
            task = asyncio.create_task(self._poll_chat(room_id, page, on_message_callback))
            self.tasks[room_id] = task
            return True
        except Exception as e:
            logger.error(f"Error joining room {room_id}: {e}")
            return False

    async def _poll_chat(self, room_id, page, callback):
        logger.info(f"Chat listener active for room {room_id}")
        js_code = """
        () => {
            let msgs = [];
            // Select all chat message containers that have a data-id (actual user messages, not system/UI templates)
            document.querySelectorAll('.cs2-msg[data-id]').forEach(el => {
                let lines = el.innerText.split('\\n');
                for (let line of lines) {
                    let text = line.trim();
                    if (text) {
                        // Filter out bot's own responses
                        let emojis = ['🎵', '🔍', '🤖', '❌', '🔇', '🚫', '✅', '🗑', '🗑️', '⏭', '⏭️', '⏹', '⏹️'];
                        let isBot = emojis.some(e => text.startsWith(e)) || text.startsWith('VuTune');
                        if (!isBot) {
                            msgs.push(text);
                        }
                    }
                }
            });
            return msgs;
        }
        """
        
        last_msgs = None
        
        while self.is_running and page:
            try:
                current_msgs = await page.evaluate(js_code)
                logger.info(f"DEBUG: current_msgs = {current_msgs}")
                
                new_msgs = []
                if last_msgs is None:
                    last_msgs = current_msgs
                else:
                    match_len = 0
                    for i in range(1, min(len(last_msgs), len(current_msgs)) + 1):
                        if last_msgs[-i:] == current_msgs[:i]:
                            match_len = i
                            
                    if match_len < len(current_msgs):
                        if match_len == 0 and current_msgs:
                            # Total mismatch. To prevent catastrophic spam loop, only take the last 1 message
                            new_msgs = current_msgs[-1:]
                        else:
                            new_msgs = current_msgs[match_len:]
                            
                    last_msgs = current_msgs

                for msg in new_msgs:
                    if msg:
                        await on_message_callback("User", msg)
            except Exception as e:
                logger.error(f"Error in chat listener: {e}")
                
            try:
                await page.screenshot(path="debug.jpg", type="jpeg", quality=50)
            except Exception:
                pass
                
            await asyncio.sleep(2.0)


    async def send_message(self, room_id, text):
        page = self.pages.get(room_id)
        if not page:
            return
        try:
            input_locator = page.locator('textarea[placeholder="Say something..."]')
            await input_locator.fill(text)
            await input_locator.press('Enter')
            logger.info(f"Sent chat to {room_id}: {text}")
        except Exception as e:
            logger.error(f"Failed to send chat: {e}")

    async def leave_room(self, room_id):
        page = self.pages.pop(room_id, None)
        if page:
            await page.close()
        task = self.tasks.pop(room_id, None)
        if task:
            task.cancel()

    async def stop(self):
        self.is_running = False
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
