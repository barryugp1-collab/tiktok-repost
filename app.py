import asyncio
import threading
import queue
import subprocess
import sys
from flask import Flask, render_template, request, Response, stream_with_context
from playwright.async_api import async_playwright

app = Flask(__name__)

def install_chromium():
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True
    )
    subprocess.run(
        [sys.executable, "-m", "playwright", "install-deps", "chromium"],
        check=True
    )

install_chromium()

async def remove_reposts(username, password, q):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = await browser.new_page()

        try:
            q.put("Opening TikTok login...")
            await page.goto("https://www.tiktok.com/login/phone-or-email/email")
            await page.wait_for_timeout(3000)

            q.put("Entering credentials...")
            await page.fill('input[name="username"]', username)
            await page.wait_for_timeout(500)
            await page.fill('input[type="password"]', password)
            await page.wait_for_timeout(500)
            await page.click('button[type="submit"]')

            q.put("Waiting for login...")
            await page.wait_for_timeout(7000)

            if "login" in page.url:
                q.put("ERROR: Login failed. Wrong credentials or CAPTCHA blocked it.")
                await browser.close()
                return

            q.put("Logged in. Loading profile...")
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="networkidle")
            await page.wait_for_timeout(3000)

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

def run_thread(username, password, q):
    asyncio.run(remove_reposts(username, password, q))

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/run", methods=["POST"])
def run():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    q = queue.Queue()
    threading.Thread(target=run_thread, args=(username, password, q)).start()

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
