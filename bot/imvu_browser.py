import asyncio
import logging
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

_stealth = Stealth()

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
        self.pages = {}   # room_id -> page
        self.is_running = False
        self.is_logged_in = False
        self.tasks = {}   # room_id -> task

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
                "--disable-gpu",
                "--disable-webgl",
                "--disable-webgl2",
                "--disable-3d-apis",
                "--disable-accelerated-2d-canvas",
                "--disable-accelerated-jpeg-decoding",
                "--disable-accelerated-video-decode",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--js-flags=--max-old-space-size=256",
                "--renderer-process-limit=1",
                "--single-process"
            ]
        )

        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
        state_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "state.json")

        if os.path.exists(state_path):
            self.context = await self.browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=user_agent,
                storage_state=state_path
            )
        else:
            self.context = await self.browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=user_agent
            )

        await _stealth.apply_stealth_async(self.context)
        self.username = self.credentials.get("username", "VuTune")

    async def join_room(self, room_id, on_message_callback):
        if not self.context:
            return False

        try:
            page = await self.context.new_page()
            await _stealth.apply_stealth_async(page)
            self.pages[room_id] = page

            # ---------- 1. NAVIGATE DIRECTLY TO ROOM ----------
            logger.info(f"Navigating directly to room {room_id}...")
            try:
                await page.goto(
                    f"https://www.imvu.com/next/chat/room-{room_id}/",
                    wait_until="domcontentloaded",
                    timeout=90000
                )
            except Exception as e:
                logger.warning(f"Room goto timed out (WebGL heavy page): {e}")

            # Wait for the SPA to initialize
            await page.wait_for_timeout(15000)
            await page.screenshot(path="debug.jpg", type="jpeg", quality=60)
            
            user_val = self.credentials.get("username", "")
            pass_val = self.credentials.get("password", "")

            # ---------- 2. WAIT FOR AND CLICK JOIN BUTTON ----------
            try:
                logger.info("Waiting for Join button to appear on the room page...")
                join_btn = page.locator('button:has-text("JOIN"), button:has-text("Join"), .action-join').first
                # Wait up to 30 seconds for the button to actually render
                await join_btn.wait_for(state="visible", timeout=30000)
                await join_btn.click(force=True)
                logger.info("Successfully clicked the Join button!")
            except Exception as e:
                logger.warning(f"Join button error (maybe already in room): {e}")

            # ---------- 3. HANDLE LOGIN MODAL IF IT POPS UP ----------
            logger.info("Waiting up to 10s to see if a login modal popped up after clicking Join...")
            modal = page.locator('form[name="login_form"]').first
            
            try:
                await modal.wait_for(state="visible", timeout=10000)
                logger.warning("Modal detected! Typing credentials like a real human...")
                
                # Type Username
                user_input = page.locator('form[name="login_form"] input[name="avatarname"]').first
                await user_input.click(force=True)
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                await page.keyboard.type(user_val, delay=100)
                
                # Type Password
                pass_input = page.locator('form[name="login_form"] input[type="password"]').first
                box = await pass_input.bounding_box()
                if box:
                    await page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                else:
                    await pass_input.click(force=True)
                
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                await page.keyboard.type(pass_val, delay=100)
                
                # Submit via Enter key (most reliable human way)
                await page.keyboard.press("Enter")
                
                logger.info("Pressed Enter! Waiting up to 60s for authentication or 2FA code...")
                
                for _ in range(30):
                    await page.wait_for_timeout(2000)
                    try:
                        await page.screenshot(path="debug.jpg", type="jpeg", quality=60)
                    except:
                        pass
                        
                    if os.path.exists("2fa_code.txt"):
                        with open("2fa_code.txt", "r") as f:
                            code = f.read().strip()
                        os.remove("2fa_code.txt")
                        logger.warning(f"2FA code {code} detected! Submitting...")
                        
                        try:
                            # Exact selector based on provided DOM
                            code_input = page.locator('input.two-factor-code').first
                            await code_input.wait_for(state="visible", timeout=5000)
                            await code_input.click(force=True)
                            await page.keyboard.press("Control+A")
                            await page.keyboard.press("Backspace")
                            await page.keyboard.type(code, delay=100)
                            await page.keyboard.press("Enter")
                            
                            # Fallback click the Continue button just in case Enter doesn't trigger it
                            continue_btn = page.locator('.two-factor-challenge-dialog button:has-text("CONTINUE"), .two-factor-challenge-dialog button:has-text("Continue")').first
                            if await continue_btn.is_visible(timeout=2000):
                                await continue_btn.click(force=True)
                                
                            logger.info("Submitted 2FA code! Waiting 15s...")
                            await page.wait_for_timeout(15000)
                        except Exception as e:
                            logger.error(f"Error submitting 2FA: {e}")
                        break
                        
                    # Check if 2FA modal is currently visible
                    is_2fa = await page.locator('.two-factor-challenge-dialog').first.is_visible()
                    is_login = await page.locator('form[name="login_form"]').first.is_visible()
                    
                    if not is_login and not is_2fa:
                        logger.info("Login/2FA modals disappeared, authentication successful!")
                        break
                    elif is_2fa:
                        # Print once every few loops to avoid spam
                        if _ % 5 == 0:
                            logger.info("Waiting for user to submit 2FA code via /debug/2fa ...")
            except Exception as e:
                logger.warning(f"Exception during login modal handling (or timeout): {e}")
                logger.info("If no modal popped up, we should be entering the room now.")

            # ---------- 3. BLOCK HEAVY 3D ASSETS ONLY AFTER LOGIN ----------
            # We apply the blocker NOW so that the login form's
            # CSS/images still load correctly during authentication.
            async def block_heavy_assets(route):
                rt = route.request.resource_type
                url = route.request.url.lower()
                if rt in ["fetch", "xhr", "websocket", "document", "script"]:
                    if any(ext in url for ext in [".cfl", ".chkn", ".xmf", ".xrf", ".xsf", ".crg"]):
                        await route.abort()
                    else:
                        await route.continue_()
                elif rt in ["media", "font"]:
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", block_heavy_assets)

            # Try to click Join button if present
            try:
                logger.info("Looking for Join button...")
                join_btn = page.locator('button:has-text("Join"), button:has-text("JOIN")').first
                await join_btn.wait_for(timeout=20000)
                await join_btn.click()
                logger.info("Clicked Join button!")
                await page.wait_for_timeout(5000)
                await page.screenshot(path="debug.jpg", type="jpeg", quality=60)
            except Exception as e:
                logger.warning(f"No Join button found (may already be in room): {e}")

            # ---------- 6. START LIVE CAMERA + CHAT POLLING ----------
            logger.info(f"Started live camera and chat listener for room {room_id}...")
            task = asyncio.create_task(self._poll_chat(room_id, page, on_message_callback))
            self.tasks[room_id] = task
            return True

        except Exception as e:
            logger.error(f"Error joining room {room_id}: {e}")
            return False

    async def _poll_chat(self, room_id, page, on_message_callback):
        logger.info(f"Chat listener active for room {room_id}")
        js_code = """
        () => {
            let msgs = [];
            document.querySelectorAll('.cs2-msg[data-id]').forEach(el => {
                let lines = el.innerText.split('\\n');
                for (let line of lines) {
                    let text = line.trim();
                    if (text) {
                        let emojis = ['\uD83C\uDFB5', '\uD83D\uDD0D', '\uD83E\uDD16', '\u274C', '\uD83D\uDD07', '\uD83D\uDEAB', '\u2705', '\uD83D\uDDD1', '\u23ED', '\u23F9'];
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
            # Take live screenshot every 3 seconds for the /debug camera
            try:
                await page.screenshot(path="debug.jpg", type="jpeg", quality=60)
            except Exception:
                pass

            # Read chat messages
            try:
                current_msgs = await page.evaluate(js_code)

                if last_msgs is None:
                    last_msgs = current_msgs
                else:
                    match_len = 0
                    for i in range(1, min(len(last_msgs), len(current_msgs)) + 1):
                        if last_msgs[-i:] == current_msgs[:i]:
                            match_len = i

                    new_msgs = []
                    if match_len < len(current_msgs):
                        if match_len == 0 and current_msgs:
                            new_msgs = current_msgs[-1:]
                        else:
                            new_msgs = current_msgs[match_len:]

                    last_msgs = current_msgs

                    for msg in new_msgs:
                        if msg:
                            await on_message_callback("User", msg)

            except Exception as e:
                logger.error(f"Error in chat listener: {e}")

            await asyncio.sleep(3.0)

    async def send_message(self, room_id, text):
        page = self.pages.get(room_id)
        if not page:
            return
        try:
            input_locator = page.locator('textarea[placeholder="Say something..."]')
            await input_locator.fill(text)
            await input_locator.press("Enter")
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
