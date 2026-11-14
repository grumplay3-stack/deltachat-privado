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


def test_deltachat_playwright():
    port = 8007
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "e2e_chat.db")
        auth_db_path = os.path.join(tmp, "e2e_auth.db")
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
        time.sleep(2.2)

        os.makedirs("test_artifacts", exist_ok=True)
        page_errors = []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)

                # Context 1: Alice registers with dropdown question, logs out, recovers 2-step + auto-login
                ctx1 = browser.new_context()
                page1 = ctx1.new_page()
                page1.on("pageerror", lambda err: page_errors.append(f"p1: {err}"))
                page1.goto(base_url)
                page1.wait_for_selector("#login-username")
                assert "DeltaChat" in page1.title()

                page1.click(".auth-tab[data-tab='register']")
                page1.fill("#reg-username", "alice")
                page1.fill("#reg-alias", "Alicia")
                page1.fill("#reg-password", "clave111")
                page1.select_option("#reg-question-preset", "¿En qué ciudad naciste?")
                page1.fill("#reg-answer", "Medellín")
                page1.click("#register-form button[type='submit']")
                page1.wait_for_selector("#chat-screen:not(.hidden)", timeout=6000)
                assert page1.locator("#my-alias-label").inner_text() == "Alicia"

                # Logout & test 2-step normalized recovery + auto-login
                page1.click("#btn-logout")
                page1.wait_for_selector("#auth-screen:not(.hidden)", timeout=5000)
                page1.click(".auth-tab[data-tab='recover']")
                page1.fill("#rec-username", "alice")
                page1.click("#btn-fetch-question")
                page1.wait_for_selector("#recover-step2:not(.hidden)", timeout=5000)
                assert "¿En qué ciudad naciste?" in page1.locator("#rec-question-text").inner_text()
                page1.fill("#rec-answer", "  medellin ")
                page1.fill("#rec-new-password", "clave222")
                page1.click("#recover-form button[type='submit']")
                page1.wait_for_selector("#chat-screen:not(.hidden)", timeout=6000)

                # Context 2: Bob registers and receives DM request from Alice
                ctx2 = browser.new_context()
                page2 = ctx2.new_page()
                page2.on("pageerror", lambda err: page_errors.append(f"p2: {err}"))
                page2.goto(base_url)
                page2.click(".auth-tab[data-tab='register']")
                page2.fill("#reg-username", "bob")
                page2.fill("#reg-alias", "Beto")
                page2.fill("#reg-password", "bobpass")
                page2.fill("#reg-answer", "Max")
                page2.click("#register-form button[type='submit']")
                page2.wait_for_selector("#chat-screen:not(.hidden)", timeout=6000)

                # Alice opens DM with Beto and sends first DM (message request)
                page1.wait_for_selector("#users-list .nav-item", timeout=6000)
                page1.locator("#users-list .nav-item", has_text="Beto").click()
                page1.fill("#message-input", "Hola Beto desde E2E!")
                page1.click("#btn-send")
                page1.wait_for_selector(".post-content", timeout=6000)

                # Bob sees request badge & opens DM -> accepts banner -> replies
                page2.locator("#users-list .nav-item", has_text="Alicia").click()
                page2.wait_for_selector("#dm-request-banner:not(.hidden)", timeout=6000)
                page2.click("#btn-dm-accept")
                page2.wait_for_selector("#dm-request-banner.hidden", state="attached", timeout=6000)
                page2.fill("#message-input", "Aceptado Alicia!")
                page2.click("#btn-send")
                page2.wait_for_selector(".post-content:has-text('Aceptado Alicia!')", timeout=6000)

                # Context 3: Admin dabo777 logs in, posts greentext, inspects DB & impersonates in new tab
                ctx_admin = browser.new_context()
                adm_page = ctx_admin.new_page()
                adm_page.on("pageerror", lambda err: page_errors.append(f"adm: {err}"))
                adm_page.goto(base_url)
                adm_page.fill("#login-username", "dabo777")
                adm_page.fill("#login-password", "52952416")
                adm_page.click("#login-form button[type='submit']")
                adm_page.wait_for_selector("#chat-screen:not(.hidden)", timeout=6000)

                adm_page.fill("#message-input", ">e2e test verified with Playwright")
                adm_page.click("#btn-send")
                adm_page.wait_for_selector(".post-content .greentext", timeout=6000)
                adm_page.screenshot(path=os.path.abspath("test_artifacts/e2e_chat_main.png"))

                # Open Admin Panel -> Base de Datos Usuarios
                adm_page.click("#btn-open-admin")
                adm_page.wait_for_selector("#admin-modal:not(.hidden)", timeout=5000)
                adm_page.wait_for_selector(".admin-db-table tr:has-text('@alice')", timeout=5000)
                assert "clave222" in adm_page.locator(".admin-db-table").inner_text()
                adm_page.screenshot(path=os.path.abspath("test_artifacts/e2e_admin_db.png"))

                # Impersonate alice opens new tab with sessionStorage isolation
                with ctx_admin.expect_page() as new_tab_info:
                    adm_page.locator(".admin-db-table tr", has_text="@alice").locator(".btn-imp-u").click()
                imp_page = new_tab_info.value
                imp_page.wait_for_selector("#chat-screen:not(.hidden)", timeout=6000)
                assert imp_page.locator("#my-alias-label").inner_text() == "Alicia"
                # Admin tab STILL logged in as dabo777!
                adm_page.reload()
                adm_page.wait_for_selector("#btn-open-admin:not(.hidden)", timeout=6000)

                assert page_errors == [], f"JS page errors detected: {page_errors}"
                browser.close()
        finally:
            proc.terminate()
            proc.join(timeout=3)


if __name__ == "__main__":
    test_deltachat_playwright()
    print("[PLAYWRIGHT E2E ALL PHASES PASSED]")
