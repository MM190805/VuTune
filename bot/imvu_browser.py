import asyncio
import logging
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

class IMVUBrowserClient:
    def __init__(self, session_data, credentials=None):
        self.session_data = session_data
        self.credentials = credentials or {}
        self.playwright = None
        self.browser = None
        self.context = None
        self.is_running = False
        self.pages = {} # room_id -> page
        self.tasks = {} # room_id -> task

    async def start(self):
        self.is_running = True
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-gpu',
                '--js-flags=--max-old-space-size=256'
            ]
        )
        self.context = await self.browser.new_context(viewport={'width': 1280, 'height': 720})
        
        cookies = []
        for k, v in self.session_data.get('cookies', {}).items():
            cookies.append({
                'name': k,
                'value': v,
                'domain': '.imvu.com',
                'path': '/'
            })
        if cookies:
            await self.context.add_cookies(cookies)
        self.username = self.session_data.get('username', 'VuTune')
        self.is_logged_in = True

    async def join_room(self, room_id, on_message_callback):
        if not self.context:
            return False
            
        try:
            page = await self.context.new_page()
            self.pages[room_id] = page
            
            async def abort_route(route):
                if route.request.resource_type in ["media"]:
                    await route.abort()
                else:
                    await route.continue_()
                    
            await page.route("**/*", abort_route)
            
            logger.info("Checking authentication...")
            await page.goto("https://www.imvu.com/next/login/")
            await page.wait_for_timeout(4000)
            
            if "login" in page.url.lower():
                logger.info("Cookies expired! Attempting automated login...")
                try:
                    await page.locator('input[type="text"], input[type="email"], input[name="username"]').first.fill(self.credentials.get('username', 'VuTune'))
                    await page.locator('input[type="password"], input[name="password"]').first.fill(self.credentials.get('password', ''))
                    await page.locator('button[type="submit"], button:has-text("Log in"), button:has-text("LOG IN")').first.click()
                    await page.wait_for_timeout(6000)
                    logger.info(f"Post-login URL: {page.url}")
                except Exception as e:
                    logger.error(f"Automated login failed: {e}")
            else:
                logger.info("Session cookies are valid.")

            logger.info(f"Navigating to room {room_id}...")
            await page.goto(f"https://www.imvu.com/next/chat/room-{room_id}/")
            
            # Wait for JOIN button and click it
            try:
                join_btn = page.locator('button', has_text="JOIN")
                await join_btn.wait_for(timeout=10000)
                await join_btn.click()
                logger.info("Clicked JOIN button.")
                # Give it a few seconds to load the chat UI
                await page.wait_for_timeout(5000)
            except Exception:
                logger.warning("No JOIN button found, might already be joined or different UI.")
            
            try:
                url = page.url
                title = await page.title()
                logger.info(f"DEBUG - Current URL: {url}")
                logger.info(f"DEBUG - Page Title: {title}")
                await page.screenshot(path="debug.png")
            except Exception as e:
                logger.error(f"Failed to take debug screenshot: {e}")
            
            # Start polling chat
            logger.info(f"Started chat listener for {room_id}...")
            task = asyncio.create_task(self._poll_chat(room_id, on_message_callback))
            self.tasks[room_id] = task
            return True
        except Exception as e:
            logger.error(f"Error joining room {room_id}: {e}")
            return False

    async def _poll_chat(self, room_id, on_message_callback):
        logger.info(f"Started chat listener for {room_id}...")
        page = self.pages.get(room_id)
        
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
            await asyncio.sleep(1.0)


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
