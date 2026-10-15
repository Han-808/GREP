"""Rebuild public Examples after editing the two readable templates."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=ROOT.parent / "project-page/scene_liveboard.html")
args = parser.parse_args()
views = {scope: (ROOT / "templates" / f"examples-{scope}.html").read_text(encoding="utf-8")
         for scope in ("multi-room", "open-space")}
payload = json.dumps(views, ensure_ascii=False).replace("<", "\\u003c")
shell = (ROOT / "templates/scene_liveboard_shell.html").read_text(encoding="utf-8")
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(shell.replace("{{SCENE_VIEWS_JSON}}", payload), encoding="utf-8")
print(f"Built {args.output}")
