"""
DIS – Folder Watcher
====================
Watches a local folder (e.g. SharePoint OneDrive sync) for new/changed files.
Automatically uploads them to DIS when detected.

Usage:
    python deploy/folder_watcher.py \
        --folder "/Users/you/OneDrive - AIM/Course Content" \
        --dis-url "http://localhost:8000" \
        --tenant-id "aim" \
        --client-id "aim_academics" \
        --user-id "watcher@aim.edu" \
        --secret "demo_secret"

Run as a background service on any machine that has the folder mounted.
"""
import argparse
import logging
import os
import time

import httpx
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("watcher")

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".json", ".jpg", ".png"}


class DISUploader(FileSystemEventHandler):
    def __init__(self, dis_url: str, token: str, client_id: str):
        self.dis_url = dis_url.rstrip("/")
        self.token = token
        self.client_id = client_id
        self._recently_uploaded = set()   # avoid double-upload on save

    def on_created(self, event):
        if not event.is_directory:
            self._maybe_upload(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._maybe_upload(event.src_path)

    def _maybe_upload(self, path: str):
        ext = os.path.splitext(path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return
        # Debounce: skip if recently uploaded
        if path in self._recently_uploaded:
            return
        self._recently_uploaded.add(path)
        # Wait briefly for file to finish writing
        time.sleep(1)
        self._upload(path)
        # Clear from debounce after 30s
        import threading
        threading.Timer(30, lambda: self._recently_uploaded.discard(path)).start()

    def _upload(self, path: str):
        filename = os.path.basename(path)
        log.info("Uploading: %s", path)
        try:
            with open(path, "rb") as f:
                resp = httpx.post(
                    f"{self.dis_url}/v1/ingest/upload",
                    headers={"Authorization": f"Bearer {self.token}"},
                    files={"file": (filename, f)},
                    params={"client_id": self.client_id},
                    timeout=120,
                )
                resp.raise_for_status()
                data = resp.json()
                log.info("  job_id=%s status=%s", data.get("job_id"), data.get("status"))
        except Exception as exc:
            log.error("  Upload failed for %s: %s", filename, exc)


def get_token(dis_url: str, tenant_id: str, client_id: str, user_id: str, secret: str) -> str:
    resp = httpx.post(f"{dis_url}/v1/auth/token", json={
        "tenant_id": tenant_id, "client_id": client_id,
        "user_id": user_id, "secret": secret, "role": "user",
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def main():
    parser = argparse.ArgumentParser(description="DIS Folder Watcher")
    parser.add_argument("--folder", required=True, help="Folder to watch")
    parser.add_argument("--dis-url", default="http://localhost:8000")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--secret", default="demo_secret")
    args = parser.parse_args()

    log.info("Getting DIS token...")
    token = get_token(args.dis_url, args.tenant_id, args.client_id, args.user_id, args.secret)
    log.info("Token obtained.")

    uploader = DISUploader(args.dis_url, token, args.client_id)
    observer = Observer()
    observer.schedule(uploader, args.folder, recursive=True)
    observer.start()
    log.info("Watching folder: %s", args.folder)
    log.info("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(5)
            # Re-authenticate every 7 hours (token expires in 8h)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
