import asyncio
import threading
import queue
import subprocess
import sys
from flask import Flask, render_template, request, Response, stream_with_context
from playwright.async_api import async_playwright

app = Flask(__name__)

def install_chromium():
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    subprocess.run([sys.executable, "-m", "playwright", "install-deps", "chromium"], check=True)

install_chromium()

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
            await page.wait_for_timeout(3000)

            if "login" in page.url:
                q.put("ERROR: Session expired or invalid. Copy a fresh sessionid from DevTools.")
                await browser.close()
                return

            q.put("Logged in. Looking for reposts tab...")

            repost_tab = await page.query_selector('[data-e2e="repost-tab"]')
            if not repost_tab:
                q.put("DONE: No reposts tab found. You have no reposts.")
                await browser.close()
                return

            await repost_tab.click()
            await page.wait_for_timeout(2000)
            q.put("Found reposts. Starting removal...")

            removed = 0
            while True:
                video = await page.query_selector('[data-e2e="repost-item"]')
                if not video:
                    q.put(f"DONE: Removed {removed} reposts.")
                    break

                await video.click()
                await page.wait_for_timeout(2000)

                repost_btn = await page.query_selector('[data-e2e="repost-icon"]')
                if repost_btn:
                    await repost_btn.click()
                    await page.wait_for_timeout(1000)

                    confirm = await page.query_selector('[data-e2e="repost-confirm"]')
                    if confirm:
                        await confirm.click()
                        await page.wait_for_timeout(1000)

                    removed += 1
                    q.put(f"Removed repost #{removed}")
                else:
                    q.put("Skipped a video (repost button not found)")

                await page.go_back()
                await page.wait_for_timeout(2000)

        except Exception as e:
            q.put(f"ERROR: {str(e)}")
        finally:
            await browser.close()

def run_thread(username, session_id, q):
    asyncio.run(remove_reposts(username, session_id, q))

@app.route("/")
def index():
    return render_template("index.html")

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
