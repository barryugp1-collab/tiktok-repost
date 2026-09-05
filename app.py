import asyncio
import threading
from flask import Flask, render_template, request, Response, stream_with_context
from playwright.async_api import async_playwright

app = Flask(__name__)

async def remove_reposts(username, password, queue):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = await browser.new_page()

        try:
            queue.put("Navigating to TikTok login...")
            await page.goto("https://www.tiktok.com/login/phone-or-email/email")
            await page.wait_for_timeout(2000)

            await page.fill('input[name="username"]', username)
            await page.fill('input[type="password"]', password)
            await page.click('button[type="submit"]')

            queue.put("Logging in, waiting...")
            await page.wait_for_timeout(6000)

            current_url = page.url
            if "login" in current_url:
                queue.put("ERROR: Login failed. Check your credentials or solve CAPTCHA manually.")
                await browser.close()
                return

            queue.put("Logged in. Loading your profile...")
            await page.goto(f"https://www.tiktok.com/@{username}")
            await page.wait_for_timeout(3000)

            repost_tab = await page.query_selector('[data-e2e="repost-tab"]')
            if not repost_tab:
                queue.put("No reposts tab found. You may have no reposts.")
                await browser.close()
                return

            await repost_tab.click()
            await page.wait_for_timeout(2000)
            queue.put("Found reposts tab. Starting removal...")

            removed = 0
            while True:
                video = await page.query_selector('[data-e2e="repost-item"]')
                if not video:
                    queue.put(f"DONE: Removed {removed} reposts.")
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
                    queue.put(f"Removed repost #{removed}")

                await page.go_back()
                await page.wait_for_timeout(2000)

        except Exception as e:
            queue.put(f"ERROR: {str(e)}")
        finally:
            await browser.close()

def run_async(username, password, queue):
    asyncio.run(remove_reposts(username, password, queue))

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/run", methods=["POST"])
def run():
    username = request.form.get("username")
    password = request.form.get("password")

    import queue
    q = queue.Queue()

    thread = threading.Thread(target=run_async, args=(username, password, q))
    thread.start()

    def generate():
        while True:
            try:
                msg = q.get(timeout=60)
                yield f"data: {msg}\n\n"
                if msg.startswith("DONE") or msg.startswith("ERROR"):
                    break
            except Exception:
                yield "data: ERROR: Timed out.\n\n"
                break

    return Response(stream_with_context(generate()), mimetype="text/event-stream")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
