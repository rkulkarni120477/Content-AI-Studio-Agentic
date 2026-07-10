"""Recursively upload every supported file from a local folder into DIS API.

This script preserves the full relative folder structure. Example:
  main_folder/a/b/file.pdf
is uploaded with source_relative_path="main_folder/a/b/file.pdf" and stored under:
  s3://<bucket>/<base_prefix>/<namespace>/<env>/raw/<job_id>/main_folder/a/b/file.pdf

Usage:
  python scripts/ingest_folder.py --folder ./main_folder --base-url http://localhost:8000 \
    --tenant-id aim --client-id aim_academics --user-id you@aim.edu --secret demo_secret
"""
from __future__ import annotations
import argparse
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Iterable

import httpx

SUPPORTED = {".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".json", ".jpg", ".jpeg", ".png"}


def safe_posix_relative_path(path: Path, root_parent: Path) -> str:
    """Build a storage-safe relative path including the selected root folder name."""
    rel = path.relative_to(root_parent).as_posix()
    parts = []
    for part in PurePosixPath(rel).parts:
        if part in ("", ".", ".."):
            continue
        clean = "".join(ch if ch.isalnum() or ch in "._- ()" else "_" for ch in part).strip()
        if clean:
            parts.append(clean)
    return "/".join(parts)


def iter_files(folder: Path, recursive: bool = True) -> Iterable[Path]:
    pattern = "**/*" if recursive else "*"
    for path in folder.glob(pattern):
        if path.is_file() and path.suffix.lower() in SUPPORTED:
            yield path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True, help="Root folder to scan. All nested folders are scanned by default.")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--client-id", default="aim")
    ap.add_argument("--user-id", default="folder.watcher@aim.edu")
    ap.add_argument("--secret", default="demo_secret")
    ap.add_argument("--no-recursive", action="store_true", help="Only upload files directly inside --folder. Default scans all nested folders.")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    folder = Path(args.folder).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        raise SystemExit(f"Folder not found: {folder}")

    token_resp = httpx.post(
        f"{base}/v1/auth/token",
        json={
            "client_id": args.client_id,
            "user_id": args.user_id,
            "secret": args.secret,
        },
        timeout=30,
    )
    token_resp.raise_for_status()
    token = token_resp.json()["access_token"]

    recursive = not args.no_recursive
    all_files = list(iter_files(folder, recursive=recursive))
    print(f"Scanning folder: {folder}")
    print(f"Recursive: {recursive}")
    print(f"Supported files found: {len(all_files)}")

    accepted = 0
    failed = 0
    root_parent = folder.parent
    for path in all_files:
        rel_path = safe_posix_relative_path(path, root_parent)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as fh:
            resp = httpx.post(
                f"{base}/v1/ingest/upload",
                params={"client_id": args.client_id},
                headers={"Authorization": f"Bearer {token}"},
                data={"source_relative_path": rel_path, "source_root": folder.name},
                files={"file": (path.name, fh, ctype)},
                timeout=180,
            )
        if resp.status_code >= 400:
            failed += 1
            print(f"FAILED   {rel_path}: {resp.status_code} {resp.text}")
        else:
            accepted += 1
            body = resp.json()
            print(f"ACCEPTED {rel_path}: job_id={body.get('job_id')} key={body.get('s3_key')}")

    print(f"Done. Accepted={accepted}, Failed={failed}, Total={len(all_files)}")


if __name__ == "__main__":
    main()
