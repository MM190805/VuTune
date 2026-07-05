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
                # Essential
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--single-process",
                "--renderer-process-limit=1",
                # Stealth
                "--disable-blink-features=AutomationControlled",
                # GPU / Graphics (all off — saves 50-100MB)
                "--disable-gpu",
                "--disable-gpu-sandbox",
                "--disable-software-rasterizer",
                "--disable-accelerated-2d-canvas",
                "--disable-webgl",
                "--disable-3d-apis",
                # JavaScript heap cap (IMVU gets 96MB max — was 256MB)
                "--js-flags=--max-old-space-size=96 --optimize-for-size --gc-global",
                # Disable background processes that leak RAM
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                # Disable heavy features
                "--disable-extensions",
                "--disable-plugins",
                "--disable-translate",
                "--disable-sync",
                "--disable-default-apps",
                "--disable-component-update",
                "--disable-domain-reliability",
                "--disable-client-side-phishing-detection",
                "--disable-hang-monitor",
                "--disable-popup-blocking",
                "--disable-prompt-on-repost",
                "--disable-breakpad",
                "--no-first-run",
                "--mute-audio",
                "--metrics-recording-only",
                "--safebrowsing-disable-auto-update",
                "--password-store=basic",
                "--use-mock-keychain",
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
                viewport={"width": 800, "height": 600},
                user_agent=user_agent,
                storage_state=state_path
            )
        else:
            self.context = await self.browser.new_context(
                viewport={"width": 800, "height": 600},
                user_agent=user_agent
            )

        await _stealth.apply_stealth_async(self.context)
        
        # Aggressively block all non-essential resources to stay under 512MB RAM
        async def intercept_route(route):
            req = route.request
            rtype = req.resource_type
            url = req.url.lower()

            # 1x1 transparent PNG — satisfies React image elements without loading real images
            empty_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

            if rtype == "image":
                await route.fulfill(status=200, content_type="image/png", body=empty_png)
            elif rtype in ["media", "font", "stylesheet"]:
                await route.abort()
            elif rtype == "script" and any(x in url for x in [
                "google-analytics", "googletagmanager", "segment.io",
                "hotjar", "mixpanel", "optimizely", "clarity.ms",
                "facebook.net", "doubleclick", "amazon-adsystem"
            ]):
                await route.abort()
            elif url.endswith(('.cfl', '.xsf', '.chkn', '.xmf', '.xrf', '.xaf', '.mp3', '.ogg', '.mp4', '.woff', '.woff2', '.ttf', '.eot')):
                await route.abort()
            else:
                await route.continue_()
        await self.context.route("**/*", intercept_route)


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
                join_btn = page.locator('button.join-cta').first
                await join_btn.wait_for(state="visible", timeout=30000)
                
                logger.info("Initial Join button found! Clicking...")
                try:
                    await join_btn.click(force=True)
                except:
                    await join_btn.evaluate("node => node.click()")
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
            # Disabled: Aborting these files breaks the IMVU React loader
            # and causes it to freeze on the Join screen!
            # async def block_heavy_assets(route): ...
            # await page.route("**/*", block_heavy_assets)

            # ---------- 4. NAVIGATE BACK TO ROOM AND CLICK JOIN ----------
            # After login/2FA, re-navigate so all page state is fresh.
            logger.info("Re-navigating to room after auth...")
            try:
                await page.goto(
                    f"https://www.imvu.com/next/chat/room-{room_id}/",
                    wait_until="domcontentloaded",
                    timeout=60000
                )
            except Exception as e:
                logger.warning(f"Room re-navigation timed out (OK): {e}")

            logger.info("Waiting 12s for React to fully hydrate...")
            await page.wait_for_timeout(12000)

            # Dismiss the Performance Alert banner so it doesn't block clicks
            try:
                close_btn = page.locator('.alert-close, [class*="close"][class*="alert"], [class*="alert"] button').first
                if await close_btn.is_visible():
                    await close_btn.click(force=True)
                    logger.info("Dismissed Performance Alert banner.")
                    await page.wait_for_timeout(1000)
            except:
                pass

            # Use Playwright trusted clicks (isTrusted=true) not JS events
            logger.info("Starting Playwright trusted-click join loop...")
            for attempt in range(6):
                try:
                    await page.screenshot(path="debug.jpg", type="jpeg", quality=60)
                except:
                    pass

                join_btn = page.locator('button.join-cta').first
                try:
                    is_vis = await join_btn.is_visible()
                except:
                    is_vis = False

                if not is_vis:
                    try:
                        chat_vis = await page.locator('.chat-input, .input-area, [class*="chatInput"]').first.is_visible()
                        if chat_vis:
                            logger.info(f"Attempt {attempt+1}: join-cta not visible but chat input is! Must be in the room.")
                            break
                    except:
                        pass
                    
                    logger.info(f"Attempt {attempt+1}: join-cta not visible yet, waiting 5s for React to hydrate...")
                    await page.wait_for_timeout(5000)
                    continue

                logger.info(f"Attempt {attempt+1}: join-cta visible, clicking with Playwright trusted click...")
                try:
                    # Scroll the button into view first
                    await join_btn.scroll_into_view_if_needed()
                    await page.wait_for_timeout(500)
                    # Use Playwright's built-in click — generates real CDP pointer events (isTrusted=true)
                    await join_btn.click(timeout=5000)
                    logger.info(f"Attempt {attempt+1}: Click sent! Waiting 8s for room to load...")
                except Exception as e:
                    logger.warning(f"Attempt {attempt+1}: Normal click failed ({e}), trying force click...")
                    try:
                        await join_btn.click(force=True, timeout=5000)
                        logger.info(f"Attempt {attempt+1}: Force click sent!")
                    except Exception as e2:
                        logger.warning(f"Attempt {attempt+1}: Force click also failed: {e2}")

                await page.wait_for_timeout(8000)

            await page.screenshot(path="debug.jpg", type="jpeg", quality=60)

            # ---------- 5. SAVE SESSION SO NEXT DEPLOY SKIPS LOGIN ----------
            state_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "state.json")
            try:
                await self.context.storage_state(path=state_path)
                logger.info("Browser session saved to state.json. Next deploy will skip login/2FA!")
            except Exception as e:
                logger.warning(f"Could not save session state: {e}")

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
            let botPrefixes = ['\uD83C\uDFB5', '\uD83D\uDD0D', '\uD83E\uDD16', '\u274C', '\uD83D\uDD07', '\uD83D\uDEAB', '\u2705', '\uD83D\uDDD1', '\u23ED', '\u23F9'];

            // Try all known IMVU chat message selectors
            let selectors = [
                '.cs2-msg[data-id]',
                '[data-id][class*="msg"]',
                '[class*="message"][data-id]',
                '[class*="chat-message"]',
                '[class*="msg-text"]',
                '[class*="bubble"]',
                '.message-bubble',
                '[class*="ChatMessage"]',
                '[class*="chatMessage"]',
            ];

            let foundEls = [];
            for (let sel of selectors) {
                let els = document.querySelectorAll(sel);
                if (els.length > 0) {
                    foundEls = Array.from(els);
                    break;
                }
            }

            foundEls.forEach(el => {
                // Find the actual text element inside the message block
                let textEl = el.querySelector('.cs2-text, [class*="text-12"], [class*="text-14"], p, span');
                let text = '';
                if (textEl) {
                    text = textEl.innerText ? textEl.innerText.trim() : '';
                } else {
                    // Fallback to the last line of the full innerText if no specific text element found
                    let fullText = el.innerText ? el.innerText.trim() : '';
                    let lines = fullText.split('\\n').filter(l => l.trim());
                    text = lines[lines.length - 1] ? lines[lines.length - 1].trim() : '';
                }
                
                if (!text) return;
                
                // Skip system messages and bot messages
                let lower = text.toLowerCase();
                if (lower.includes('joined the chat') || lower.includes('left the chat')) return;
                let isBot = botPrefixes.some(e => text.startsWith(e)) || text.startsWith('VuTune');
                if (!isBot) {
                    msgs.push(text);
                }
            });
            return msgs;
        }
        """

        last_msgs = None

        while self.is_running and page:
            # Removed screenshot loop to prevent memory leaks and OOM crashes

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
                            logger.info(f"RECEIVED RAW CHAT: {msg}")
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
