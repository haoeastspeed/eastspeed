# -*- coding: utf-8 -*-
"""重打便携版与源码 zip（标准库 zipfile，结构可控）。"""
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")

PORTABLE_SRC = os.path.join(DIST, "DongFangSpeed")
PORTABLE_ZIP = os.path.join(DIST, "东方神速-便携版.zip")
SOURCE_ZIP = os.path.join(DIST, "东方神速-源码.zip")

SKIP_DIR_NAMES = {".git", "dist", "build", "__pycache__",
                  ".pytest_cache", "output", ".idea", ".vscode"}
SKIP_SUFFIX = {".pyc", ".pyo"}


def add_tree(zf, base_dir, arc_top, skip_dirs=None, skip_suffix=None):
    skip_dirs = skip_dirs or set()
    skip_suffix = skip_suffix or set()
    count = 0
    for dirpath, dirnames, filenames in os.walk(base_dir):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in skip_suffix:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, base_dir)
            arc = arc_top + "/" + rel.replace(os.sep, "/")
            zf.write(full, arc)
            count += 1
    return count


def main():
    with zipfile.ZipFile(PORTABLE_ZIP, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        n1 = add_tree(zf, PORTABLE_SRC, "DongFangSpeed")
    print("portable files:", n1, "size MB:",
          round(os.path.getsize(PORTABLE_ZIP) / 1048576, 2))

    with zipfile.ZipFile(SOURCE_ZIP, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as zf:
        n2 = add_tree(zf, ROOT, "pydownloader",
                      skip_dirs=SKIP_DIR_NAMES, skip_suffix=SKIP_SUFFIX)
    print("source files:", n2, "size MB:",
          round(os.path.getsize(SOURCE_ZIP) / 1048576, 2))


if __name__ == "__main__":
    main()
