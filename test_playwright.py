import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-blink-features=AutomationControlled'])
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = await context.new_page()
        print("Navigating to Douyin...")
        await page.goto("https://creator.douyin.com/", timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        await page.screenshot(path="douyin_debug.png", full_page=True)
        print("Screenshot saved to douyin_debug.png")
        html = await page.content()
        with open("douyin_debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("HTML saved to douyin_debug.html")
        await browser.close()

asyncio.run(run())
