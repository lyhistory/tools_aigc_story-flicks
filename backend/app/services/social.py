import os
import json
import asyncio
import base64
from loguru import logger
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta

# In-memory store for active login sessions
# Structure: { "douyin": { "status": "pending", "qr_base64": "...", "task": <Task> } }
ACTIVE_LOGIN_SESSIONS: Dict[str, Dict[str, Any]] = {}

class BaseSocialPlatform:
    """Base class for social media automated publishing."""
    
    def __init__(self, platform_name: str):
        self.platform_name = platform_name
        self.auth_file = os.path.join(os.getcwd(), "backend", "auth", f"{self.platform_name}.json")
        os.makedirs(os.path.dirname(self.auth_file), exist_ok=True)
        # Persistent profile dir per platform — survives between restarts
        self.profile_dir = os.path.join(os.getcwd(), "backend", "auth", f"{self.platform_name}_profile")
        os.makedirs(self.profile_dir, exist_ok=True)

    async def is_authenticated(self) -> bool:
        """Check if saved auth file has valid, non-stale cookies."""
        if not os.path.exists(self.auth_file):
            return False
        try:
            import time
            # Auto-expire after 48h — forces re-login when session likely died
            age_hours = (time.time() - os.path.getmtime(self.auth_file)) / 3600
            if age_hours > 48:
                logger.info(f"{self.platform_name} session is {age_hours:.0f}h old, treating as expired")
                return False
            with open(self.auth_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data and "cookies" in data and len(data["cookies"]) > 0:
                    return True
        except Exception:
            pass
        return False

    def clear_auth(self):
        """Clears saved auth files and profile directories."""
        logger.warning(f"Clearing authenticated state for {self.platform_name}")
        if os.path.exists(self.auth_file):
            try:
                os.remove(self.auth_file)
                logger.info(f"Removed auth file: {self.auth_file}")
            except Exception as e:
                logger.warning(f"Failed to delete auth file: {e}")
        
        if os.path.exists(self.profile_dir):
            import shutil
            try:
                shutil.rmtree(self.profile_dir)
                os.makedirs(self.profile_dir, exist_ok=True)
                logger.info(f"Cleared profile directory: {self.profile_dir}")
            except Exception as e:
                logger.warning(f"Failed to clear profile dir: {e}")

    async def verify_logged_in(self, page: Page) -> bool:
        """Verify if the browser session is logged in. Returns False if redirected to login page.

        Strategy:
        1. Wait for page to settle (network idle preferred).
        2. Check URL for login/signin indicators.
        3. If on a login page but cookies exist, try a refresh — sometimes the first navigation
           triggers a redirect loop that resolves after one more load.
        4. Only then check for QR-code panels as a secondary signal.
        """
        # Wait for the page to finish loading (prefer networkidle, fall back to timeout)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            await page.wait_for_timeout(5000)

        url = page.url.lower()
        if "login" in url or "signin" in url or "auth" in url:
            logger.warning(f"{self.platform_name} initial URL suggests login page: {page.url}")
            # If we have saved cookies, try refreshing — sometimes the first visit redirects
            # but subsequent visits with cookies land on the correct page.
            if os.path.exists(self.auth_file):
                logger.info(f"{self.platform_name} Cookies found, attempting page refresh...")
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        await page.wait_for_timeout(3000)
                    url = page.url.lower()
                    if "login" not in url and "signin" not in url and "auth" not in url:
                        logger.info(f"{self.platform_name} Refresh resolved — now on non-login URL: {page.url}")
                        return True
                except Exception as e:
                    logger.warning(f"{self.platform_name} Refresh failed: {e}")
            return False
        return True


    async def _launch_publish_browser(self, p):
        """Launch a browser for publishing using Playwright bundled Chromium.

        Uses saved auth state (cookies) from the login flow.
        Runs headless=True with stealth args to reduce bot-detection.
        Returns (browser, context) so callers can close both.
        """
        stealth_args = [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--disable-infobars',
            '--disable-extensions',
            '--disable-gpu',
            '--window-size=1280,800',
            '--lang=zh-CN',
        ]

        context_args: dict = {
            "viewport": {'width': 1280, 'height': 800},
            "user_agent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            "locale": "zh-CN",
        }
        if os.path.exists(self.auth_file):
            try:
                with open(self.auth_file, "r") as f:
                    json.load(f)
                context_args["storage_state"] = self.auth_file
                logger.info(f"Loaded saved auth state from {self.auth_file}")
            except Exception as e:
                logger.warning(f"Could not load auth state: {e}")

        browser = await p.chromium.launch(
            headless=True,
            args=stealth_args,
        )
        context = await browser.new_context(**context_args)

        # Inject stealth scripts to mask webdriver fingerprint
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.navigator.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
        """)
        logger.info("Launched Playwright Chromium with stealth configuration.")
        return browser, context

    async def _init_browser(self, p, headless=True) -> tuple[Browser, BrowserContext]:
        """Launch a headless browser for login QR flow (not for publishing)."""
        browser = await p.chromium.launch(
            headless=headless, 
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-blink-features=AutomationControlled']
        )
        context_args = {
            "viewport": {'width': 1280, 'height': 800},
            "user_agent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        }
        if os.path.exists(self.auth_file):
            try:
                with open(self.auth_file, "r") as f:
                    json.load(f)
                context_args["storage_state"] = self.auth_file
            except:
                pass
        context = await browser.new_context(**context_args)
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        await context.add_init_script("""
            window.navigator.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        """)
        return browser, context

    async def _capture_loaded_qr(self, page: Page, selectors: list[str], timeout_ms: int = 30000) -> str:
        """Wait for a real QR image/canvas to render before taking a screenshot."""
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        last_error = None

        while asyncio.get_running_loop().time() < deadline:
            scopes = [page] + list(page.frames)
            for scope in scopes:
                for selector in selectors:
                    try:
                        locator = scope.locator(selector).first
                        if await locator.count() == 0:
                            continue

                        box = await locator.bounding_box()
                        if not box or box["width"] < 120 or box["height"] < 120:
                            continue

                        loaded = await locator.evaluate(
                            """el => {
                                const tag = el.tagName ? el.tagName.toLowerCase() : '';
                                if (tag === 'canvas') return el.width >= 120 && el.height >= 120;
                                if (tag === 'img') return el.complete && el.naturalWidth >= 120 && el.naturalHeight >= 120;
                                const img = el.querySelector && el.querySelector('img');
                                if (img) return img.complete && img.naturalWidth >= 120 && img.naturalHeight >= 120;
                                const canvas = el.querySelector && el.querySelector('canvas');
                                if (canvas) return canvas.width >= 120 && canvas.height >= 120;
                                return true;
                            }"""
                        )
                        if not loaded:
                            continue

                        await page.wait_for_timeout(800)
                        qr_src = await locator.get_attribute("src")
                        if qr_src and qr_src.startswith("data:image/"):
                            return qr_src

                        qr_bytes = await locator.screenshot()
                        return f"data:image/png;base64,{base64.b64encode(qr_bytes).decode('utf-8')}"
                    except Exception as e:
                        last_error = e

            await page.wait_for_timeout(500)

        raise TimeoutError(f"QR code did not finish loading within {timeout_ms}ms: {last_error}")

    async def _has_visible_qr(self, page: Page, selectors: list[str]) -> bool:
        for scope in [page] + list(page.frames):
            for selector in selectors:
                try:
                    locator = scope.locator(selector).first
                    if await locator.count() == 0:
                        continue
                    box = await locator.bounding_box()
                    if box and box["width"] >= 120 and box["height"] >= 120:
                        return True
                except Exception:
                    continue
        return False

    async def start_login_session(self) -> str:
        """Starts login flow, returns QR base64, and spawns wait task."""
        raise NotImplementedError

    async def _click_first_visible(self, page: Page, selectors: list[str], timeout_ms: int = 5000) -> bool:
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        while asyncio.get_running_loop().time() < deadline:
            for selector in selectors:
                try:
                    locator = page.locator(selector).first
                    if await locator.count() == 0:
                        continue
                    if not await locator.is_visible():
                        continue
                    await locator.click(timeout=1500)
                    return True
                except Exception:
                    continue
            await page.wait_for_timeout(300)
        return False

    async def _fill_first_visible(self, page: Page, selectors: list[str], value: str, timeout_ms: int = 5000) -> bool:
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        while asyncio.get_running_loop().time() < deadline:
            for selector in selectors:
                try:
                    locator = page.locator(selector).first
                    if await locator.count() == 0:
                        continue
                    if not await locator.is_visible():
                        continue
                    await locator.click(timeout=1500)
                    await page.keyboard.press("Control+A")
                    await page.keyboard.type(value)
                    await page.keyboard.press("Enter")
                    return True
                except Exception:
                    continue
            await page.wait_for_timeout(300)
        return False

    async def _click_near_text(self, page: Page, text: str, offset_x: int = 220, timeout_ms: int = 5000) -> bool:
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        while asyncio.get_running_loop().time() < deadline:
            try:
                target = page.get_by_text(text, exact=True).first
                if await target.count() > 0 and await target.is_visible():
                    box = await target.bounding_box()
                    if box:
                        await page.mouse.click(box["x"] + offset_x, box["y"] + (box["height"] / 2))
                        return True
            except Exception:
                pass
            await page.wait_for_timeout(300)
        return False

    async def _click_switch_near_label(self, page: Page, text: str, offset_x: int = 225, timeout_ms: int = 5000) -> bool:
        """Click a switch positioned to the right of an exact label and verify the click opens related controls."""
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        while asyncio.get_running_loop().time() < deadline:
            try:
                label = page.get_by_text(text, exact=True).first
                if await label.count() > 0 and await label.is_visible():
                    box = await label.bounding_box()
                    if box:
                        await page.mouse.click(box["x"] + offset_x, box["y"] + (box["height"] / 2))
                        await page.wait_for_timeout(800)
                        return True
            except Exception:
                pass
            await page.wait_for_timeout(300)
        return False

    async def _click_control_in_text_row(self, page: Page, text: str, timeout_ms: int = 5000) -> bool:
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        script = """
            label => {
                const visible = el => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const all = Array.from(document.querySelectorAll('body *'));
                const labelEl = all.find(el => visible(el) && (el.innerText || el.textContent || '').trim() === label);
                if (!labelEl) return false;

                let row = labelEl;
                for (let i = 0; i < 5 && row.parentElement; i += 1) {
                    const rect = row.getBoundingClientRect();
                    if (rect.width >= 260 && rect.height >= 24) break;
                    row = row.parentElement;
                }

                const candidates = Array.from(row.querySelectorAll(
                    '[role="switch"], button, input[type="checkbox"], .switch, [class*="switch"], [class*="Switch"]'
                )).filter(visible);
                let target = candidates.find(el => {
                    const rect = el.getBoundingClientRect();
                    const labelRect = labelEl.getBoundingClientRect();
                    return rect.left > labelRect.right - 2;
                }) || candidates[candidates.length - 1];

                if (!target) {
                    const rect = labelEl.getBoundingClientRect();
                    target = document.elementFromPoint(rect.right + 240, rect.top + rect.height / 2);
                }
                if (!target) return false;
                target.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
                target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                target.click();
                return true;
            }
        """
        while asyncio.get_running_loop().time() < deadline:
            try:
                if await page.evaluate(script, text):
                    return True
            except Exception:
                pass
            await page.wait_for_timeout(300)
        return False

    async def _fill_text_entry(self, page: Page, selectors: list[str], value: str, timeout_ms: int = 10000) -> bool:
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        while asyncio.get_running_loop().time() < deadline:
            for selector in selectors:
                try:
                    locator = page.locator(selector).first
                    if await locator.count() == 0 or not await locator.is_visible():
                        continue

                    await locator.click(timeout=1500)
                    tag_name = await locator.evaluate("el => el.tagName.toLowerCase()")
                    editable = await locator.evaluate("el => el.isContentEditable")
                    if tag_name in ("input", "textarea"):
                        await locator.fill(value, timeout=3000)
                    elif editable:
                        await page.keyboard.press("Control+A")
                        await page.keyboard.type(value)
                    else:
                        await locator.evaluate(
                            """(el, text) => {
                                el.textContent = text;
                                el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            }""",
                            value,
                        )
                    return True
                except Exception:
                    continue
            await page.wait_for_timeout(300)
        return False

    async def _fill_date_time_input(self, page: Page, selectors: list[str], value: str, timeout_ms: int = 5000) -> bool:
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        while asyncio.get_running_loop().time() < deadline:
            for selector in selectors:
                try:
                    locator = page.locator(selector).first
                    if await locator.count() == 0 or not await locator.is_visible():
                        continue
                    
                    logger.info(f"Attempting to set datetime input {selector} to {value}")
                    # 1. Click the input to focus and trigger any dropdowns
                    await locator.click(timeout=1500)
                    await page.wait_for_timeout(500)
                    
                    # 2. Try setting value via JS
                    await locator.evaluate("""(el, val) => {
                        el.value = val;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""", value)
                    
                    # 3. Try standard fill
                    try:
                        await locator.fill(value, timeout=1500)
                    except Exception:
                        pass
                        
                    # 4. Try keyboard typing
                    await page.keyboard.press("Control+A")
                    await page.keyboard.press("Backspace")
                    await page.keyboard.type(value)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(500)
                    
                    # 5. Look for any visible "确定", "确认", "OK" button in the date picker popup and click it
                    for btn_selector in [
                        'button:has-text("确定")',
                        'button:has-text("确认")',
                        'span:has-text("确定")',
                        'span:has-text("确认")',
                        'a:has-text("确定")',
                        '.ant-picker-ok button',
                        '.ant-btn-primary',
                    ]:
                        try:
                            btn = page.locator(btn_selector).filter(visible=True).first
                            if await btn.count() > 0:
                                await btn.click(timeout=1000)
                                logger.info(f"Clicked confirm button: {btn_selector}")
                                break
                        except Exception:
                            continue
                            
                    # 6. Click neutral area to close the dropdown if still open
                    await page.wait_for_timeout(500)
                    return True
                except Exception as e:
                    logger.warning(f"Error filling datetime input {selector}: {e}")
            await page.wait_for_timeout(500)
        return False

    async def _wait_until_upload_ready(
        self,
        page: Page,
        platform_label: str,
        timeout_ms: int = 180000,
        ready_markers: Optional[list[str]] = None,
    ) -> None:
        logger.info(f"Waiting for {platform_label} upload processing to start...")
        upload_markers = ["文件解析中", "上传中", "处理中", "正在上传", "正在处理", "取消上传"]
        ready_markers = ready_markers or []
        
        # 1. Wait up to 15 seconds for either upload markers or ready markers to appear
        start_time = asyncio.get_running_loop().time()
        started = False
        while asyncio.get_running_loop().time() - start_time < 15.0:
            try:
                body_text = await page.locator("body").inner_text(timeout=2000)
                has_upload = any(marker in body_text for marker in upload_markers) or any(f"{i}%" in body_text for i in range(101))
                has_ready = any(marker in body_text for marker in ready_markers)
                if has_upload or has_ready:
                    started = True
                    logger.info(f"{platform_label} upload detected (has_upload={has_upload}, has_ready={has_ready})")
                    break
            except Exception:
                pass
            await page.wait_for_timeout(1000)
            
        if not started:
            logger.warning(f"Could not confirm that {platform_label} upload has started, proceeding with wait loop anyway.")

        # 2. Wait for upload markers to clear and ready markers to be present
        logger.info(f"Waiting for {platform_label} upload processing to finish...")
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        while asyncio.get_running_loop().time() < deadline:
            try:
                body_text = await page.locator("body").inner_text(timeout=3000)
                
                # Check if we have any active upload markers (including percentages)
                has_upload_active = any(marker in body_text for marker in upload_markers) or any(f"{i}%" in body_text for i in range(1, 100))
                has_ready = any(marker in body_text for marker in ready_markers) if ready_markers else True
                
                if not has_upload_active and has_ready:
                    logger.info(f"{platform_label} upload is ready (ready markers found: {has_ready})")
                    return
            except Exception:
                pass
            await page.wait_for_timeout(2000)
        raise TimeoutError(f"{platform_label} upload did not finish processing within {timeout_ms // 1000}s")

    async def _wait_for_publish_result(
        self,
        page: Page,
        platform_label: str,
        screenshot_path: str,
        initial_url: str,
        timeout_ms: int = 60000,
    ) -> None:
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
        success_markers = [
            "发布成功", "发表成功", "提交成功", "发布已提交", "投稿成功",
            "定时发布成功", "定时发表成功", "保存成功", "草稿保存成功",
        ]
        blocking_markers = [
            "请完善", "不能为空", "失败", "错误", "刷新重试", "异常",
            "请选择", "请输入", "未通过",
        ]
        
        # Capture initial state of the page text (to detect changes)
        initial_text = ""
        try:
            initial_text = await page.locator("body").inner_text(timeout=2000)
        except Exception:
            pass

        logger.info(f"Waiting for {platform_label} publish confirmation...")
        while asyncio.get_running_loop().time() < deadline:
            try:
                # 1. Check if URL changed significantly (e.g. redirected to content list/manage)
                current_url = page.url
                if current_url != initial_url:
                    url_lower = current_url.lower()
                    if any(x in url_lower for x in ["manage", "list", "home", "index", "posts"]):
                        logger.info(f"{platform_label} redirected to success URL: {current_url}")
                        await page.wait_for_timeout(2000)
                        await page.screenshot(path=screenshot_path, full_page=True)
                        return

                # 2. Check for success notifications/modals/toasts (these usually appear on top)
                for toast_selector in [
                    '.ant-message', '.ant-notification', '.toast', '.message', 
                    '[class*="toast"]', '[class*="notification"]', '[class*="message"]',
                    '[role="alert"]', '.dy-toast', '.semi-toast'
                ]:
                    try:
                        toast = page.locator(toast_selector).filter(visible=True).first
                        if await toast.count() > 0:
                            toast_text = await toast.inner_text(timeout=500)
                            if any(marker in toast_text for marker in success_markers):
                                logger.info(f"{platform_label} success toast found: {toast_text}")
                                await page.screenshot(path=screenshot_path, full_page=True)
                                return
                            if any(marker in toast_text for marker in blocking_markers):
                                await page.screenshot(path=screenshot_path, full_page=True)
                                raise Exception(f"{platform_label} publish blocked by toast: {toast_text}")
                    except Exception as e:
                        if "publish blocked" in str(e):
                            raise
                        continue

                # 3. Check full page text change
                last_text = await page.locator("body").inner_text(timeout=2000)
                
                # Check for blocking markers anywhere first
                if any(marker in last_text and marker not in initial_text for marker in blocking_markers):
                    await page.screenshot(path=screenshot_path, full_page=True)
                    raise Exception(f"{platform_label} publish page shows a new blocking error message.")

                # Check for success markers that were either NOT in initial text, OR are in the text
                for marker in success_markers:
                    if marker in last_text:
                        if marker not in initial_text:
                            logger.info(f"{platform_label} success marker '{marker}' detected (new on page).")
                            await page.wait_for_timeout(2000)
                            await page.screenshot(path=screenshot_path, full_page=True)
                            return
                        
                        # If it was already there, check if it's inside a success/result panel
                        for success_panel in ['.success', '.result', '[class*="success"]', '[class*="result"]', '.publish-success']:
                            try:
                                panel = page.locator(success_panel).filter(visible=True).first
                                if await panel.count() > 0:
                                    panel_text = await panel.inner_text(timeout=500)
                                    if marker in panel_text:
                                        logger.info(f"{platform_label} success marker found in panel: {success_panel}")
                                        await page.screenshot(path=screenshot_path, full_page=True)
                                        return
                            except Exception:
                                continue
            except Exception as e:
                if "blocked" in str(e) or "blocking" in str(e):
                    raise
                logger.warning(f"Error checking publish result: {e}")
            await page.wait_for_timeout(1500)

        # Fallback/default success check
        await page.screenshot(path=screenshot_path, full_page=True)
        logger.warning(f"{platform_label} publish wait finished without definitive success marker. Assuming success.")

    async def _enable_platform_schedule(self, page: Page, scheduled_for: Optional[datetime]) -> None:
        if not scheduled_for:
            return

        local_time = scheduled_for.astimezone(timezone(timedelta(hours=8)))
        date_value = local_time.strftime("%Y-%m-%d")
        time_value = local_time.strftime("%H:%M")
        datetime_value = local_time.strftime("%Y-%m-%d %H:%M")

        logger.info(f"Enabling platform scheduled publish for {self.platform_name}: {datetime_value}")
        clicked = await self._click_first_visible(page, [
            'label:has-text("定时发布")',
            'label:has-text("定时")',
            'button:has-text("定时发布")',
            'span:has-text("定时发布")',
            'div:has-text("定时发布")',
            'text="定时发布"',
        ], timeout_ms=8000)
        if not clicked:
            raise Exception(f"Could not find 定时发布 option on {self.platform_name} publish page.")

        await page.wait_for_timeout(1000)
        
        # Try combined first
        if await self._fill_date_time_input(page, [
            'input[placeholder*="发布时间"]',
            'input[placeholder*="日期和时间"]',
            'input[placeholder*="发布"]',
            '[class*="datetime"] input',
        ], datetime_value, timeout_ms=5000):
            return

        # Fallback to separate
        date_filled = await self._fill_date_time_input(page, [
            'input[placeholder*="日期"]',
            'input[placeholder*="选择日期"]',
            'input[placeholder*="年月日"]',
            'input[type="date"]',
        ], date_value, timeout_ms=4000)
        time_filled = await self._fill_date_time_input(page, [
            'input[placeholder*="时间"]',
            'input[placeholder*="选择时间"]',
            'input[placeholder*="时分"]',
            'input[type="time"]',
        ], time_value, timeout_ms=4000)

        if not (date_filled and time_filled):
            raise Exception(f"Could not set scheduled publish time on {self.platform_name} page.")

    async def _enable_xhs_schedule(self, page: Page, scheduled_for: Optional[datetime]) -> None:
        if not scheduled_for:
            return

        local_time = scheduled_for.astimezone(timezone(timedelta(hours=8)))
        date_value = local_time.strftime("%Y-%m-%d")
        time_value = local_time.strftime("%H:%M")
        datetime_value = local_time.strftime("%Y-%m-%d %H:%M")
        compact_datetime = local_time.strftime("%Y-%m-%d %H:%M")
        logger.info(f"Enabling Xiaohongshu scheduled publish: {datetime_value}")

        await page.get_by_text("更多设置", exact=True).scroll_into_view_if_needed(timeout=5000)
        clicked = await self._click_switch_near_label(page, "定时发布", offset_x=225, timeout_ms=5000)
        if not clicked:
            clicked = await self._click_control_in_text_row(page, "定时发布", timeout_ms=5000)
        if not clicked:
            clicked = await self._click_near_text(page, "定时发布", offset_x=225, timeout_ms=5000)
        if not clicked:
            raise Exception("Could not enable Xiaohongshu 定时发布 switch.")

        await page.wait_for_timeout(1000)
        await self._set_xhs_datetime_from_visible_row(page, compact_datetime)
        body_text = await page.locator("body").inner_text(timeout=3000)
        if date_value in body_text and time_value in body_text:
            return

        if await self._fill_date_time_input(page, [
            'input[placeholder*="发布时间"]',
            'input[placeholder*="定时"]',
            'input[placeholder*="选择日期"]',
            'input[placeholder*="请选择"]',
        ], datetime_value, timeout_ms=8000):
            return

        # Fallback if combined selector doesn't work
        date_filled = await self._fill_date_time_input(page, [
            'input[placeholder*="日期"]',
            'input[placeholder*="选择日期"]',
        ], date_value, timeout_ms=4000)
        time_filled = await self._fill_date_time_input(page, [
            'input[placeholder*="时间"]',
            'input[placeholder*="选择时间"]',
        ], time_value, timeout_ms=4000)
        if not (date_filled and time_filled):
            raise Exception("Could not set scheduled publish time on xiaohongshu page.")

        body_text = await page.locator("body").inner_text(timeout=3000)
        if date_value not in body_text or time_value not in body_text:
            raise Exception("Xiaohongshu scheduled publish time did not update to the requested value.")

    async def _set_xhs_datetime_from_visible_row(self, page: Page, datetime_value: str) -> None:
        try:
            label = page.get_by_text("定时发布", exact=True).first
            await label.wait_for(state="visible", timeout=3000)
            box = await label.bounding_box()
            if not box:
                return
            # The XHS datetime display is the rectangular field to the right of the switch.
            await page.mouse.click(box["x"] + 430, box["y"] + (box["height"] / 2))
            await page.wait_for_timeout(800)
            await page.keyboard.press("Control+A")
            await page.keyboard.type(datetime_value)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(800)
            for btn_selector in [
                'button:has-text("确定")',
                'button:has-text("确认")',
                'text="确定"',
                'text="确认"',
            ]:
                try:
                    btn = page.locator(btn_selector).filter(visible=True).first
                    if await btn.count() > 0:
                        await btn.click(timeout=1000)
                        break
                except Exception:
                    continue
            await page.wait_for_timeout(1000)
        except Exception as e:
            logger.warning(f"Could not directly type Xiaohongshu datetime: {e}")

    async def publish(self, video_path: str, title: str, description: str, scheduled_for: Optional[datetime] = None):
        """Uploads video."""
        raise NotImplementedError






class DouyinPublisher(BaseSocialPlatform):
    def __init__(self):
        super().__init__("douyin")

    async def is_authenticated(self) -> bool:
        if not await super().is_authenticated():
            return False
        try:
            async with async_playwright() as p:
                browser, context = await self._launch_publish_browser(p)
                page = await context.new_page()
                try:
                    await page.goto("https://creator.douyin.com/creator-micro/content/upload", timeout=60000, wait_until="domcontentloaded")
                    await self._click_first_visible(page, ['button:has-text("我知道了")', 'text="我知道了"'], timeout_ms=2000)
                    authenticated = await self.verify_logged_in(page)
                    if not authenticated and os.path.exists(self.auth_file):
                        # Try refresh — cookies may be valid but first visit triggers redirect
                        logger.info("Douyin auth check inconclusive, attempting refresh...")
                        try:
                            await page.reload(wait_until="domcontentloaded", timeout=30000)
                            await self._click_first_visible(page, ['button:has-text("我知道了")', 'text="我知道了"'], timeout_ms=2000)
                            authenticated = await self.verify_logged_in(page)
                        except Exception as e:
                            logger.warning(f"Douyin refresh failed: {e}")
                    if not authenticated:
                        self.clear_auth()
                    return authenticated
                finally:
                    await page.close()
                    await context.close()
                    await browser.close()
        except Exception as e:
            logger.warning(f"Douyin live auth check failed: {e}")
            return False

    async def verify_logged_in(self, page: Page) -> bool:
        # First use the base class logic (URL check + refresh)
        if not await super().verify_logged_in(page):
            return False

        # Only check for QR panels if we are NOT on a login URL
        # IMPORTANT: Do NOT use bare "canvas" — it matches decorative/animation canvases everywhere.
        if await self._has_visible_qr(page, [
            ".web-login-scan-qr img",
            "img.login-scan-qr",
            "img[src*='qrcode']",
        ]):
            logger.warning("Douyin QR login panel detected; session is not authenticated.")
            return False
        try:
            body_text = await page.locator("body").inner_text(timeout=2000)
            if "扫码登录" in body_text or "验证码登录" in body_text or "登录/注册" in body_text:
                logger.warning("Douyin login text detected; session is not authenticated.")
                return False
        except Exception:
            pass
        return True

    async def _enter_high_quality_upload(self, page: Page) -> None:
        clicked = await self._click_first_visible(page, [
            'button:has-text("高清发布")',
            'a:has-text("高清发布")',
            'div:has-text("高清发布")',
            'span:has-text("高清发布")',
            'text="高清发布"',
        ], timeout_ms=8000)
        if clicked:
            logger.info("Clicked Douyin 高清发布 entry.")
            await page.wait_for_timeout(2000)

    async def start_login_session(self) -> str:
        async def _wait_for_login(browser: Browser, context: BrowserContext, page: Page):
            try:
                # Phase 1: Verify QR code is actually displayed on screen before waiting
                await page.wait_for_timeout(3000)  # Wait for initial render
                
                # Check if QR element exists (confirms we're on login screen, not already logged in)
                qr_element_present = False
                try:
                    qr_locator = page.get_by_role("img", name="二维码")
                    if await qr_locator.count() > 0:
                        qr_element_present = True
                except:
                    pass
                
                if not qr_element_present:
                    # Try alternative selector
                    try:
                        canvas_locator = page.locator(".web-login-scan-qr img, img.login-scan-qr, img[src*='qrcode']")
                        if await canvas_locator.count() > 0:
                            qr_element_present = True
                    except:
                        pass
                
                if not qr_element_present:
                    logger.info(f"Douyin: No QR detected, checking if already logged in...")
                    if await self.verify_logged_in(page):
                        await context.storage_state(path=self.auth_file)
                        ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "success"
                        return
                    else:
                        raise Exception("Could not display Douyin QR login page - check browser setup")
                
                logger.info(f"Douyin: QR confirmed on screen. Waiting for scan (up to 180s)...")
                
                # Phase 2: Monitor for login completion via QR disappearance + URL change
                for attempt in range(180):
                    await asyncio.sleep(1)
                    
                    # Check if QR code element is still visible
                    qr_still_visible = False
                    try:
                        qr_locator = page.get_by_role("img", name="二维码")
                        qr_still_visible = await qr_locator.count() > 0
                    except Exception as e:
                        logger.debug(f"Checking Douyin QR visibility failed: {e}")
                        pass
                    
                    # QR disappeared → likely scanned!
                    if not qr_still_visible and qr_element_present:
                        logger.info(f"Douyin: QR code disappeared at attempt {attempt} (scan detected!)")
                        
                        # Small settle delay
                        await page.wait_for_timeout(1000)
                        
                        # Final URL validation - must be non-login douyin.com domain
                        final_url = page.url.lower()
                        for bad_kw in ['login', 'signin', 'qrcode', 'auth']:
                            if bad_kw in final_url:
                                logger.warning(f"Douyin: After QR disappeared, landed on login-like page again: {final_url}")
                                continue  # Keep waiting
                        
                        # Additional verification: check that page contains dashboard markers
                        try:
                            dashboard_text = await page.evaluate("() => document.body?.innerText || ''")
                            dashboard_markers = ["创作者中心", "创作中心", "数据", "作品", "发布"]
                            if not any(marker in dashboard_text for marker in dashboard_markers):
                                # Could be a temporary redirect page - keep waiting
                                logger.warning(f"Douyin: After QR disappear, found no dashboard markers, continuing to wait...")
                                continue
                        except Exception as e:
                            logger.debug(f"Could not verify dashboard markers: {e}")
                        
                        # Save validated auth state (give brief moment for any async writes to settle)
                        try:
                            await context.storage_state(path=self.auth_file)
                            await asyncio.sleep(0.5)  # Small delay to ensure file flush
                        except Exception as e:
                            logger.error(f"Failed to save Douyin storage state: {e}")
                            raise
                        
                        ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "success"
                        return
                    
                    # Edge case: URL changed even if QR tracking missed. Only save after
                    # the platform-specific logged-in UI check passes.
                    if 'creator.douyin.com' in page.url.lower() and \
                       'login' not in page.url.lower() and 'signin' not in page.url.lower() and \
                       await self.verify_logged_in(page):
                        await context.storage_state(path=self.auth_file)
                        ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "success"
                        return
                
                raise Exception("Douyin login timed out after 180s - please scan QR within ~60s")
            
            except Exception as e:
                logger.error(f"Douyin login error: {e}")
                ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "failed"
                raise
            finally:
                await browser.close()
                await asyncio.sleep(300)
                        # Cleanup existing session
        if self.platform_name in ACTIVE_LOGIN_SESSIONS:
            del ACTIVE_LOGIN_SESSIONS[self.platform_name]

        # Connect/Reconnect should start a fresh QR login, not trust old storage_state.
        self.clear_auth()
        
        ACTIVE_LOGIN_SESSIONS[self.platform_name] = {"status": "starting", "qr_base64": None}
        p = await async_playwright().start()
        browser, context = await self._init_browser(p, headless=True)
        page = await context.new_page()
        
        try:
            await page.goto("https://creator.douyin.com/creator-micro/content/upload", timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            
            # Check if already logged in
            if await self.verify_logged_in(page):
                logger.info("Douyin already logged in on session start!")
                # Save auth state from current context so subsequent debug uploads work
                await context.storage_state(path=self.auth_file)
                ACTIVE_LOGIN_SESSIONS[self.platform_name] = {"status": "success", "qr_base64": None}
                await browser.close()
                return "ALREADY_LOGGED_IN"
            
            # Show QR login interface
            scan_login_tab = page.get_by_text("扫码登录", exact=True).first
            if await scan_login_tab.count() > 0:
                await scan_login_tab.click(timeout=3000)
            await page.wait_for_timeout(1000)
            
            # Get QR element
            qr_locator = page.get_by_role("img", name="二维码").first
            if not await qr_locator.count():
                qr_locator = page.locator(".web-login-scan-qr img, img.login-scan-qr, img[src*='qrcode']").first
            
            await qr_locator.wait_for(state="visible", timeout=15000)
            
            # Capture QR as base64
            qr_src = await qr_locator.get_attribute("src")
            if qr_src and qr_src.startswith("data:image/"):
                qr_b64 = qr_src
            else:
                qr_bytes = await qr_locator.screenshot()
                qr_b64 = f"data:image/png;base64,{base64.b64encode(qr_bytes).decode()}"
            
            ACTIVE_LOGIN_SESSIONS[self.platform_name]["qr_base64"] = qr_b64
            ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "pending"
            
            asyncio.create_task(_wait_for_login(browser, context, page))
            return ACTIVE_LOGIN_SESSIONS[self.platform_name]["qr_base64"]
        
        except Exception as e:
            await browser.close()
            ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "failed"
            raise e

    async def publish(self, video_path: str, title: str, description: str, scheduled_for: Optional[datetime] = None):
        """Publish video to Douyin."""
        async with async_playwright() as p:
            browser, context = await self._launch_publish_browser(p)
            page = await context.new_page()
            try:
                logger.info("Opening Douyin publisher...")
                await page.goto("https://creator.douyin.com/creator-micro/content/upload", timeout=60000, wait_until="domcontentloaded")
                await self._click_first_visible(page, ['button:has-text("我知道了")', 'text="我知道了"'], timeout_ms=3000)
                
                # Pre-flight: if on login page but cookies exist, refresh once
                if "login" in page.url.lower() and os.path.exists(self.auth_file):
                    logger.info("Douyin on login page after goto — refreshing with cookies...")
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                    await self._click_first_visible(page, ['button:has-text("我知道了")', 'text="我知道了"'], timeout_ms=3000)

                if not await self.verify_logged_in(page):
                    self.clear_auth()
                    raise Exception("Not logged in to Douyin. Please scan QR code first.")

                await self._enter_high_quality_upload(page)
                
                logger.info("Uploading video file...")
                file_input = page.locator("input[type='file']").first
                await file_input.set_input_files(video_path)
                
                logger.info("Waiting for upload to process (may take 1-2 min for large files)...")
                await page.locator('input[placeholder*="标题"], textarea[placeholder*="简介"], [contenteditable="true"]').first.wait_for(state="visible", timeout=180000)
                await self._wait_until_upload_ready(
                    page,
                    "Douyin",
                    ready_markers=["重新上传", "预览视频", "设置封面", "选择封面"],
                )
                await self._click_first_visible(page, ['button:has-text("我知道了")', 'text="我知道了"'], timeout_ms=3000)

                title_filled = await self._fill_text_entry(page, [
                    'input[placeholder*="作品作品标题"]',
                    'input[placeholder*="作品标题"]',
                    'input[placeholder*="标题"]',
                    'textarea[placeholder*="作品标题"]',
                    '[contenteditable="true"][data-placeholder*="标题"]',
                ], title, timeout_ms=10000)
                if not title_filled:
                    raise Exception("Could not find visible Douyin title field after upload.")

                desc_filled = await self._fill_text_entry(page, [
                    'textarea[placeholder*="作品简介"]',
                    'textarea[placeholder*="简介"]',
                    'textarea[placeholder*="描述"]',
                    '[contenteditable="true"][data-placeholder*="简介"]',
                    '[contenteditable="true"][data-placeholder*="描述"]',
                    '[contenteditable="true"]',
                ], description, timeout_ms=10000)
                if not desc_filled:
                    raise Exception("Could not find visible Douyin description field after upload.")

                await self._enable_platform_schedule(page, scheduled_for)
                
                logger.info("Clicking publish button...")
                publish_btn = page.get_by_role("button", name="发布", exact=True).first
                await publish_btn.wait_for(state="visible", timeout=90000)
                await publish_btn.click(timeout=90000)
                logger.info("Douyin publish clicked!")
                await self._wait_for_publish_result(
                    page, 
                    "Douyin", 
                    "douyin_after_publish.png", 
                    "https://creator.douyin.com/creator-micro/content/upload", 
                    timeout_ms=45000
                )
            except Exception as e:
                logger.error(f"Douyin publish failed: {e}")
                await page.screenshot(path="douyin_error.png")
                raise e
            finally:
                await page.close()
                await context.close()
                await browser.close()

    async def debug_upload(self, video_path: str, title: str, description: str, scheduled_for: Optional[datetime] = None) -> str:
        """Upload + fill all fields + set scheduler, but do NOT click publish. Returns screenshot path."""
        screenshot_path = "douyin_debug_upload.png"
        # First, re-validate auth fresh - in case cookies got stale since last check
        temp_publisher = DouyinPublisher()
        if not await temp_publisher.is_authenticated():
            logger.error("Debug upload failed: Douyin authentication not verified")
            raise Exception("Not logged in to Douyin. Please reconnect first.")
        
        async with async_playwright() as p:
            browser, context = await self._launch_publish_browser(p)
            page = await context.new_page()
            try:
                logger.info("[DEBUG] Opening Douyin publisher...")
                await page.goto("https://creator.douyin.com/creator-micro/content/upload", timeout=60000, wait_until="domcontentloaded")
                await self._click_first_visible(page, ['button:has-text("我知道了")', 'text="我知道了"'], timeout_ms=3000)

                # Pre-flight: if on login page but cookies exist, refresh once
                if "login" in page.url.lower() and os.path.exists(self.auth_file):
                    logger.info("Douyin debug: on login page after goto — refreshing with cookies...")
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                    await self._click_first_visible(page, ['button:has-text("我知道了")', 'text="我知道了"'], timeout_ms=3000)

                # Additional pre-flight verification: test if saved cookies actually work
                # Navigate to a non-editing domain URL to check auth state without side effects
                try:
                    test_url = "https://creator.douyin.com/creator/home"
                    await page.goto(test_url, timeout=15000, wait_until="networkidle")
                    # If we landed on login page after going to home, auth is stale
                    if "login" in page.url.lower() or "qrcode" in page.url.lower():
                        logger.warning("Douyin: Auth stale - test URL redirected to login")
                        self.clear_auth()
                        raise Exception("Authentication expired - please re-login via QR code")
                    # Return to upload page
                    await page.goto("https://creator.douyin.com/creator-micro/content/upload", timeout=60000, wait_until="domcontentloaded")
                    await self._click_first_visible(page, ['button:has-text("我知道了")', 'text="我知道了"'], timeout_ms=3000)
                except Exception as e:
                    logger.debug(f"Douyin auth pre-flight check: {e}")

                if not await self.verify_logged_in(page):
                    self.clear_auth()
                    raise Exception("Not logged in to Douyin. Please scan QR code first.")

                await self._enter_high_quality_upload(page)

                logger.info("[DEBUG] Uploading video file...")
                file_input = page.locator("input[type='file']").first
                await file_input.set_input_files(video_path)

                await page.locator('input[placeholder*="标题"], textarea[placeholder*="简介"], [contenteditable="true"]').first.wait_for(state="visible", timeout=180000)
                await self._wait_until_upload_ready(page, "Douyin", ready_markers=["重新上传", "预览视频", "设置封面", "选择封面"])
                await self._click_first_visible(page, ['button:has-text("我知道了")', 'text="我知道了"'], timeout_ms=3000)

                await self._fill_text_entry(page, [
                    'input[placeholder*="作品作品标题"]', 'input[placeholder*="作品标题"]',
                    'input[placeholder*="标题"]', 'textarea[placeholder*="作品标题"]',
                    '[contenteditable="true"][data-placeholder*="标题"]',
                ], title, timeout_ms=10000)

                await self._fill_text_entry(page, [
                    'textarea[placeholder*="作品简介"]', 'textarea[placeholder*="简介"]',
                    'textarea[placeholder*="描述"]', '[contenteditable="true"][data-placeholder*="简介"]',
                    '[contenteditable="true"][data-placeholder*="描述"]', '[contenteditable="true"]',
                ], description, timeout_ms=10000)

                await self._enable_platform_schedule(page, scheduled_for)

                # Scroll to show publish button area in screenshot
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1500)
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"[DEBUG] Douyin debug screenshot saved to {screenshot_path}")
                return screenshot_path
            except Exception as e:
                logger.error(f"[DEBUG] Douyin debug upload failed: {e}")
                await page.screenshot(path=screenshot_path)
                raise e
            finally:
                await page.close()
                await context.close()
                await browser.close()

class XiaohongshuPublisher(BaseSocialPlatform):
    def __init__(self):
        super().__init__("xiaohongshu")

    async def verify_logged_in(self, page: Page) -> bool:
        if not await super().verify_logged_in(page):
            return False
        if await self._has_visible_qr(page, [
            ".qrcode-img",
            ".login-qrcode",
            "img.qrcode",
            "img[src*='qrcode']",
        ]):
            logger.warning("Xiaohongshu QR login panel detected; session is not authenticated.")
            return False
        try:
            body_text = await page.locator("body").inner_text(timeout=3000)
            login_markers = ["扫码登录", "APP扫一扫登录", "验证码登录", "登录后可发布", "手机号登录"]
            if any(marker in body_text for marker in login_markers):
                logger.warning("Xiaohongshu login text detected; session is not authenticated.")
                return False
            logged_in_markers = ["发布笔记", "笔记管理", "创作服务平台", "数据看板"]
            if not any(marker in body_text for marker in logged_in_markers):
                logger.warning("Xiaohongshu logged-in page markers not found; treating session as unauthenticated.")
                return False
        except Exception as e:
            logger.warning(f"Xiaohongshu logged-in verification failed: {e}")
            return False
        return True

    async def start_login_session(self) -> str:
        async def _wait_for_login(browser: Browser, context: BrowserContext, page: Page):
            try:
                # Phase 1: Verify QR code is actually displayed before waiting
                await page.wait_for_timeout(3000)  # Wait for initial render
                
                # Check if QR element exists (confirms we're on login screen, not already logged in)
                qr_element_present = False
                try:
                    qrcode_locator = page.locator('.qrcode-img, .login-qrcode, [data-testid="qrcode"]')
                    if await qrcode_locator.count() > 0:
                        qr_element_present = True
                except Exception as e:
                    logger.debug(f"XHS QR element check failed: {e}")
                
                if not qr_element_present:
                    # Alternative: look for "APP扫一扫登录" text indicator
                    try:
                        scan_text = page.get_by_text("APP扫一扫登录", exact=True)
                        if await scan_text.count() > 0:
                            qr_element_present = True
                    except:
                        pass
                
                if not qr_element_present:
                    logger.info(f"XHS: No QR detected, checking if already logged in...")
                    if await self.verify_logged_in(page):
                        await context.storage_state(path=self.auth_file)
                        ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "success"
                        return
                    else:
                        raise Exception("Could not display XHS QR login page - check browser setup")
                
                logger.info(f"XHS: QR confirmed on screen. Waiting for scan (up to 180s)...")
                
                # Phase 2: Monitor for completion via QR disappearance + URL change
                for attempt in range(180):
                    await asyncio.sleep(1)
                    
                    # Check if QR element still visible
                    qr_still_visible = False
                    try:
                        qrcode_locator = page.locator('.qrcode-img, .login-qrcode, [data-testid="qrcode"]')
                        qr_still_visible = await qrcode_locator.count() > 0
                    except Exception as e:
                        logger.debug(f"Checking XHS QR visibility failed: {e}")
                    
                    # QR disappeared → likely scanned!
                    if not qr_still_visible and qr_element_present:
                        logger.info(f"XHS: QR code disappeared at attempt {attempt} (scan detected!)")
                        
                        await page.wait_for_timeout(1000)
                        
                        # Final URL validation – must be non-login XHS domain
                        final_url = page.url.lower()
                        bad_keywords = ['login', 'signin', 'qrcode', 'auth']
                        if any(kw in final_url for kw in bad_keywords):
                            logger.warning(f"After QR disappeared, landed on XHS login-like again: {final_url}")
                            continue
                        
                        # Save validated auth state (give brief moment for any async writes to settle)
                        try:
                            await context.storage_state(path=self.auth_file)
                            await asyncio.sleep(0.5)  # Small delay to ensure file flush
                        except Exception as e:
                            logger.error(f"Failed to save XHS storage state: {e}")
                            raise
                        
                        ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "success"
                        return
                    
                    # Edge: URL cleanly changed to creator domain even if QR tracking missed
                    final_url = page.url.lower()
                    if ('creator.xiaohongshu.com' in final_url and
                        'login' not in final_url and
                        'signin' not in final_url and
                        'auth' not in final_url and
                        await self.verify_logged_in(page)):
                        await context.storage_state(path=self.auth_file)
                        ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "success"
                        return
                
                raise Exception("XHS login timed out after 180s - please scan QR within ~60s")
            
            except Exception as e:
                logger.error(f"XHS login error: {e}")
                ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "failed"
                raise
            finally:
                await browser.close()
                await asyncio.sleep(300)
                if self.platform_name in ACTIVE_LOGIN_SESSIONS:
                    del ACTIVE_LOGIN_SESSIONS[self.platform_name]

        if self.platform_name in ACTIVE_LOGIN_SESSIONS:
            del ACTIVE_LOGIN_SESSIONS[self.platform_name]

        # Connect/Reconnect should start a fresh QR login, not trust old storage_state.
        self.clear_auth()

        ACTIVE_LOGIN_SESSIONS[self.platform_name] = {"status": "starting", "qr_base64": None}
        p = await async_playwright().start()
        browser, context = await self._init_browser(p, headless=True)
        page = await context.new_page()
        try:
            await page.goto("https://creator.xiaohongshu.com/creator/home", timeout=60000, wait_until="domcontentloaded")
            
            await page.wait_for_timeout(3000)
            
            if await self.verify_logged_in(page):
                logger.info("Xiaohongshu already logged in on session start!")
                await context.storage_state(path=self.auth_file)
                ACTIVE_LOGIN_SESSIONS[self.platform_name] = {"status": "success", "qr_base64": None}
                await browser.close()
                return "ALREADY_LOGGED_IN"
                
            try:
                # Click the tiny switch icon in the corner
                login_box = page.locator("div[class*='login-box']").first
                switch_img = login_box.locator("img.css-wemwzq").first
                if await switch_img.count() > 0:
                    await switch_img.click(timeout=3000)
                else:
                    qr_tab = page.locator('div, span').filter(has_text="扫码登录").first
                    if await qr_tab.count() > 0:
                        await qr_tab.click(timeout=3000)
                
                await page.wait_for_timeout(1000)
                
                primary_qr = page.locator('.login-box-container').get_by_text("APP扫一扫登录").locator("xpath=..//following-sibling::div//img").first
                try:
                    await primary_qr.wait_for(state="visible", timeout=10000)
                    qr_locator = primary_qr
                except:
                    # Removed bare "canvas" to avoid false positives
                    qr_locator = page.locator(".qrcode-img, img.qrcode, img[src*='qrcode']").first
                    await qr_locator.wait_for(state="visible", timeout=10000)
                
                qr_src = await qr_locator.get_attribute("src")
                if qr_src and qr_src.startswith("data:image/"):
                    qr_b64 = qr_src
                else:
                    qr_bytes = await qr_locator.screenshot()
                    qr_b64 = f"data:image/png;base64,{base64.b64encode(qr_bytes).decode()}"
            except Exception as e:
                logger.warning(f"QR locator failed, taking full page screenshot: {e}")
                qr_bytes = await page.screenshot(full_page=True)
                qr_b64 = f"data:image/png;base64,{base64.b64encode(qr_bytes).decode()}"
                
            ACTIVE_LOGIN_SESSIONS[self.platform_name]["qr_base64"] = qr_b64
            ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "pending"
            
            asyncio.create_task(_wait_for_login(browser, context, page))
            return ACTIVE_LOGIN_SESSIONS[self.platform_name]["qr_base64"]
        except Exception as e:
            await browser.close()
            ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "failed"
            raise e

    async def publish(self, video_path: str, title: str, description: str, scheduled_for: Optional[datetime] = None):
        """Publish video to Xiaohongshu."""
        async with async_playwright() as p:
            browser, context = await self._launch_publish_browser(p)
            page = await context.new_page()
            try:
                logger.info("Opening Xiaohongshu publisher...")
                await page.goto("https://creator.xiaohongshu.com/publish/publish?source=official", timeout=60000, wait_until="domcontentloaded")
                # Pre-flight: if on login page but cookies exist, refresh once
                if "login" in page.url.lower() and os.path.exists(self.auth_file):
                    logger.info("XHS on login page after goto — refreshing with cookies...")
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                if not await self.verify_logged_in(page):
                    self.clear_auth()
                    raise Exception("Not logged in to Xiaohongshu. Please scan QR code first.")
                
                logger.info("Uploading Xiaohongshu video file...")
                file_input = page.locator('input[type="file"]').first
                await file_input.set_input_files(video_path, timeout=30000)
                
                title_input = page.locator('input.title-input, input[placeholder*="标题"]')
                await title_input.first.wait_for(state="visible", timeout=180000)
                await self._wait_until_upload_ready(page, "Xiaohongshu")
                await title_input.first.fill(title)
                
                desc_filled = await self._fill_text_entry(page, [
                    'textarea[placeholder*="正文"]',
                    'textarea[placeholder*="描述"]',
                    '[contenteditable="true"][data-placeholder*="正文"]',
                    '[contenteditable="true"][placeholder*="正文"]',
                    '.ql-editor',
                    '#post-textarea',
                    '.post-content',
                ], description, timeout_ms=8000)
                if not desc_filled:
                    logger.warning("Could not find a visible Xiaohongshu description field; continuing with title only.")

                await self._enable_xhs_schedule(page, scheduled_for)
                
                publish_btn = page.locator('button:has-text("发布")').first
                await publish_btn.wait_for(state="visible", timeout=90000)
                await publish_btn.click(timeout=90000)
                logger.info("Xiaohongshu publish clicked!")
                await self._wait_for_publish_result(
                    page, 
                    "Xiaohongshu", 
                    "xhs_after_publish.png", 
                    "https://creator.xiaohongshu.com/publish/publish?source=official", 
                    timeout_ms=45000
                )
            except Exception as e:
                logger.error(f"Xiaohongshu publish failed: {e}")
                await page.screenshot(path="xhs_error.png")
                raise e
            finally:
                await page.close()
                await context.close()
                await browser.close()

    async def debug_upload(self, video_path: str, title: str, description: str, scheduled_for: Optional[datetime] = None) -> str:
        """Upload + fill all fields + set scheduler, but do NOT click publish. Returns screenshot path."""
        screenshot_path = "xhs_debug_upload.png"
        async with async_playwright() as p:
            browser, context = await self._launch_publish_browser(p)
            page = await context.new_page()
            try:
                logger.info("[DEBUG] Opening Xiaohongshu publisher...")
                await page.goto("https://creator.xiaohongshu.com/publish/publish?source=official", timeout=60000, wait_until="domcontentloaded")
                # Pre-flight: if on login page but cookies exist, refresh once
                if "login" in page.url.lower() and os.path.exists(self.auth_file):
                    logger.info("XHS debug: on login page after goto — refreshing with cookies...")
                    await page.reload(wait_until="domcontentloaded", timeout=30000)

                # Additional pre-flight verification for Xiaohongshu
                try:
                    test_url = "https://creator.xiaohongshu.com/creator/home"
                    await page.goto(test_url, timeout=15000, wait_until="networkidle")
                    if "login" in page.url.lower() or "qrcode" in page.url.lower():
                        logger.warning("XHS: Auth stale - test URL redirected to login")
                        self.clear_auth()
                        raise Exception("Authentication expired - please re-login via QR code")
                    await page.goto("https://creator.xiaohongshu.com/publish/publish?source=official", timeout=60000, wait_until="domcontentloaded")
                except Exception as e:
                    logger.debug(f"XHS auth pre-flight check: {e}")

                if not await self.verify_logged_in(page):
                    self.clear_auth()
                    raise Exception("Not logged in to Xiaohongshu. Please scan QR code first.")

                logger.info("[DEBUG] Uploading Xiaohongshu video file...")
                file_input = page.locator('input[type="file"]').first
                await file_input.set_input_files(video_path, timeout=30000)

                title_input = page.locator('input.title-input, input[placeholder*="标题"]')
                await title_input.first.wait_for(state="visible", timeout=180000)
                await self._wait_until_upload_ready(page, "Xiaohongshu")
                await title_input.first.fill(title)

                await self._fill_text_entry(page, [
                    'textarea[placeholder*="正文"]', 'textarea[placeholder*="描述"]',
                    '[contenteditable="true"][data-placeholder*="正文"]', '[contenteditable="true"][placeholder*="正文"]',
                    '.ql-editor', '#post-textarea', '.post-content',
                ], description, timeout_ms=8000)

                await self._enable_xhs_schedule(page, scheduled_for)

                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1500)
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"[DEBUG] Xiaohongshu debug screenshot saved to {screenshot_path}")
                return screenshot_path
            except Exception as e:
                logger.error(f"[DEBUG] Xiaohongshu debug upload failed: {e}")
                await page.screenshot(path=screenshot_path)
                raise e
            finally:
                await page.close()
                await context.close()
                await browser.close()


class WechatChannelsPublisher(BaseSocialPlatform):
    def __init__(self):
        super().__init__("channels")
        self.qr_selectors = [
            "img.qrcode",
            ".qr-container img",
            ".qrcode-container img",
            "#login_container img",
            "img[src*='qrcode']",
            "img[src^='data:image']",
            # NOTE: removed bare "canvas" — it matches any canvas element (decorative, animations)
            # and causes false-positive QR detection on pages that are actually logged in.
        ]

    async def is_authenticated(self) -> bool:
        if not await super().is_authenticated():
            return False
        
        max_validation_attempts = 3
        
        for attempt in range(max_validation_attempts):
            try:
                async with async_playwright() as p:
                    browser, context = await self._launch_publish_browser(p)
                    page = await context.new_page()
                    
                    # Navigate to post/list - this is the default landing after QR login
                    await page.goto("https://channels.weixin.qq.com/platform/post/list", 
                                timeout=60000, wait_until="networkidle")
                    
                    # Give time for dashboard to render fully
                    await page.wait_for_timeout(12000)
                    
                    # Quick redirect check
                    if "login" in page.url.lower() or "qrcode" in page.url.lower():
                        logger.info(f"WeChat auth failed (redirected to login) - attempt {attempt+1}")
                        if attempt < max_validation_attempts - 1:
                            try:
                                await page.reload(wait_until="networkidle", timeout=30000)
                                await page.wait_for_timeout(8000)
                                continue
                            except:
                                pass
                        self.clear_auth()
                        return False
                    
                    # Get comprehensive page content
                    body_text = await page.evaluate("() => document.body?.innerText || ''")
                    
                    # Markers specific to the list/management page (post/list vs create)
                    required_markers = ["视频列表", "草稿箱", "数据分析", "发布记录", "视频号助手"]
                    has_marker = any(marker in body_text for marker in required_markers)
                    
                    # Also check for presence of navigation elements indicating logged-in state
                    if not has_marker:
                        try:
                            nav_elements = await page.query_selector_all("[class*='nav'], [id*='header'], [data-ml-root='true']")
                            if len(await nav_elements) > 0:
                                has_marker = True
                        except:
                            pass
                    
                    if not has_marker and attempt < max_validation_attempts - 1:
                        logger.info(f"WeChat auth partial on post/list - retrying ({attempt+1}/{max_validation_attempts})")
                        await page.wait_for_timeout(8000)
                        body_text_retry = await page.evaluate("() => document.body?.innerText || ''")
                        retry_has_marker = any(marker in body_text_retry for marker in required_markers)
                        if not retry_has_marker:
                            try:
                                nav_elements_retry = await page.query_selector_all("[class*='nav'], [id*='header']")
                                if len(await nav_elements_retry) > 0:
                                    retry_has_marker = True
                            except:
                                pass
                        if retry_has_marker:
                            has_marker = True
                    
                    if has_marker:
                        logger.info(f"WeChat Channels auth validated successfully on attempt {attempt+1} (using /post/list)")
                        return True
                        
                    logger.info(f"WeChat Channels auth failed validation on attempt {attempt+1} - no markers found on /post/list")
                    
            except Exception as e:
                logger.error(f"WeChat auth exception attempt {attempt+1}: {e}")
            
            if attempt == max_validation_attempts - 1:
                self.clear_auth()
        
        return False

    async def _is_logged_in_page(self, page: Page) -> bool:
        # 1. URL-based check — definitive signal of being logged out
        if "login" in page.url or "qrcode" in page.url:
            return False

        # 2. Wait for page to settle before checking markers
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            await page.wait_for_timeout(3000)

        # 3. Check for visible QR/login panel (using platform-specific selectors only, no bare canvas)
        if await self._has_visible_qr(page, self.qr_selectors):
            return False

        # 4. Look for known authenticated-page content markers
        for selector in [
            'text="发表动态"',
            'text="发布视频"',
            'text="作品管理"',
            'button:has-text("发表")',
            'button:has-text("发布")',
            'text="视频号助手"',
            'text="一站式服务"',
        ]:
            try:
                marker = page.locator(selector).first
                if await marker.count() > 0 and await marker.is_visible():
                    return True
            except Exception:
                continue

        # 5. Negative check: if the body text contains login-related keywords, treat as not logged in
        try:
            body_text = await page.locator("body").inner_text(timeout=3000)
            login_keywords = ["扫码登录", "登录视频号", "请输入验证码", "手机号登录"]
            if any(kw in body_text for kw in login_keywords):
                return False
        except Exception:
            pass

        # 6. If we reached here without finding login signals, assume logged in
        #    (the page may have loaded with cookies but markers render differently)
        logger.info(f"WeChat Channels _is_logged_in_page inconclusive — assuming authenticated (URL={page.url})")
        return True

    async def verify_logged_in(self, page: Page) -> bool:
        await page.wait_for_timeout(3000)
        return await self._is_logged_in_page(page)

    async def start_login_session(self) -> str:
        async def _wait_for_login(browser: Browser, context: BrowserContext, page: Page):
            try:
                for _ in range(120):
                    await asyncio.sleep(1)
                    if await self._is_logged_in_page(page):
                        logger.info(f"Channels login detected at URL: {page.url}")
                        await page.wait_for_timeout(5000)
                        if not await self._is_logged_in_page(page):
                            logger.warning("Channels login page became unauthenticated before storage state save; continuing to wait.")
                            continue
                        await context.storage_state(path=self.auth_file)
                        ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "success"
                        return
                raise Exception("Channels login timed out after 120s")
            except Exception as e:
                logger.error(f"Channels login timeout/error: {e}")
                ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "failed"
            finally:
                await browser.close()
                await asyncio.sleep(300)
                if self.platform_name in ACTIVE_LOGIN_SESSIONS:
                    del ACTIVE_LOGIN_SESSIONS[self.platform_name]

        if self.platform_name in ACTIVE_LOGIN_SESSIONS:
            del ACTIVE_LOGIN_SESSIONS[self.platform_name]
        
        ACTIVE_LOGIN_SESSIONS[self.platform_name] = {"status": "starting", "qr_base64": None}
        p = await async_playwright().start()
        browser, context = await self._init_browser(p, headless=True)
        page = await context.new_page()
        try:
            await page.goto("https://channels.weixin.qq.com/platform/post/list", timeout=60000, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                logger.info("Channels login page did not reach networkidle before QR capture; continuing with QR wait.")
            
            if await self._is_logged_in_page(page):
                logger.info("Wechat Channels already logged in on session start!")
                await page.wait_for_timeout(5000)
                if await self._is_logged_in_page(page):
                    await context.storage_state(path=self.auth_file)
                    ACTIVE_LOGIN_SESSIONS[self.platform_name] = {"status": "success", "qr_base64": None}
                    await browser.close()
                    return "ALREADY_LOGGED_IN"
                logger.warning("Wechat Channels already-login check did not remain authenticated; showing QR instead.")
                
            try:
                qr_b64 = await self._capture_loaded_qr(page, self.qr_selectors, timeout_ms=45000)
            except Exception as e:
                logger.warning(f"QR locator failed, taking fallback screenshot: {e}")
                # Try to screenshot just the login container to avoid huge page captures
                try:
                    login_box = page.locator(".login-box, .login-panel, .login-container, #login_container, .qrcode-container").first
                    await login_box.wait_for(state="visible", timeout=3000)
                    qr_bytes = await login_box.screenshot()
                except:
                    # If all else fails, just take a standard viewport screenshot
                    qr_bytes = await page.screenshot(full_page=False)
                    
                qr_b64 = f"data:image/png;base64,{base64.b64encode(qr_bytes).decode()}"

            ACTIVE_LOGIN_SESSIONS[self.platform_name]["qr_base64"] = qr_b64
            ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "pending"
            
            asyncio.create_task(_wait_for_login(browser, context, page))
            return ACTIVE_LOGIN_SESSIONS[self.platform_name]["qr_base64"]
        except Exception as e:
            await browser.close()
            ACTIVE_LOGIN_SESSIONS[self.platform_name]["status"] = "failed"
            raise e

    async def publish(self, video_path: str, title: str, description: str, scheduled_for: Optional[datetime] = None):
        """Publish video to WeChat Channels."""
        async with async_playwright() as p:
            browser, context = await self._launch_publish_browser(p)
            page = await context.new_page()
            try:
                await page.goto("https://channels.weixin.qq.com/platform/post/create", timeout=60000, wait_until="domcontentloaded")
                # Pre-flight: if on login page but cookies exist, refresh once
                if "login" in page.url.lower() and os.path.exists(self.auth_file):
                    logger.info("WeChat Channels on login page after goto — refreshing with cookies...")
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                if not await self.verify_logged_in(page):
                    self.clear_auth()
                    raise Exception("Not logged in to WeChat Channels. Please scan QR code first.")
                
                logger.info("Uploading WeChat Channels video file...")
                file_input = page.locator('input[type="file"]').first
                if await file_input.count() > 0:
                    await file_input.set_input_files(video_path, timeout=30000)
                else:
                    async with page.expect_file_chooser() as fc_info:
                        clicked_upload = await self._click_first_visible(page, [
                            '.upload',
                            '.btn-upload',
                            '[class*="upload"]',
                            'text="上传"',
                        ], timeout_ms=15000)
                        if not clicked_upload:
                            raise Exception("Could not find WeChat Channels upload control.")
                    file_chooser = await fc_info.value
                    await file_chooser.set_files(video_path)
                
                desc_input = page.locator('.input-editor, .desc-area, [contenteditable="true"]')
                await desc_input.first.wait_for(state="visible", timeout=180000)
                await desc_input.first.fill(f"{title}\n{description}")

                await self._enable_platform_schedule(page, scheduled_for)
                
                publish_btn = page.locator('button:has-text("发表"), button:has-text("发布")').first
                await publish_btn.wait_for(state="visible", timeout=90000)
                await publish_btn.click(timeout=90000)
                logger.info("WeChat Channels publish clicked!")
                await self._wait_for_publish_result(
                    page, 
                    "WeChat Channels", 
                    "channels_after_publish.png", 
                    "https://channels.weixin.qq.com/platform/post/create", 
                    timeout_ms=45000
                )
            except Exception as e:
                await page.screenshot(path="channels_error.png")
                raise e
            finally:
                await page.close()
                await context.close()
                await browser.close()

    async def debug_upload(self, video_path: str, title: str, description: str, scheduled_for: Optional[datetime] = None) -> str:
        """Upload + fill all fields + set scheduler, but do NOT click publish. Returns screenshot path."""
        screenshot_path = "channels_debug_upload.png"
        async with async_playwright() as p:
            browser, context = await self._launch_publish_browser(p)
            page = await context.new_page()
            try:
                logger.info("[DEBUG] Opening WeChat Channels publisher...")
                await page.goto("https://channels.weixin.qq.com/platform/post/create", timeout=60000, wait_until="domcontentloaded")
                # Pre-flight: if on login page but cookies exist, refresh once
                if "login" in page.url.lower() and os.path.exists(self.auth_file):
                    logger.info("WeChat Channels debug: on login page after goto — refreshing with cookies...")
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                if not await self.verify_logged_in(page):
                    self.clear_auth()
                    raise Exception("Not logged in to WeChat Channels. Please scan QR code first.")

                logger.info("[DEBUG] Uploading WeChat Channels video file...")
                file_input = page.locator('input[type="file"]').first
                if await file_input.count() > 0:
                    await file_input.set_input_files(video_path, timeout=30000)
                else:
                    async with page.expect_file_chooser() as fc_info:
                        clicked_upload = await self._click_first_visible(page, [
                            '.upload', '.btn-upload', '[class*="upload"]', 'text="上传"',
                        ], timeout_ms=15000)
                        if not clicked_upload:
                            raise Exception("Could not find WeChat Channels upload control.")
                    file_chooser = await fc_info.value
                    await file_chooser.set_files(video_path)

                desc_input = page.locator('.input-editor, .desc-area, [contenteditable="true"]')
                await desc_input.first.wait_for(state="visible", timeout=180000)
                await desc_input.first.fill(f"{title}\n{description}")

                await self._enable_platform_schedule(page, scheduled_for)

                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1500)
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"[DEBUG] WeChat Channels debug screenshot saved to {screenshot_path}")
                return screenshot_path
            except Exception as e:
                logger.error(f"[DEBUG] WeChat Channels debug upload failed: {e}")
                await page.screenshot(path=screenshot_path)
                raise e
            finally:
                await page.close()
                await context.close()
                await browser.close()
