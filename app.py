import asyncio
import threading
import queue
import subprocess
import sys
import base64
from flask import Flask, render_template, request, Response, stream_with_context
from playwright.async_api import async_playwright

app = Flask(__name__)

def install_chromium():
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    subprocess.run([sys.executable, "-m", "playwright", "install-deps", "chromium"], check=True)

install_chromium()

async def take_debug_screenshot(session_id, username):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        await context.add_cookies([{
            "name": "sessionid",
            "value": session_id,
            "domain": ".tiktok.com",
            "path": "/",
            "httpOnly": True,
            "secure": True
        }])
        page = await context.new_page()
        await page.goto(f"https://www.tiktok.com/@{username}", wait_until="networkidle")
        await page.wait_for_timeout(3000)

        repost_tab = await page.query_selector('[data-e2e="repost-tab"]')
        if repost_tab:
            await repost_tab.click()
            await page.wait_for_timeout(2000)

        video = (
            await page.query_selector('[data-e2e="repost-item"]') or
            await page.query_selector('[data-e2e="user-post-item"]') or
            await page.query_selector('div[class*="DivItemContainer"]') or
            await page.query_selector('a[href*="/video/"]')
        )

        if video:
            href = await video.get_attribute("href")
            if not href:
                link = await video.query_selector("a")
                href = await link.get_attribute("href") if link else None
            if href:
                if href.startswith("/"):
                    href = "https://www.tiktok.com" + href
                await page.goto(href, wait_until="networkidle")
                await page.wait_for_timeout(3000)

        screenshot = await page.screenshot(full_page=False)
        html = await page.content()
        await browser.close()
        return base64.b64encode(screenshot).decode(), html

async def remove_reposts(username, session_id, q):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )

        await context.add_cookies([{
            "name": "sessionid",
            "value": session_id,
            "domain": ".tiktok.com",
            "path": "/",
            "httpOnly": True,
            "secure": True
        }])

        page = await context.new_page()

        try:
            q.put("Opening TikTok...")
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="networkidle")
            await page.wait_for_timeout(4000)

            if "login" in page.url:
                q.put("ERROR: Session expired. Get a fresh sessionid.")
                await browser.close()
                return

            q.put("Logged in. Looking for reposts tab...")

            repost_tab = await page.query_selector('[data-e2e="repost-tab"]')
            if not repost_tab:
                q.put("ERROR: Could not find reposts tab.")
                await browser.close()
                return

            await repost_tab.click()
            await page.wait_for_timeout(3000)
            q.put("Found reposts tab. Scanning for videos...")

            removed = 0
            fails = 0

            while fails < 5:
                await page.wait_for_timeout(2000)

                video = (
                    await page.query_selector('[data-e2e="repost-item"]') or
                    await page.query_selector('[data-e2e="user-post-item"]') or
                    await page.query_selector('div[class*="DivItemContainer"]') or
                    await page.query_selector('a[href*="/video/"]')
                )

                if not video:
                    q.put(f"DONE: Removed {removed} reposts.")
                    break

                try:
                    href = await video.get_attribute("href")
                    if not href:
                        link = await video.query_selector("a")
                        href = await link.get_attribute("href") if link else None
                    if href:
                        if href.startswith("/"):
                            href = "https://www.tiktok.com" + href
                        await page.goto(href, wait_until="networkidle")
                        await page.wait_for_timeout(3000)
                    else:
                        await video.click(timeout=10000)
                        await page.wait_for_timeout(3000)
                except Exception as e:
                    fails += 1
                    q.put(f"Could not open video (attempt {fails}/5): {str(e)}")
                    continue

                you_reposted = (
                    await page.query_selector('text="You reposted"') or
                    await page.query_selector('[aria-label="You reposted"]') or
                    await page.query_selector('div[class*="reposted"]') or
                    await page.query_selector('span:has-text("You reposted")')
                )

                if you_reposted:
                    await you_reposted.click()
                    await page.wait_for_timeout(1500)

                    remove_btn = (
                        await page.query_selector('text="Remove"') or
                        await page.query_selector('button:has-text("Remove")') or
                        await page.query_selector('div[class*="MenuItem"]:has-text("Remove")')
                    )

                    if remove_btn:
                        await remove_btn.click()
                        await page.wait_for_timeout(1000)
                        removed += 1
                        fails = 0
                        q.put(f"Removed repost #{removed}")
                    else:
                        fails += 1
                        q.put(f"Could not find Remove button (attempt {fails}/5)")
                else:
                    fails += 1
                    q.put(f"Could not find 'You reposted' button (attempt {fails}/5)")

                await page.go_back()
                await page.wait_for_timeout(3000)

        except Exception as e:
            q.put(f"ERROR: {str(e)}")
        finally:
            await browser.close()

def run_thread(username, session_id, q):
    asyncio.run(remove_reposts(username, session_id, q))

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/debug")
def debug():
    session_id = request.args.get("session_id", "")
    username = request.args.get("username", "")

    result = {}

    def run_debug():
        result["data"] = asyncio.run(take_debug_screenshot(session_id, username))

    t = threading.Thread(target=run_debug)
    t.start()
    t.join(timeout=60)

    if "data" not in result:
        return "Timed out", 500

    screenshot_b64, html = result["data"]
    return f'''
    <h2>Screenshot</h2>
    <img src="data:image/png;base64,{screenshot_b64}" style="max-width:100%">
    <h2>Page HTML (first 5000 chars)</h2>
    <pre style="white-space:pre-wrap;word-break:break-all">{html[:5000]}</pre>
    '''

@app.route("/run", methods=["POST"])
def run():
    username = request.form.get("username", "").replace("@", "").strip()
    session_id = request.form.get("session_id", "").strip()

    q = queue.Queue()
    threading.Thread(target=run_thread, args=(username, session_id, q)).start()

    def generate():
        while True:
            try:
                msg = q.get(timeout=120)
                yield f"data: {msg}\n\n"
                if msg.startswith("DONE") or msg.startswith("ERROR"):
                    break
            except:
                yield "data: ERROR: Timed out.\n\n"
                break

    return Response(stream_with_context(generate()), mimetype="text/event-stream")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
