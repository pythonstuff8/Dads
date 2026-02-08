"""Duplicate Photo Finder - Backend process for Electron IPC."""

import os
import sys
import json
import shutil
import threading
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass

from PIL import Image
import imagehash
from pillow_heif import register_heif_opener

register_heif_opener()

SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif", ".webp",
    ".heic", ".heif",
}
DEFAULT_PHASH_THRESHOLD = 20

cancel_event = threading.Event()


@dataclass
class ImageInfo:
    path: Path
    phash: imagehash.ImageHash
    file_size: int
    modified_time: float


def emit(event_type, **kwargs):
    """Send a JSON event to Electron via stdout."""
    msg = {"event": event_type, **kwargs}
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def discover_images(root_folder, output_folder=None):
    images = []
    output_path = Path(output_folder).resolve() if output_folder else None

    def on_error(err):
        emit("log", message=f"Access denied: {err.filename}")

    for dirpath, dirnames, filenames in os.walk(root_folder, onerror=on_error):
        if output_path:
            resolved = Path(dirpath).resolve()
            if resolved == output_path or output_path in resolved.parents:
                continue
            dirnames[:] = [
                d for d in dirnames
                if not (Path(dirpath) / d).resolve() == output_path
            ]
        for fname in filenames:
            if Path(fname).suffix.lower() in SUPPORTED_EXTENSIONS:
                images.append(Path(dirpath) / fname)
    return images


def compute_image_info(filepath):
    try:
        img = Image.open(filepath)
        img.verify()
        img = Image.open(filepath)
        img = img.convert("RGB")
        p = imagehash.phash(img, hash_size=16)
        stat = filepath.stat()
        return ImageInfo(path=filepath, phash=p, file_size=stat.st_size, modified_time=stat.st_mtime)
    except Exception:
        return None


class _BKTreeNode:
    __slots__ = ("item", "children")
    def __init__(self, item):
        self.item = item
        self.children = {}


class _BKTree:
    def __init__(self, distance_func):
        self._dist = distance_func
        self._root = None

    def add(self, item):
        if self._root is None:
            self._root = _BKTreeNode(item)
            return
        node = self._root
        while True:
            d = self._dist(item, node.item)
            if d in node.children:
                node = node.children[d]
            else:
                node.children[d] = _BKTreeNode(item)
                return

    def find_within(self, item, threshold):
        if self._root is None:
            return []
        results = []
        stack = [self._root]
        while stack:
            node = stack.pop()
            d = self._dist(item, node.item)
            if d <= threshold:
                results.append(node.item)
            for k, child in node.children.items():
                if d - threshold <= k <= d + threshold:
                    stack.append(child)
        return results


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1


def group_duplicates(image_infos, phash_threshold=DEFAULT_PHASH_THRESHOLD):
    if len(image_infos) < 2:
        return []
    dist = lambda a, b: a.phash - b.phash
    tree = _BKTree(dist)
    for info in image_infos:
        tree.add(info)
    uf = _UnionFind(len(image_infos))
    id_to_idx = {id(info): i for i, info in enumerate(image_infos)}
    for i, info in enumerate(image_infos):
        matches = tree.find_within(info, phash_threshold)
        for match in matches:
            j = id_to_idx[id(match)]
            if i != j:
                uf.union(i, j)
    groups = defaultdict(list)
    for i, info in enumerate(image_infos):
        groups[uf.find(i)].append(info)
    return [g for g in groups.values() if len(g) >= 2]


def select_original(group):
    return max(group, key=lambda info: (info.file_size, -info.modified_time, -len(str(info.path))))


def copy_duplicate(source_path, output_folder):
    """Copy a duplicate image to the output folder. Original stays in place."""
    output_dir = Path(output_folder)
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / source_path.name
    if dest.exists():
        stem = source_path.stem
        suffix = source_path.suffix
        counter = 1
        while dest.exists():
            dest = output_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    try:
        shutil.copy2(str(source_path), str(dest))
        return dest
    except (PermissionError, OSError):
        return None


def run_scan(source, output, threshold):
    cancel_event.clear()

    emit("status", message="Scanning for images...")
    emit("log", message=f"Scanning {source}...")

    images = discover_images(source, output_folder=output)

    if not images:
        emit("log", message="No supported image files found.")
        emit("status", message="No images found.")
        emit("complete", summary="No images found.", groups=0, moved=0, errors=0)
        return

    emit("log", message=f"Found {len(images)} images")
    emit("status", message="Computing image hashes...")

    infos = []
    errors = 0

    for i, img_path in enumerate(images):
        if cancel_event.is_set():
            emit("log", message="Scan cancelled by user.")
            emit("status", message="Cancelled.")
            emit("cancelled")
            return

        info = compute_image_info(img_path)
        if info:
            infos.append(info)
        else:
            errors += 1
            emit("log", message=f"Skipped (error): {img_path.name}")

        emit("progress", current=i + 1, total=len(images))

    emit("status", message="Identifying duplicates...")
    emit("log", message="Grouping duplicates...")
    groups = group_duplicates(infos, phash_threshold=threshold)

    if not groups:
        emit("log", message="No duplicates found.")
        emit("status", message="Complete - no duplicates found.")
        summary = f"Scanned {len(infos)} images. No duplicates found. {errors} errors."
        emit("complete", summary=summary, groups=0, moved=0, errors=errors)
        return

    total_dupes = sum(len(g) - 1 for g in groups)
    emit("log", message=f"Found {len(groups)} duplicate group(s) ({total_dupes} duplicates)")
    emit("status", message="Copying similar photos...")

    moved = 0
    move_errors = 0

    for group in groups:
        if cancel_event.is_set():
            emit("log", message="Scan cancelled by user.")
            emit("status", message="Cancelled.")
            emit("cancelled")
            return

        original = select_original(group)
        emit("log", message=f"Original (kept): {original.path.name} ({original.file_size:,} bytes)")

        for img in group:
            if img is original:
                continue
            result = copy_duplicate(img.path, output)
            if result:
                moved += 1
                emit("log", message=f"  Copied similar: {img.path.name} -> {result.name}")
            else:
                move_errors += 1
                emit("log", message=f"  Failed to copy: {img.path.name}")

    total_errors = errors + move_errors
    summary = f"{len(groups)} duplicate group(s) found. {moved} similar photo(s) copied. {total_errors} error(s)."
    emit("log", message=f"Done! {summary}")
    emit("status", message="Complete.")
    emit("complete", summary=summary, groups=len(groups), moved=moved, errors=total_errors)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            emit("error", message="Invalid JSON command")
            continue

        action = cmd.get("cmd")

        if action == "scan":
            source = cmd.get("source", "")
            output = cmd.get("output", "")
            threshold = cmd.get("threshold", DEFAULT_PHASH_THRESHOLD)
            run_scan(source, output, threshold)
        elif action == "cancel":
            cancel_event.set()
        elif action == "ping":
            emit("pong")
        elif action == "quit":
            break
        else:
            emit("error", message=f"Unknown command: {action}")


if __name__ == "__main__":
    main()
