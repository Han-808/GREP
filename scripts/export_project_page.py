"""Export the curated GREP website as a self-contained GitHub Pages directory.

The local Python preview and captured source data remain untouched. Only the
fields consumed by the public UI and images referenced by retained examples are
included. Scores and current deduction object IDs are copied without changes.

Usage: python website/export_static.py --output /path/to/empty/staging/directory
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
from urllib.parse import unquote, urljoin, urlsplit

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR if (SCRIPT_DIR / "index.html").is_file() else SCRIPT_DIR.parent / "website"
SNAPSHOT = ROOT.parent / "data/website/site"
APIS = {
    "/api/status": "./status.json",
    "/api/leaderboard": "./leaderboard.json",
    "/api/multi-room-scenes": "./multi_room_sceneboard.json",
}
HTML_FILES = ("index.html", "scene_liveboard.html", "leaderboard.html", "asset-credits.html")
ASSET_FILES = {
    "home.css", "examples.css", "leaderboard.css", "leaderboard.js",
    "public-board-shared.css", "public-board-navigation.js", "public-board-policy.js",
    "room-demo.js", "realistic-room.js", "realism-manifest.json",
}
ASSET_DIRS = {"lighting", "models", "textures", "vendor"}
VIEW_PATTERN = re.compile(r'(<script id="scene-views" type="application/json">)(.*?)(</script>)', re.S)
STATUS_FIELDS = "case_id status score liveboard_score l1_score liveboard_l1_score l3_score liveboard_l3_score liveboard_spatial_reasoning liveboard_visual_coherence coverage image_url scene_type object_count score_status metrics liveboard_metrics acceptance_status".split()
LAYOUT_FIELDS = "scene_id layout_id status room_count object_count scene_image_url aggregate_shape score physical scene_quality functional_semantics visual_coherence coverage metrics".split()
ROOM_FIELDS = "room_id room_key case_id order_index scene_type object_count image_url status coverage score physical scene_quality functional_semantics visual_coherence metrics".split()
MODEL_FIELDS = "key label completed expected overall scored_case_count submetric_scores fine_metric_scores".split()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def keep(value, fields):
    return {key: deepcopy(value[key]) for key in fields if key in value}


def deductions(row):
    # Preserve the current report attribution; never recover historical findings.
    return [keep(item, ("metric", "object_ids")) for item in row.get("deductions", [])]


def project_status(source):
    data = keep(source, ("counts", "display_population"))
    data["datasets"] = []
    for dataset in source["datasets"]:
        current = keep(dataset, ("key", "label", "lifecycle"))
        current["scenes"] = []
        for scene in dataset["scenes"]:
            row = keep(scene, STATUS_FIELDS)
            row["deductions"] = deductions(scene)
            current["scenes"].append(row)
        data["datasets"].append(current)
    return data


def project_multi(source):
    data = keep(source, ("counts", "reporting_counts"))
    data["datasets"] = []
    for dataset in source["datasets"]:
        current = keep(dataset, ("key", "label"))
        current["layouts"] = []
        for layout in dataset["layouts"]:
            row = keep(layout, LAYOUT_FIELDS)
            row["rooms"] = []
            for room in layout["rooms"]:
                value = keep(room, ROOM_FIELDS)
                value["deductions"] = deductions(room)
                row["rooms"].append(value)
            current["layouts"].append(row)
        data["datasets"].append(current)
    return data


def project_board(source):
    data = keep(source, ("id", "label", "coverage_note", "submetrics", "fine_metrics", "metrics"))
    data["models"] = [keep(model, MODEL_FIELDS) for model in source.get("models", [])]
    data["scoring_profile"] = keep(source.get("scoring_profile", {}), ("metric_formula",))
    data["methodology"] = keep(source.get("methodology", {}), ("overall_formula", "score_mapping", "aggregation_policy"))
    return data


def project_leaderboard(source):
    data = project_board(source)
    data["default_leaderboard_view"] = source["default_leaderboard_view"]
    data["leaderboard_views"] = [project_board(board) for board in source["leaderboard_views"]]
    return data


def relative_text(text):
    for route, destination in APIS.items():
        text = text.replace(route, destination)
    for prefix in ("/assets/", "/scene_liveboard.html", "/leaderboard.html"):
        text = re.sub(r'([\"\'`])' + re.escape(prefix), lambda m: m[1] + "." + prefix, text)
    # Exact root links occur in the shared navigation and header markup.
    text = re.sub(r'([\"\'`])/(\1)', lambda m: m[1] + "./" + m[2], text)
    return re.sub(r"\s*setInterval\(load,\s*2500\);?", "", text)


def export_html(text):
    def views(match):
        payload = {key: relative_text(value) for key, value in json.loads(match[2]).items()}
        encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
        return match[1] + encoded + match[3]
    return relative_text(VIEW_PATTERN.sub(views, text))


def export_editable(output):
    """Optional small source bundle for rebuilding the embedded example views."""
    if output.exists() and any(output.iterdir()):
        raise ValueError("Editable source output must be an empty directory.")
    (output / "templates").mkdir(parents=True, exist_ok=True)
    shell = export_html((ROOT / "scene_liveboard.html").read_text(encoding="utf-8"))
    payload = json.loads(VIEW_PATTERN.search(shell)[2])
    for scope, page in payload.items():
        (output / "templates" / f"examples-{scope}.html").write_text(page, encoding="utf-8")
    shell = VIEW_PATTERN.sub(lambda match: match[1] + "{{SCENE_VIEWS_JSON}}" + match[3], shell)
    (output / "templates/scene_liveboard_shell.html").write_text(shell, encoding="utf-8")
    helper = '''"""Rebuild public Examples after editing the two readable templates."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=ROOT.parent / "project-page/scene_liveboard.html")
args = parser.parse_args()
views = {scope: (ROOT / "templates" / f"examples-{scope}.html").read_text(encoding="utf-8")
         for scope in ("multi-room", "open-space")}
payload = json.dumps(views, ensure_ascii=False).replace("<", "\\\\u003c")
shell = (ROOT / "templates/scene_liveboard_shell.html").read_text(encoding="utf-8")
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(shell.replace("{{SCENE_VIEWS_JSON}}", payload), encoding="utf-8")
print(f"Built {args.output}")
'''
    (output / "rebuild_examples.py").write_text(helper, encoding="utf-8")


def image_urls(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_url") and isinstance(item, str) and item.startswith("/"):
                yield item
            else:
                yield from image_urls(item)
    elif isinstance(value, list):
        for item in value:
            yield from image_urls(item)


def relative_images(value):
    if isinstance(value, dict):
        return {key: "." + item if key.endswith("_url") and isinstance(item, str) and item.startswith("/") else relative_images(item) for key, item in value.items()}
    if isinstance(value, list):
        return [relative_images(item) for item in value]
    return value


def verify_projection(original, exported, label):
    """Every retained field equals the source, including exact floating scores."""
    def compare(before, after, path):
        if isinstance(after, dict):
            for key, value in after.items():
                if key not in before:
                    if key == "deductions" and value == []:
                        continue
                    if key in {"scoring_profile", "methodology", "models"} and not value:
                        continue
                    raise AssertionError(f"New data field: {path}.{key}")
                compare(before[key], value, f"{path}.{key}")
        elif isinstance(after, list):
            assert len(before) == len(after), f"Changed population: {path}"
            for index, item in enumerate(after):
                compare(before[index], item, f"{path}[{index}]")
        else:
            assert before == after, f"Changed field: {path}"
    compare(original, exported, label)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in {"href", "src"} and value and "${" not in value:
                self.urls.append(value)


def verify_urls(output):
    """Resolve actual dependencies under a project path, not domain root."""
    checked = set()
    problems = []
    base = "https://example.github.io/GREP/"

    def check(url, source, document=False):
        if not url or "${" in url or url.startswith(("#", "data:", "http:", "https:", "mailto:")):
            return
        origin = base + ("index.html" if document else source)
        resolved = urlsplit(urljoin(origin, unescape(url)))
        if not resolved.path.startswith("/GREP/"):
            problems.append(f"Escapes project path: {source} -> {url}")
            return
        relative = unquote(resolved.path.removeprefix("/GREP/")) or "index.html"
        if relative.endswith("/"):
            relative += "index.html"
        file = (output / relative).resolve()
        if not file.is_relative_to(output) or not file.is_file():
            problems.append(f"Missing dependency: {source} -> {url}")
        checked.add((source, url))

    for file in sorted(output.rglob("*")):
        if not file.is_file():
            continue
        name = file.relative_to(output).as_posix()
        if file.suffix in {".html", ".js", ".css"}:
            text = file.read_text(encoding="utf-8")
            if file.suffix == ".html":
                segments = [text] + list(json.loads(VIEW_PATTERN.search(text)[2]).values()) if VIEW_PATTERN.search(text) else [text]
                for segment in segments:
                    parser = Links(); parser.feed(segment)
                    for url in parser.urls:
                        check(url, name)
                    for url in re.findall(r'fetch\([\"\']([^\"\']+)[\"\']', segment):
                        check(url, name)
                    for mapping in re.findall(r'<script type="importmap">(.*?)</script>', segment, re.S):
                        for url in json.loads(mapping).get("imports", {}).values():
                            check(url, name)
            if file.suffix == ".js":
                for url in re.findall(r'(?:from\s*|import\s*)[\"\'](\.[^\"\']+)[\"\']', text):
                    check(url, name)
                for url in re.findall(r'fetch\([\"\']([^\"\']+)[\"\']', text):
                    check(url, name, document=True)
                if name == "assets/realistic-room.js":
                    for url in re.findall(r'[\"\']((?:models|textures|lighting)/[^\"\']+)[\"\']', text):
                        check(url, name)
                if name == "assets/public-board-navigation.js":
                    for url in re.findall(r'[\"\'`](\./(?:scene_liveboard\.html|leaderboard\.html)?)[?\"\'`]', text):
                        check(url, name, document=True)
            for url in re.findall(r'url\([\"\']?([^\)\"\']+)', text):
                check(url, name)
        elif file.suffix == ".gltf":
            model = read_json(file)
            for row in model.get("buffers", []) + model.get("images", []):
                if "uri" in row:
                    check(row["uri"], name)
        elif name in {"status.json", "multi_room_sceneboard.json"}:
            def find_images(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key.endswith("_url") and isinstance(item, str):
                            check(item, name, document=True)
                        else:
                            find_images(item)
                elif isinstance(value, list):
                    for item in value:
                        find_images(item)
            find_images(read_json(file))
    if problems:
        raise ValueError("\n".join(problems))
    return len(checked)


def export(output, audit):
    output = output.resolve()
    if output == ROOT or output.is_relative_to(ROOT) or ROOT.is_relative_to(output):
        raise ValueError("Export into a separate staging directory, outside the source website.")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty; use a new staging path.")
    output.mkdir(parents=True, exist_ok=True)
    sources = {name: read_json(ROOT / name) for name in ("status.json", "multi_room_sceneboard.json", "leaderboard.json")}
    projected = {
        "status.json": project_status(sources["status.json"]),
        "multi_room_sceneboard.json": project_multi(sources["multi_room_sceneboard.json"]),
        "leaderboard.json": project_leaderboard(sources["leaderboard.json"]),
    }
    for name, data in projected.items():
        verify_projection(sources[name], data, name)
        write_json(output / name, relative_images(data))

    images = sorted(set(url for data in projected.values() for url in image_urls(data)))
    for url in images:
        source = (SNAPSHOT / url.lstrip("/")).resolve()
        if not source.is_relative_to(SNAPSHOT.resolve()) or not source.is_file():
            raise ValueError(f"Retained image missing or outside snapshot: {url}")
        target = output / url.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    for name in HTML_FILES:
        (output / name).write_text(export_html((ROOT / name).read_text(encoding="utf-8")), encoding="utf-8")
    paper = ROOT / "paper/GREP.pdf"
    if paper.is_file():
        (output / "paper").mkdir(exist_ok=True)
        shutil.copy2(paper, output / "paper/GREP.pdf")
    for file in sorted((ROOT / "assets").rglob("*")):
        if not file.is_file():
            continue
        relative = file.relative_to(ROOT / "assets")
        if len(relative.parts) == 1 and file.name not in ASSET_FILES:
            continue
        if len(relative.parts) > 1 and relative.parts[0] not in ASSET_DIRS:
            continue
        target = output / "assets" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if file.suffix == ".js" and relative.parts[0] != "vendor":
            text = relative_text(file.read_text(encoding="utf-8"))
            if file.name == "public-board-policy.js":
                policy = read_json(ROOT / "public-board-policy.json")
                text = "window.GREP_PUBLIC_BOARDS = " + json.dumps(keep(policy, ("default_scope", "scopes"))) + ";\n" + text[text.index("(() => {"):]
            target.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(file, target)
    (output / ".nojekyll").write_text("", encoding="utf-8")

    checks = verify_urls(output)
    records = [{"path": file.relative_to(output).as_posix(), "bytes": file.stat().st_size, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()} for file in sorted(output.rglob("*")) if file.is_file()]
    oversized = [row for row in records if row["bytes"] >= 100_000_000]
    if oversized:
        raise ValueError(f"Files exceed GitHub 100 MB limit: {oversized}")
    summary = {
        "files": len(records), "bytes": sum(row["bytes"] for row in records),
        "largest_file": max(records, key=lambda row: row["bytes"]),
        "retained_images": len(images), "verified_local_references": checks,
        "project_path": "/GREP/", "source_scores_and_object_ids_unchanged": True,
        "excluded_scopes": read_json(ROOT / "public-board-policy.json")["excluded_scopes"],
        "public_data_bytes": {name: (output / name).stat().st_size for name in projected},
    }
    audit.parent.mkdir(parents=True, exist_ok=True)
    write_json(audit, {**summary, "manifest": records})
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, help="Local verification manifest; not published.")
    parser.add_argument("--website", type=Path, default=ROOT, help="Current curated preview website source directory.")
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT, help="Captured website directory containing retained PNGs.")
    parser.add_argument("--editable-output", type=Path, help="Optional separate readable Examples source directory.")
    args = parser.parse_args()
    ROOT = args.website.resolve()
    SNAPSHOT = args.snapshot.resolve()
    export(args.output, args.audit or args.output.with_name(args.output.name + "-audit.json"))
    if args.editable_output:
        export_editable(args.editable_output)
