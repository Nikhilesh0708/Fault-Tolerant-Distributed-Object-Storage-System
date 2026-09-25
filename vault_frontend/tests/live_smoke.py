"""Optional integration check against a supplied Member 3 package, not a deployed service."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import requests
from streamlit.testing.v1 import AppTest
from tests.mock_storage import running_server


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(client, process, url):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            response = client.get(url, timeout=2)
            if response.status_code == 200:
                return response
        except requests.RequestException:
            pass
        if process.poll() is not None:
            raise RuntimeError("Service exited during startup")
        time.sleep(0.1)
    raise RuntimeError("Service startup deadline exceeded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery-root", required=True)
    parser.add_argument("--browser", action="store_true", help="Optional: requires Playwright and Chromium")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    recovery_root = Path(args.recovery_root).resolve()
    if not (recovery_root / "recovery" / "api.py").is_file():
        parser.error("Provide the vault_member3 folder containing recovery/api.py")
    recovery_port, frontend_port = free_port(), free_port()
    environment = os.environ.copy()
    environment.update(VAULT_MODE="demo", VAULT_ADMIN_TOKEN="", VAULT_DATABASE_PATH=":memory:")
    processes = []
    client = requests.Session()
    client.trust_env = False
    try:
        recovery = subprocess.Popen([sys.executable, "-m", "uvicorn", "recovery.api:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(recovery_port), "--log-level", "error"],
            cwd=recovery_root, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(recovery)
        recovery_url = f"http://127.0.0.1:{recovery_port}"
        health = wait_ready(client, recovery, recovery_url + "/healthz")
        client.post(recovery_url + "/health/check", timeout=10).raise_for_status()
        client.post(recovery_url + "/integrity/scan", timeout=10).raise_for_status()
        with running_server() as storage_url:
            os.environ.update(VAULT_RECOVERY_URL=recovery_url, VAULT_STORAGE_URL=storage_url,
                              VAULT_PUBLIC_STORAGE_URL=storage_url, VAULT_STORAGE_TOKEN="", VAULT_RECOVERY_TOKEN="")
            app = AppTest.from_file(str(root / "frontend" / "app.py"), default_timeout=30).run()
            assert not app.exception, [e.message for e in app.exception]
            metric = {m.label: m.value for m in app.metric}
            assert metric["Nodes online"] == "4 / 4", metric
            assert metric["Stored objects"] == "1", metric
            assert not app.warning
            frontend = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "frontend/app.py",
                "--server.headless", "true", "--server.port", str(frontend_port)],
                cwd=root, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(frontend)
            ready = wait_ready(client, frontend, f"http://127.0.0.1:{frontend_port}/_stcore/health")
            assert "ok" in ready.text.lower()
            browser_checks = False
            if args.browser:
                from playwright.sync_api import sync_playwright, expect
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    page = browser.new_page(viewport={"width": 1440, "height": 1380}, device_scale_factor=1)
                    page.goto(f"http://127.0.0.1:{frontend_port}")
                    expect(page.get_by_role("heading", name="Storage, under control.")).to_be_visible(timeout=30000)
                    artifacts = root / "artifacts"
                    artifacts.mkdir(exist_ok=True)
                    page.screenshot(path=str(artifacts / "Live_Check.png"), full_page=True)
                    page.get_by_text("Design preview", exact=True).click()
                    expect(page.get_by_text("Design preview · sample data only.", exact=False)).to_be_visible()
                    expect(page.get_by_text("128", exact=True).first).to_be_visible(timeout=10000)
                    page.screenshot(path=str(artifacts / "Vault_Dashboard_Preview.png"), full_page=True)
                    page.get_by_text("Live services", exact=True).click()
                    page.get_by_text("Files", exact=True).click()
                    expect(page.get_by_role("heading", name="Your files", exact=True)).to_be_visible()
                    payload = b"Verified through the real Streamlit file picker.\n"
                    page.locator('input[type="file"]').set_input_files({"name": "browser-check.txt", "mimeType": "text/plain", "buffer": payload})
                    page.get_by_role("button", name="Upload to Vault", exact=True).click()
                    expect(page.get_by_text("Uploaded browser-check.txt.", exact=True)).to_be_visible(timeout=15000)
                    page.get_by_role("textbox", name="Search files on this page").fill("browser-check")
                    page.get_by_role("textbox", name="Search files on this page").press("Enter")
                    expect(page.get_by_text("1 matching objects on this page", exact=True)).to_be_visible()
                    with page.expect_download() as downloaded:
                        page.get_by_role("link", name="Download file", exact=True).click()
                    download = downloaded.value
                    assert Path(download.path()).read_bytes() == payload
                    page.get_by_role("button", name="Delete selected object", exact=True).click()
                    expect(page.get_by_text("Select the confirmation checkbox first. Nothing was deleted.")).to_be_visible()
                    page.get_by_text("I confirm deletion of this object", exact=True).click()
                    page.get_by_role("button", name="Delete selected object", exact=True).click()
                    expect(page.get_by_text("Deleted browser-check.txt.", exact=True)).to_be_visible()
                    page.get_by_text("Overview", exact=True).click()
                    page.get_by_text("Design preview", exact=True).click()
                    expect(page.get_by_role("heading", name="Storage, under control.")).to_be_visible()
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.screenshot(path=str(artifacts / "Vault_Mobile_Preview.png"), full_page=True)
                    browser.close()
                    browser_checks = True
            print(json.dumps({"recovery_health": health.status_code, "streamlit_health": ready.status_code,
                              "dashboard_metrics": metric, "app_exceptions": 0,
                              "browser_upload_download_confirmed_delete": browser_checks,
                              "note": "Real local HTTP connections; recovery nodes and storage fixture are simulations."}, indent=2))
    finally:
        client.close()
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
