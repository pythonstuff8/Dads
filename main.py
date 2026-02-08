"""Duplicate Photo Finder - Detects and moves duplicate images using perceptual hashing."""

import os
import shutil
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from collections import defaultdict

from PIL import Image
import imagehash
from pillow_heif import register_heif_opener

register_heif_opener()

# --- Constants ---
SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif", ".webp",
    ".heic", ".heif",
}
DEFAULT_PHASH_THRESHOLD = 20
WINDOW_TITLE = "Duplicate Photo Finder"
WINDOW_SIZE = "720x580"


# --- Data Classes ---


@dataclass
class ImageInfo:
    path: Path
    phash: imagehash.ImageHash
    file_size: int
    modified_time: float


# --- Core Logic ---


def discover_images(root_folder, output_folder=None, error_callback=None):
    """Recursively walk directory tree and collect image file paths."""
    images = []
    output_path = Path(output_folder).resolve() if output_folder else None

    def on_error(err):
        if error_callback:
            error_callback(f"Access denied: {err.filename}")

    for dirpath, dirnames, filenames in os.walk(root_folder, onerror=on_error):
        # Skip the output folder if it's inside the source folder
        if output_path:
            resolved = Path(dirpath).resolve()
            if resolved == output_path or output_path in resolved.parents:
                continue
            # Also prune dirnames so os.walk doesn't descend into output_folder
            dirnames[:] = [
                d
                for d in dirnames
                if not (Path(dirpath) / d).resolve() == output_path
            ]

        for fname in filenames:
            if Path(fname).suffix.lower() in SUPPORTED_EXTENSIONS:
                images.append(Path(dirpath) / fname)

    return images


def compute_image_info(filepath):
    """Compute perceptual hash and metadata for one image. Returns None on failure."""
    try:
        img = Image.open(filepath)
        img.verify()
        img = Image.open(filepath)  # Re-open after verify
        img = img.convert("RGB")    # Normalize color mode (needed for HEIC etc.)
        p = imagehash.phash(img, hash_size=16)
        stat = filepath.stat()
        return ImageInfo(
            path=filepath,
            phash=p,
            file_size=stat.st_size,
            modified_time=stat.st_mtime,
        )
    except Exception:
        return None


class _BKTreeNode:
    __slots__ = ("item", "children")

    def __init__(self, item):
        self.item = item
        self.children = {}


class _BKTree:
    """BK-tree for efficient nearest-neighbor search in Hamming distance space."""

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
        """Return all items within the given Hamming distance threshold."""
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
    """Disjoint-set / union-find for grouping duplicate images."""

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
    """Return groups of 2+ images that are perceptual duplicates.

    Uses a BK-tree for efficient Hamming-distance neighbor search and
    union-find to merge overlapping matches into groups.
    """
    if len(image_infos) < 2:
        return []

    dist = lambda a, b: a.phash - b.phash

    # Build BK-tree
    tree = _BKTree(dist)
    for info in image_infos:
        tree.add(info)

    # Find all pairs within threshold, merge via union-find
    uf = _UnionFind(len(image_infos))
    id_to_idx = {id(info): i for i, info in enumerate(image_infos)}

    for i, info in enumerate(image_infos):
        matches = tree.find_within(info, phash_threshold)
        for match in matches:
            j = id_to_idx[id(match)]
            if i != j:
                uf.union(i, j)

    # Collect groups
    groups = defaultdict(list)
    for i, info in enumerate(image_infos):
        groups[uf.find(i)].append(info)

    return [g for g in groups.values() if len(g) >= 2]


def select_original(group):
    """Pick the best 'original' from a group of duplicates (largest file, earliest date)."""
    return max(
        group,
        key=lambda info: (
            info.file_size,
            -info.modified_time,
            -len(str(info.path)),
        ),
    )


def move_duplicate(source_path, output_folder):
    """Move a duplicate image to the output folder. Returns destination path or None on failure."""
    output_dir = Path(output_folder)
    output_dir.mkdir(parents=True, exist_ok=True)

    dest = output_dir / source_path.name

    # Handle name collisions
    if dest.exists():
        stem = source_path.stem
        suffix = source_path.suffix
        counter = 1
        while dest.exists():
            dest = output_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    try:
        shutil.move(str(source_path), str(dest))
        return dest
    except (PermissionError, OSError):
        return None


# --- GUI ---


class DuplicateFinderApp:
    def __init__(self, root):
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(600, 450)

        self.cancel_event = threading.Event()
        self.worker_thread = None

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.threshold_var = tk.IntVar(value=DEFAULT_PHASH_THRESHOLD)
        self.status_var = tk.StringVar(value="Ready")
        self.summary_var = tk.StringVar()

        self._build_gui()

    def _build_gui(self):
        # Main container with padding
        main_frame = ttk.Frame(self.root, padding=15)
        main_frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)

        row = 0

        # --- Source folder ---
        ttk.Label(main_frame, text="Source Folder:").grid(
            row=row, column=0, sticky="w", pady=(0, 5)
        )
        row += 1

        source_frame = ttk.Frame(main_frame)
        source_frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        source_frame.columnconfigure(0, weight=1)

        ttk.Entry(source_frame, textvariable=self.source_var).grid(
            row=0, column=0, sticky="ew", padx=(0, 5)
        )
        ttk.Button(source_frame, text="Browse...", command=self._browse_source).grid(
            row=0, column=1
        )
        row += 1

        # --- Output folder ---
        ttk.Label(main_frame, text="Output Folder (duplicates go here):").grid(
            row=row, column=0, sticky="w", pady=(0, 5)
        )
        row += 1

        output_frame = ttk.Frame(main_frame)
        output_frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        output_frame.columnconfigure(0, weight=1)

        ttk.Entry(output_frame, textvariable=self.output_var).grid(
            row=0, column=0, sticky="ew", padx=(0, 5)
        )
        ttk.Button(output_frame, text="Browse...", command=self._browse_output).grid(
            row=0, column=1
        )
        row += 1

        # --- Threshold slider ---
        threshold_frame = ttk.Frame(main_frame)
        threshold_frame.grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=(0, 10)
        )
        threshold_frame.columnconfigure(1, weight=1)

        ttk.Label(threshold_frame, text="Similarity Threshold:").grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        self.threshold_scale = ttk.Scale(
            threshold_frame,
            from_=1,
            to=60,
            variable=self.threshold_var,
            orient="horizontal",
            command=self._on_threshold_change,
        )
        self.threshold_scale.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.threshold_label = ttk.Label(
            threshold_frame, text=str(DEFAULT_PHASH_THRESHOLD), width=3
        )
        self.threshold_label.grid(row=0, column=2)
        row += 1

        # --- Buttons ---
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=row, column=0, columnspan=3, pady=(0, 10))

        self.start_btn = ttk.Button(
            button_frame, text="Start Scan", command=self._start_scan
        )
        self.start_btn.grid(row=0, column=0, padx=(0, 10))

        self.cancel_btn = ttk.Button(
            button_frame, text="Cancel", command=self._cancel_scan, state="disabled"
        )
        self.cancel_btn.grid(row=0, column=1)
        row += 1

        # --- Progress bar ---
        progress_frame = ttk.Frame(main_frame)
        progress_frame.grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=(0, 5)
        )
        progress_frame.columnconfigure(0, weight=1)

        self.progress_bar = ttk.Progressbar(
            progress_frame, mode="determinate", maximum=100
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.progress_label = ttk.Label(progress_frame, text="0/0")
        self.progress_label.grid(row=0, column=1)
        row += 1

        # --- Status ---
        ttk.Label(main_frame, textvariable=self.status_var).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 5)
        )
        row += 1

        # --- Log area ---
        ttk.Label(main_frame, text="Log:").grid(
            row=row, column=0, sticky="w", pady=(0, 3)
        )
        row += 1

        log_frame = ttk.Frame(main_frame)
        log_frame.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=(0, 10))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        main_frame.rowconfigure(row, weight=1)

        self.log_text = tk.Text(log_frame, height=10, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log_text.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        row += 1

        # --- Summary ---
        ttk.Label(main_frame, textvariable=self.summary_var, font=("", 10, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w"
        )

    def _on_threshold_change(self, value):
        self.threshold_label.config(text=str(int(float(value))))

    def _browse_source(self):
        folder = filedialog.askdirectory(title="Select Source Folder")
        if folder:
            self.source_var.set(folder)

    def _browse_output(self):
        folder = filedialog.askdirectory(title="Select Output Folder for Duplicates")
        if folder:
            self.output_var.set(folder)

    def _validate_inputs(self):
        source = self.source_var.get().strip()
        output = self.output_var.get().strip()

        if not source:
            messagebox.showerror("Error", "Please select a source folder.")
            return False
        if not os.path.isdir(source):
            messagebox.showerror("Error", f"Source folder does not exist:\n{source}")
            return False
        if not output:
            messagebox.showerror("Error", "Please select an output folder.")
            return False

        source_resolved = Path(source).resolve()
        output_resolved = Path(output).resolve()
        if source_resolved == output_resolved:
            messagebox.showerror(
                "Error", "Source and output folders cannot be the same."
            )
            return False

        return True

    def _start_scan(self):
        if not self._validate_inputs():
            return

        self.start_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.summary_var.set("")
        self.progress_bar["value"] = 0
        self.progress_label.config(text="0/0")

        # Clear log
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)

        self.cancel_event.clear()
        self.worker_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self.worker_thread.start()

    def _cancel_scan(self):
        self.cancel_event.set()
        self.status_var.set("Cancelling...")

    def _log(self, message):
        """Thread-safe logging to the text area."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.root.after(0, self._append_log, f"[{timestamp}] {message}")

    def _append_log(self, text):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _update_progress(self, current, total):
        self.root.after(0, self._set_progress, current, total)

    def _set_progress(self, current, total):
        self.progress_bar["maximum"] = total
        self.progress_bar["value"] = current
        self.progress_label.config(text=f"{current}/{total}")

    def _set_status(self, text):
        self.root.after(0, self.status_var.set, text)

    def _scan_worker(self):
        """Background thread: discover, hash, group, and move duplicates."""
        source = self.source_var.get().strip()
        output = self.output_var.get().strip()
        threshold = self.threshold_var.get()

        # Phase 1: Discover images
        self._set_status("Scanning for images...")
        self._log(f"Scanning {source}...")

        images = discover_images(
            source,
            output_folder=output,
            error_callback=lambda msg: self._log(msg),
        )

        if not images:
            self._log("No supported image files found.")
            self._set_status("No images found.")
            self.root.after(0, self._scan_complete)
            return

        self._log(f"Found {len(images)} images")

        # Phase 2: Compute hashes
        self._set_status("Computing image hashes...")
        infos = []
        errors = 0

        for i, img_path in enumerate(images):
            if self.cancel_event.is_set():
                self._log("Scan cancelled by user.")
                self._set_status("Cancelled.")
                self.root.after(0, self._scan_complete)
                return

            info = compute_image_info(img_path)
            if info:
                infos.append(info)
            else:
                errors += 1
                self._log(f"Skipped (error): {img_path.name}")

            self._update_progress(i + 1, len(images))

        # Phase 3: Group duplicates
        self._set_status("Identifying duplicates...")
        self._log("Grouping duplicates...")
        groups = group_duplicates(infos, phash_threshold=threshold)

        if not groups:
            self._log("No duplicates found.")
            self._set_status("Complete - no duplicates found.")
            self.root.after(
                0,
                self.summary_var.set,
                f"Scanned {len(infos)} images. No duplicates found. {errors} errors.",
            )
            self.root.after(0, self._scan_complete)
            return

        self._log(
            f"Found {len(groups)} duplicate group(s) "
            f"({sum(len(g) - 1 for g in groups)} duplicates)"
        )

        # Phase 4: Move duplicates
        self._set_status("Moving duplicates...")
        moved = 0
        move_errors = 0

        for group in groups:
            if self.cancel_event.is_set():
                self._log("Scan cancelled by user.")
                self._set_status("Cancelled.")
                self.root.after(0, self._scan_complete)
                return

            original = select_original(group)
            self._log(f"Original: {original.path.name} ({original.file_size:,} bytes)")

            for img in group:
                if img is original:
                    continue
                result = move_duplicate(img.path, output)
                if result:
                    moved += 1
                    self._log(f"  Moved: {img.path.name} -> {result.name}")
                else:
                    move_errors += 1
                    self._log(f"  Failed to move: {img.path.name}")

        # Phase 5: Summary
        total_errors = errors + move_errors
        summary = (
            f"{len(groups)} duplicate group(s) found. "
            f"{moved} file(s) moved. {total_errors} error(s)."
        )
        self._log(f"Done! {summary}")
        self._set_status("Complete.")
        self.root.after(0, self.summary_var.set, summary)
        self.root.after(0, self._scan_complete)

    def _scan_complete(self):
        """Re-enable controls after scan finishes."""
        self.start_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")


# --- Entry Point ---

if __name__ == "__main__":
    root = tk.Tk()
    app = DuplicateFinderApp(root)
    root.mainloop()
