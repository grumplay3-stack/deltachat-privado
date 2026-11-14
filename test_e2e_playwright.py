import multiprocessing
import os
import time
from playwright.sync_api import sync_playwright
import uvicorn
import main

def run_server():
    uvicorn.run(main.app, host="127.0.0.1", port=8001, log_level="warning")

def test_deltachat_playwright():
    proc = multiprocessing.Process(target=run_server, daemon=True)
    proc.start()
    time.sleep(2)  # wait for server startup

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # 1. Open DeltaChat
            page.goto("http://127.0.0.1:8001")
            page.wait_for_selector("#login-username")
            assert "DeltaChat" in page.title()

            # 2. Login as dabo777
            page.fill("#login-username", "dabo777")
            page.fill("#login-password", "52952416")
            page.click("button[type='submit']")

            # 3. Wait for chat screen
            page.wait_for_selector("#chat-screen:not(.hidden)", timeout=6000)
            page.wait_for_selector("#message-input")

            # 4. Post greentext message
            page.fill("#message-input", ">e2e test verified with Playwright")
            page.click("#btn-send")

            # 5. Verify message arrived in DOM
            page.wait_for_selector(".post-content .greentext", timeout=6000)
            greentext_el = page.locator(".post-content .greentext").last
            assert "e2e test verified with Playwright" in greentext_el.inner_text()

            # 6. Capture screenshot evidence
            os.makedirs("test_artifacts", exist_ok=True)
            screenshot_path = os.path.abspath("test_artifacts/playwright_e2e_evidence.png")
            page.screenshot(path=screenshot_path)
            print(f"[PLAYWRIGHT SUCCESS] Screenshot captured at: {screenshot_path}")

            browser.close()
    finally:
        proc.terminate()
        proc.join(timeout=2)

if __name__ == "__main__":
    test_deltachat_playwright()
