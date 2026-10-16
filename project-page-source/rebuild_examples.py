"""Rebuild public Examples after editing the two readable templates."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
SITE = ROOT.parent / "project-page"


def version_demo_modules():
    """Version the demo's own module imports, from leaf dependencies upward."""
    visited = set()

    def visit(path):
        if path in visited:
            return
        visited.add(path)
        source = path.read_text(encoding="utf-8")

        def replace(match):
            relative = match.group(2)
            if relative.startswith("./vendor/"):
                return match.group(0)
            dependency = path.parent / relative
            if not dependency.is_file():
                raise FileNotFoundError(dependency)
            visit(dependency)
            digest = hashlib.sha256(dependency.read_bytes()).hexdigest()[:12]
            return f"{match.group(1)}{relative}?v={digest}{match.group(3)}"

        updated = re.sub(r'''(from\s+['"])(\./[^'"?]+\.js)(?:\?v=[a-f0-9]+)?(['"])''', replace, source)
        if updated != source:
            path.write_text(updated, encoding="utf-8", newline="\n")

    visit(SITE / "assets/room-demo.js")


def version_assets(html):
    """Keep page markup and its local scripts/styles on the same revision."""
    def replace(match):
        url = match.group(1)
        asset = SITE / url.removeprefix("./")
        if not asset.is_file():
            raise FileNotFoundError(asset)
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()[:12]
        return f"{url}?v={digest}"
    return re.sub(r'(\./assets/[^\s"\'<>?]+\.(?:css|js))(?:\?v=[a-f0-9]+)?', replace, html)


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=ROOT.parent / "project-page/scene_liveboard.html")
args = parser.parse_args()
version_demo_modules()
views = {scope: version_assets((ROOT / "templates" / f"examples-{scope}.html").read_text(encoding="utf-8"))
         for scope in ("multi-room", "open-space")}
payload = json.dumps(views, ensure_ascii=False).replace("<", "\\u003c")
shell = (ROOT / "templates/scene_liveboard_shell.html").read_text(encoding="utf-8")
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(shell.replace("{{SCENE_VIEWS_JSON}}", payload), encoding="utf-8", newline="\n")
for entry in ("index.html", "leaderboard.html"):
    page = SITE / entry
    page.write_text(version_assets(page.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
print(f"Built {args.output}")
