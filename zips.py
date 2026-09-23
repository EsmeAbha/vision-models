"""Unpack an uploaded zip somewhere safe, and find the folder that matters.

Lifted from the folder page, which learned both of these the hard way, and
put here so it can be used without importing a module that builds interface
components at import time.

Two problems a zip brings. A zip entry can name ../../etc or an absolute
path, and extractall follows it happily -- so entries that would land outside
the destination are dropped rather than written. And zipping a folder nests
it once, sometimes twice, so the top level of the archive is a wrapper rather
than the thing inside; without stepping through it, "which level names a row"
points at the wrapper and every unit collapses into one.
"""
from __future__ import annotations

import os
import time
import zipfile

_here = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(_here, "uploads", "finai")

_cache = {}


def _safe_members(zf, dest):
    """Yield only the members that stay inside dest."""
    dest = os.path.realpath(dest)
    for m in zf.infolist():
        name = m.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            continue
        if os.path.isabs(name) or (len(name) > 1 and name[1] == ":"):
            continue
        target = os.path.realpath(os.path.join(dest, name))
        if target == dest or target.startswith(dest + os.sep):
            yield m


def descend_single(root):
    """Step through wrapper folders like 'archive/archive/'."""
    for _ in range(4):
        try:
            entries = [e for e in os.listdir(root)
                       if not e.startswith((".", "__"))]
        except OSError:
            return root
        subdirs = [e for e in entries if os.path.isdir(os.path.join(root, e))]
        if len(entries) == 1 and len(subdirs) == 1:
            root = os.path.join(root, subdirs[0])
        else:
            return root
    return root


def extract(zip_path, dest_dir=UPLOAD_DIR):
    """(root, note). Unpacked once per upload and reused."""
    zip_path = str(getattr(zip_path, "name", zip_path))
    key = (zip_path, os.path.getsize(zip_path))
    if key in _cache and os.path.isdir(_cache[key]):
        return _cache[key], "using the already-unpacked copy"

    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(dest_dir, f"zip_{stamp}")
    os.makedirs(dest, exist_ok=True)

    n = 0
    with zipfile.ZipFile(zip_path) as zf:
        members = list(_safe_members(zf, dest))
        skipped = len(zf.infolist()) - len(members)
        for m in members:
            zf.extract(m, dest)
            n += 1

    root = descend_single(dest)
    note = f"unpacked {n} entr{'y' if n == 1 else 'ies'}"
    if skipped:
        note += f" ({skipped} unsafe path(s) skipped)"
    if root != dest:
        note += f"; using inner folder '{os.path.basename(root)}'"
    _cache[key] = root
    return root, note
