"""
GitHub Release Uploader — uploads .ipa files to GitHub releases.

Automatically creates a release and uploads the .ipa as an asset,
so the user can download it from anywhere.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path


GITHUB_API = "https://api.github.com"
GITHUB_UPLOADS = "https://uploads.github.com"
CHUNK_SIZE = 8 * 1024 * 1024  # 8MB read chunks for upload progress


def upload_to_release(
    ipa_path: Path,
    repo: str,
    token: str | None = None,
    tag: str | None = None,
    release_name: str | None = None,
) -> str:
    """
    Upload an .ipa file to a GitHub release.

    Args:
        ipa_path: Path to the .ipa file
        repo: GitHub repo in "owner/repo" format
        token: GitHub personal access token (or set GITHUB_TOKEN env var)
        tag: Git tag for the release (default: auto-generated)
        release_name: Release title (default: based on .ipa name)

    Returns:
        Download URL for the uploaded asset.
    """
    ipa_path = Path(ipa_path)
    if not ipa_path.exists():
        raise FileNotFoundError(f"IPA not found: {ipa_path}")

    token = token or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError(
            "GitHub token required. Either pass --token or set GITHUB_TOKEN env var.\n"
            "Create one at: https://github.com/settings/tokens\n"
            "Required scope: 'repo' (for private repos) or 'public_repo'"
        )

    owner, repo_name = repo.split("/")
    ipa_name = ipa_path.name
    file_size = ipa_path.stat().st_size
    size_mb = file_size / (1024 * 1024)

    if not tag:
        tag = f"ipa-{ipa_path.stem.lower()}-{int(time.time())}"
    if not release_name:
        release_name = f"{ipa_path.stem} (Converted IPA)"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Step 1: Create the release
    print(f"  Creating release '{release_name}'...")
    release_data = json.dumps({
        "tag_name": tag,
        "name": release_name,
        "body": (
            f"Auto-uploaded by apk2ipa converter.\n\n"
            f"- **File:** {ipa_name}\n"
            f"- **Size:** {size_mb:.1f} MB\n"
            f"- **Generated:** {time.strftime('%Y-%m-%d %H:%M UTC')}\n"
        ),
        "draft": False,
        "prerelease": False,
    }).encode()

    release_url = f"{GITHUB_API}/repos/{owner}/{repo_name}/releases"
    req = urllib.request.Request(
        release_url, data=release_data, method="POST", headers={
            **headers, "Content-Type": "application/json"
        }
    )

    try:
        resp = urllib.request.urlopen(req, timeout=30)
        release_info = json.loads(resp.read().decode())
        release_id = release_info["id"]
        print(f"  Release created: {release_info['html_url']}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Failed to create release: {e.code} {body}") from e

    # Step 2: Upload the .ipa as a release asset
    upload_url = (
        f"{GITHUB_UPLOADS}/repos/{owner}/{repo_name}"
        f"/releases/{release_id}/assets?name={ipa_name}"
    )

    print(f"  Uploading {ipa_name} ({size_mb:.0f} MB)...")

    # Read file into memory in chunks and upload
    # For files > 1GB, we stream it
    with open(ipa_path, "rb") as f:
        file_data = _StreamingFileWrapper(f, file_size)
        req = urllib.request.Request(
            upload_url, data=file_data, method="POST", headers={
                **headers,
                "Content-Type": "application/octet-stream",
                "Content-Length": str(file_size),
            }
        )

        try:
            resp = urllib.request.urlopen(req, timeout=3600)  # 1 hour timeout
            asset_info = json.loads(resp.read().decode())
            download_url = asset_info["browser_download_url"]
            print(f"\n  Upload complete!")
            print(f"  Download: {download_url}")
            return download_url
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            # Try to clean up the empty release
            _delete_release(release_url, release_id, headers)
            raise RuntimeError(f"Failed to upload asset: {e.code} {body}") from e


class _StreamingFileWrapper:
    """Wraps a file object to show upload progress."""

    def __init__(self, f, total_size: int):
        self._f = f
        self._total = total_size
        self._sent = 0
        self._last_print = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._f.read(size if size > 0 else CHUNK_SIZE)
        self._sent += len(chunk)
        pct = (self._sent / self._total) * 100
        # Print progress every 5%
        if pct - self._last_print >= 5:
            mb_sent = self._sent / (1024 * 1024)
            mb_total = self._total / (1024 * 1024)
            print(f"    {mb_sent:.0f}/{mb_total:.0f} MB ({pct:.0f}%)", end="\r")
            sys.stdout.flush()
            self._last_print = pct
        return chunk

    def __len__(self):
        return self._total


def _delete_release(base_url: str, release_id: int, headers: dict) -> None:
    """Best-effort cleanup of a failed release."""
    try:
        req = urllib.request.Request(
            f"{base_url}/{release_id}", method="DELETE", headers=headers
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
