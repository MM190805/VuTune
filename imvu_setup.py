import asyncio
import json
from playwright.async_api import async_playwright

async def main():
    print("Launching browser...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={'width': 1280, 'height': 720})
        page = await context.new_page()
        
        print("Please log in to IMVU in the browser window.")
        print("Solve the 2FA if it asks.")
        
        await page.goto("https://www.imvu.com/next/login/")
        
        # Wait for user input in the console
        await asyncio.to_thread(input, "Press Enter here once you are fully logged in...")
        
        print("Saving full browser state (cookies + local storage)...")
        await context.storage_state(path="state.json")
            
        print("Saved state.json successfully!")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
