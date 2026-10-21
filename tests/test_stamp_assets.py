"""Frontend build step: CSS/JS links get a content-hash version (browser caching)."""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("stamp_assets", ROOT / "frontend" / "nginx" / "stamp_assets.py")
stamp_assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stamp_assets)


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def test_links_get_content_hashes_and_change_with_the_file(tmp_path):
    (tmp_path / "css").mkdir()
    (tmp_path / "inhouse" / "js").mkdir(parents=True)
    (tmp_path / "css" / "site.css").write_text("body{}")
    (tmp_path / "inhouse" / "js" / "app.js").write_text("run()")
    (tmp_path / "page.html").write_text(
        '<link href="/css/site.css?v=old-manual"><script src="https://cdn.example/x.js"></script>'
        '<script src="/js/missing.js?v=1"></script>')
    (tmp_path / "inhouse" / "index.html").write_text('<script src="js/app.js?v=23"></script>')

    assert stamp_assets.main(str(tmp_path)) == 0
    page = (tmp_path / "page.html").read_text()
    assert f'href="/css/site.css?v={short_hash("body{}")}"' in page
    assert 'src="https://cdn.example/x.js"' in page and 'src="/js/missing.js?v=1"' in page  # left alone
    assert f'src="js/app.js?v={short_hash("run()")}"' in (tmp_path / "inhouse" / "index.html").read_text()


def test_every_link_in_the_real_site_resolves(tmp_path):
    import shutil
    for name in ("css", "js", "inhouse", "admin", "account"):
        shutil.copytree(ROOT / "frontend" / name, tmp_path / name)
    for page in (ROOT / "frontend").glob("*.html"):
        shutil.copy(page, tmp_path)
    assert stamp_assets.main(str(tmp_path)) == 0
    for page in tmp_path.rglob("*.html"):
        for match in stamp_assets.ASSET_LINK.finditer(page.read_text(encoding="utf-8")):
            assert re.search(r'\?v=[0-9a-f]{12}"$', match[0]), f"{page.name}: {match['path']} does not exist"
