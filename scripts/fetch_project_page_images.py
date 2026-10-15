"""Hydrate only the retained public case images from a pinned GREP release.

No authentication, third-party packages, or raw experiment data are required.
The downloaded ZIP and each selected PNG must match their committed SHA-256.
Existing matching images are reused. The ZIP stays in a temporary directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

REPO = Path(__file__).resolve().parent.parent
PREFIXES = {"scene", "anthropic-scene", "multi-room-scene", "multi-room-room"}
CHUNK = 1024 * 1024


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value or path.as_posix() != value:
        raise ValueError(f"Unsafe relative path: {value}")
    return path


def load_manifest(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported image manifest schema")
    source = data["source"]
    url = urlsplit(source["url"])
    if url.scheme != "https" or url.netloc != "github.com" or not url.path.startswith("/Han-808/GREP/releases/download/"):
        raise ValueError("Expected the public GREP GitHub release URL")
    if not isinstance(source["bytes"], int) or not 0 < source["bytes"] < 2_000_000_000:
        raise ValueError("Unexpected source archive size")
    if not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
        raise ValueError("Invalid archive SHA-256")
    seen = set()
    for image in data["images"]:
        relative = safe_path(image["path"])
        member = safe_path(image["member"])
        if relative.parts[0] not in PREFIXES or relative.suffix != ".png" or member.as_posix() != "website/site/" + relative.as_posix():
            raise ValueError(f"Unexpected public image mapping: {image['path']}")
        if image["path"] in seen:
            raise ValueError(f"Duplicate image: {image['path']}")
        seen.add(image["path"])
        if not isinstance(image["bytes"], int) or not 8 <= image["bytes"] < 10_000_000:
            raise ValueError(f"Unexpected image size: {image['path']}")
        if not re.fullmatch(r"[0-9a-f]{64}", image["sha256"]):
            raise ValueError(f"Invalid image SHA-256: {image['path']}")
    return data


def target_path(root, relative):
    target = root.joinpath(*safe_path(relative).parts)
    for path in (target, *target.parents):
        if path == root:
            break
        if path.is_symlink():
            raise ValueError(f"Symlink is not allowed in output path: {relative}")
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"Output path escaped the project page: {relative}")
    return target


def matches(target, image):
    return target.is_file() and target.stat().st_size == image["bytes"] and sha256(target) == image["sha256"]


def download(source, target, max_seconds=900):
    """Stream at most the pinned byte count; retry transient failures twice."""
    deadline = time.monotonic() + max_seconds
    for attempt in range(1, 4):
        total = 0
        digest = hashlib.sha256()
        try:
            request = Request(source["url"], headers={"User-Agent": "GREP-GitHub-Pages", "Accept": "application/octet-stream"})
            with urlopen(request, timeout=60) as response, target.open("wb") as output:
                if response.status != 200:
                    raise ValueError(f"Unexpected archive HTTP status: {response.status}")
                length = response.headers.get("Content-Length")
                if length is not None and int(length) != source["bytes"]:
                    raise ValueError("Archive Content-Length differs from the pinned release")
                while chunk := response.read(CHUNK):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Archive download exceeded the total time limit")
                    total += len(chunk)
                    if total > source["bytes"]:
                        raise ValueError("Archive exceeds the pinned release size")
                    output.write(chunk)
                    digest.update(chunk)
            if total != source["bytes"] or digest.hexdigest() != source["sha256"]:
                raise ValueError("Downloaded archive size or SHA-256 mismatch")
            return
        except (OSError, TimeoutError) as error:
            if attempt == 3 or time.monotonic() >= deadline:
                raise
            print(f"Archive download attempt {attempt} failed: {type(error).__name__}; retrying.", flush=True)
    raise RuntimeError("Archive download failed")


def verify_archive(path, source):
    if path.stat().st_size != source["bytes"] or sha256(path) != source["sha256"]:
        raise ValueError("Local archive size or SHA-256 does not match the manifest")


def extract_selected(archive, root, images):
    """Read exact approved members; never call extractall or trust ZIP paths."""
    with zipfile.ZipFile(archive) as handle:
        selected = {image["member"] for image in images}
        index = {}
        for entry in handle.infolist():
            if entry.filename in selected:
                if entry.filename in index:
                    raise ValueError(f"Duplicate ZIP member: {entry.filename}")
                index[entry.filename] = entry
        for image in images:
            entry = index.get(image["member"])
            if entry is None:
                raise ValueError(f"Required image missing from archive: {image['member']}")
            mode = entry.external_attr >> 16
            if entry.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
                raise ValueError(f"ZIP member is not a regular file: {entry.filename}")
            if entry.file_size != image["bytes"]:
                raise ValueError(f"ZIP member size mismatch: {entry.filename}")
            target = target_path(root, image["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, total = hashlib.sha256(), 0
            with tempfile.NamedTemporaryFile(prefix=".grep-image-", dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                try:
                    with handle.open(entry) as source:
                        while chunk := source.read(CHUNK):
                            if total == 0 and not chunk.startswith(b"\x89PNG\r\n\x1a\n"):
                                raise ValueError(f"Expected a PNG image: {entry.filename}")
                            total += len(chunk)
                            if total > image["bytes"]:
                                raise ValueError(f"Image exceeds its pinned size: {entry.filename}")
                            output.write(chunk); digest.update(chunk)
                    if total != image["bytes"] or digest.hexdigest() != image["sha256"]:
                        raise ValueError(f"Image SHA-256 mismatch: {entry.filename}")
                except BaseException:
                    output.close(); temporary.unlink(missing_ok=True)
                    raise
            temporary.replace(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=REPO / "project-page-source/images-manifest.json")
    parser.add_argument("--output", type=Path, default=REPO / "project-page")
    parser.add_argument("--archive", type=Path, help="Optional already-downloaded pinned release ZIP.")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    root = args.output.resolve(); root.mkdir(parents=True, exist_ok=True)
    missing = [image for image in manifest["images"] if not matches(target_path(root, image["path"]), image)]
    if missing:
        if args.archive:
            verify_archive(args.archive, manifest["source"])
            extract_selected(args.archive, root, missing)
        else:
            with tempfile.TemporaryDirectory(prefix="grep-page-images-") as directory:
                archive = Path(directory) / manifest["source"]["name"]
                print(f"Downloading pinned {manifest['source']['name']} for {len(missing)} public images…", flush=True)
                download(manifest["source"], archive)
                extract_selected(archive, root, missing)
    for image in manifest["images"]:
        if not matches(target_path(root, image["path"]), image):
            raise ValueError(f"Final image verification failed: {image['path']}")
    print(json.dumps({"images_verified": len(manifest["images"]), "images_restored": len(missing), "bytes": sum(image["bytes"] for image in manifest["images"])}, indent=2))


if __name__ == "__main__":
    main()
