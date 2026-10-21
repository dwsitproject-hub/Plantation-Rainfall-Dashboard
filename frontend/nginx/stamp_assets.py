"""Stamp every local CSS/JS link in the site's HTML with a hash of the file's contents.

Runs while the frontend image is built. A changed file gets a new URL, so nginx can
let browsers keep CSS/JS for a long time (see default.conf.template) and pages need
far fewer requests, without anyone having to bump ?v= by hand.

Usage: python stamp_assets.py <site folder>
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

HASH_LENGTH = 12  # must match the ?v= pattern in default.conf.template
ASSET_LINK = re.compile(r'(?P<attr>\b(?:src|href)=")(?P<path>[^"?#:]+\.(?:css|js))(?:\?[^"#]*)?(?P<end>")')


def content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:HASH_LENGTH]


def stamp_page(page: Path, site: Path) -> int:
    html = page.read_text(encoding="utf-8")
    stamped = 0

    def replace(match: re.Match) -> str:
        nonlocal stamped
        link = match["path"]
        asset = (site / link.lstrip("/")) if link.startswith("/") else (page.parent / link)
        asset = asset.resolve()
        if not asset.is_file() or site not in asset.parents:
            return match[0]  # not one of our files: leave it alone
        stamped += 1
        return f'{match["attr"]}{link}?v={content_hash(asset)}{match["end"]}'

    page.write_bytes(ASSET_LINK.sub(replace, html).encode("utf-8"))
    return stamped


def main(site_folder: str) -> int:
    site = Path(site_folder).resolve()
    total = 0
    for page in sorted(site.rglob("*.html")):
        total += stamp_page(page, site)
    print(f"Stamped {total} CSS/JS links with content hashes.")
    return 0 if total else 1  # no links stamped means the build is broken


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
