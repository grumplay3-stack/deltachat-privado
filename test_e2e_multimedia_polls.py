import multiprocessing
import os
import tempfile
import time
from playwright.sync_api import sync_playwright


def _run_server(db_path: str, auth_db_path: str, uploads_dir: str, backups_dir: str, port: int):
    os.environ["DELTACHAT_DB"] = db_path
    os.environ["DELTACHAT_AUTH_DB"] = auth_db_path
    os.environ["DELTACHAT_UPLOADS"] = uploads_dir
    os.environ["DELTACHAT_BACKUPS"] = backups_dir

    import uvicorn
    import main

    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning")


def test_multimedia_and_polls_e2e():
    port = 8011
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "multimedia_chat.db")
        auth_db_path = os.path.join(tmp, "multimedia_auth.db")
        uploads_dir = os.path.join(tmp, "uploads")
        backups_dir = os.path.join(tmp, "backups")
        os.makedirs(uploads_dir, exist_ok=True)
        os.makedirs(backups_dir, exist_ok=True)

        proc = multiprocessing.Process(
            target=_run_server,
            args=(db_path, auth_db_path, uploads_dir, backups_dir, port),
            daemon=True,
        )
        proc.start()
        time.sleep(2.5)

        page_errors = []
        screenshot_dir = r"C:\Users\DvD.YURIBIA\.gemini\antigravity\brain\21a54f02-85ab-4a84-b8d6-c839a750afd5"

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(viewport={"width": 1280, "height": 850})
                page = ctx.new_page()
                page.on("pageerror", lambda err: page_errors.append(f"page: {err}"))

                # 1. Login as seeded admin dabo777
                page.goto(base_url)
                page.wait_for_selector("#login-username")
                page.fill("#login-username", "dabo777")
                page.fill("#login-password", "52952416")
                page.click("#login-form button[type='submit']")
                page.wait_for_selector("#chat-screen:not(.hidden)", timeout=7000)

                # 2. Verify Radio bar is present
                page.wait_for_selector("#xat-radio-bar", timeout=5000)
                assert page.locator("#xat-radio-title").inner_text() == "ConectFm"
                assert page.is_visible("#btn-admin-radio")

                # 3. Admin opens radio modal and sets live stream
                page.click("#btn-admin-radio")
                page.wait_for_selector("#admin-radio-modal:not(.hidden)", timeout=4000)
                page.fill("#admin-radio-title-input", "Delta Retro FM")
                page.fill("#admin-radio-url-input", "https://stream.example.com/radio.mp3")
                page.check("#admin-radio-active-chk")
                page.click("#admin-radio-form button[type='submit']")
                page.wait_for_selector("#admin-radio-modal", state="hidden", timeout=4000)

                # Check radio title and live badge
                page.wait_for_function("document.getElementById('xat-radio-title').textContent === 'Delta Retro FM'")
                assert "LIVE" in page.locator("#xat-radio-badge").inner_text()

                # 4. Open poll modal and create poll
                page.wait_for_selector("#btn-open-poll-modal")
                page.click("#btn-open-poll-modal")
                page.wait_for_selector("#poll-modal:not(.hidden)", timeout=4000)

                page.fill("#poll-question-input", "¿Cuál es tu lenguaje favorito?")
                opt_inputs = page.locator(".poll-opt-input")
                opt_inputs.nth(0).fill("Python")
                opt_inputs.nth(1).fill("TypeScript")

                # Add 3rd option
                page.click("#btn-add-poll-opt")
                opt_inputs = page.locator(".poll-opt-input")
                assert opt_inputs.count() == 3
                opt_inputs.nth(2).fill("Rust")

                # Submit poll
                page.click("#poll-form button[type='submit']")
                page.wait_for_selector("#poll-modal", state="hidden", timeout=4000)

                # 5. Check poll card rendered in chat feed
                page.wait_for_selector(".xat-poll-card", timeout=6000)
                assert "¿Cuál es tu lenguaje favorito?" in page.locator(".xat-poll-title").inner_text()
                assert page.locator(".xat-poll-opt-btn").count() == 3

                # 6. Vote for Python
                page.locator(".xat-poll-opt-btn", has_text="Python").click()
                time.sleep(1.0)

                # Check Python has 1 vote and 100%
                python_btn = page.locator(".xat-poll-opt-btn", has_text="Python")
                assert "1 (100%)" in python_btn.inner_text()
                assert "✓" in python_btn.inner_text()

                # 7. Check voice recording and attach buttons exist
                assert page.is_visible("#btn-voice-record")
                assert page.is_visible("#btn-attach")
                assert page.is_visible("#btn-toggle-sound")

                # 8. Post a message with YouTube link and verify smart embed
                page.fill("#message-input", "Miren este temazo: https://www.youtube.com/watch?v=dQw4w9WgXcQ")
                page.click("#btn-send")
                page.wait_for_selector(".xat-embed-card", timeout=6000)
                assert page.is_visible(".xat-embed-thumb-wrap")
                assert "YouTube" in page.locator(".xat-embed-service").inner_text()

                # 9. Take full page visual screenshot
                screenshot_path = os.path.join(screenshot_dir, "xat_multimedia_polls_dark.png")
                page.screenshot(path=screenshot_path, full_page=True)

                assert len(page_errors) == 0, f"Page errors: {page_errors}"
                browser.close()

        finally:
            proc.terminate()
            proc.join(timeout=3)
