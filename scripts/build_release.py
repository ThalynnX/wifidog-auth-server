#!/usr/bin/env python3
"""Build WiFiDog release tarballs from the current Git HEAD.

Usage:
    python3 scripts/build_release.py --version v2.0.3

Outputs to ./dist:
    wifidog-portal-v2.0.3-full.tar.gz
    wifidog-portal-v2.0.3-server.tar.gz
    router packages copied from router-packages/
    RELEASE-NOTES-v2.0.3.md
    SHA256SUMS.txt
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_head(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest.parent / "head.tar"
    with archive.open("wb") as f:
        subprocess.run(
            ["git", "-C", str(ROOT), "archive", "--format=tar", "HEAD"],
            check=True,
            stdout=f,
        )
    with tarfile.open(archive) as tar:
        tar.extractall(dest)
    archive.unlink()


def make_tar(src: Path, out: Path) -> None:
    with tarfile.open(out, "w:gz") as tar:
        tar.add(src, arcname=src.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, help="e.g. v2.0.3")
    args = parser.parse_args()

    version = args.version
    if not version.startswith("v"):
        version = "v" + version
    notes = ROOT / f"RELEASE-NOTES-{version}.md"
    if not notes.exists():
        raise SystemExit(f"missing release notes: {notes}")

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    work = Path(tempfile.mkdtemp(prefix="wifidog-release-"))
    try:
        server = work / f"wifidog-auth-server-{version}"
        full = work / f"wifidog-portal-{version}"
        extract_head(server)
        extract_head(full)

        # server package excludes router binary packages
        shutil.rmtree(server / "router-packages", ignore_errors=True)

        make_tar(server, DIST / f"wifidog-portal-{version}-server.tar.gz")
        make_tar(full, DIST / f"wifidog-portal-{version}-full.tar.gz")

        for p in sorted((ROOT / "router-packages").iterdir()):
            if p.is_file() and p.suffix not in {".md", ".txt"}:
                shutil.copy2(p, DIST / p.name)

        shutil.copy2(notes, DIST / notes.name)

        sums = DIST / "SHA256SUMS.txt"
        lines = []
        for p in sorted(DIST.iterdir()):
            if p.is_file() and p.name != sums.name:
                lines.append(f"{sha256(p)}  {p.name}")
        sums.write_text("\n".join(lines) + "\n")

        for p in sorted(DIST.iterdir()):
            if p.is_file():
                print(f"{p.name}  {p.stat().st_size} bytes")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
