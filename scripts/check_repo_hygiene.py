#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Read-only delivery hygiene checks for the RAG repository."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TRACKED = ("data/domain_catalog.json",)


def parse_status_path(line: str) -> str | None:
    line = line.rstrip("\n")
    if not line or len(line) < 4:
        return None
    payload = line[3:]
    if " -> " in payload:
        payload = payload.split(" -> ", 1)[1]
    return payload.strip() or None


def is_forbidden_path(path: str) -> bool:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if name.upper() == "NUL":
        return True
    if name.endswith(".tmp"):
        return True
    if "_debug" in name:
        return True
    return False


def find_forbidden_paths(lines: list[str]) -> list[str]:
    found: list[str] = []
    for line in lines:
        path = parse_status_path(line)
        if path and is_forbidden_path(path) and path not in found:
            found.append(path)
    return found


def run_git(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def collect_hygiene_errors(repo_root: Path = ROOT) -> list[str]:
    errors: list[str] = []

    diff_check = run_git("diff", "--check", cwd=repo_root)
    if diff_check.returncode != 0:
        errors.append("git diff --check failed")
        for chunk in (diff_check.stdout, diff_check.stderr):
            text = (chunk or "").strip()
            if text:
                errors.append(text)

    status = run_git("status", "--porcelain", "--untracked-files=all", cwd=repo_root)
    if status.returncode != 0:
        errors.append("git status failed")
        if status.stderr.strip():
            errors.append(status.stderr.strip())
        return errors

    lines = [line for line in status.stdout.splitlines() if line.strip()]
    if lines:
        errors.append("working tree is not clean:")
        errors.extend(f"  {line}" for line in lines)

    forbidden = find_forbidden_paths(lines)
    if forbidden:
        errors.append("forbidden paths present:")
        errors.extend(f"  {path}" for path in forbidden)

    for rel in REQUIRED_TRACKED:
        tracked = run_git("ls-files", "--error-unmatch", rel, cwd=repo_root)
        if tracked.returncode != 0:
            errors.append(f"required tracked file missing from git index: {rel}")

    return errors


def main() -> int:
    errors = collect_hygiene_errors()
    if errors:
        print("repo hygiene check FAILED", file=sys.stderr)
        for message in errors:
            print(message, file=sys.stderr)
        return 1

    print("repo hygiene check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
