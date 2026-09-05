import asyncio
import threading
import queue
import json
from flask import Flask, render_template, request, Response, stream_with_context
from playwright.async_api import async_playwright

app = Flask(__name__)

async def remove_reposts_with_cookies(username, cookies, q):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        )

        try:
            parsed = json.loads(cookies)
            await context.add_cookies(parsed)
            q.put("Cookies loaded. Opening TikTok...")
        except Exception:
            q.put("ERROR: Invalid cookie format.")
            await browser.close()
            return

        page = await context.new_page()

        try:
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="networkidle")
            await page.wait_for_timeout(3000)

            if "login" in page.url:
                q.put("ERROR: Cookies expired or invalid. Please re-export them.")
                await browser.close()
                return

            q.put("Logged in successfully. Looking for reposts tab...")

            repost_tab = await page.query_selector('[data-e2e="repost-tab"]')
            if not repost_tab:
                q.put("No reposts tab found. You might have no reposts.")
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
                    q.put(f"Skipped a video (no repost button found)")

                await page.go_back()
                await page.wait_for_timeout(2000)

        except Exception as e:
            q.put(f"ERROR: {str(e)}")
        finally:
            await browser.close()

def run_thread(username, cookies, q):
    asyncio.run(remove_reposts_with_cookies(username, cookies, q))

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/run", methods=["POST"])
def run():
    username = request.form.get("username", "").replace("@", "").strip()
    cookies = request.form.get("cookies", "").strip()

    q = queue.Queue()
    thread = threading.Thread(target=run_thread, args=(username, cookies, q))
    thread.start()

    def generate():
        while True:
            try:
                msg = q.get(timeout=120)
                yield f"data: {msg}\n\n"
                if msg.startswith("DONE") or msg.startswith("ERROR"):
                    break
            except Exception:
                yield "data: ERROR: Timed out.\n\n"
                break

    return Response(stream_with_context(generate()), mimetype="text/event-stream")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
