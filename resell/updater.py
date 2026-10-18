"""Update the toolkit from GitHub: git pull on the checked-out branch, reinstall if dependencies changed.

Your own files are never touched: data/ (sheet, database, config.local.yaml, fact sheets) and .env are
gitignored. Local edits to tracked files (e.g. config.yaml) are stashed before the pull and put back after.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The web server exits with this code after an update; windows/start.bat restarts it on the new code.
RESTART_EXIT_CODE = 3


@dataclass
class Status:
    ok: bool
    branch: str = ""
    behind: int = 0
    current: str = ""
    latest: str = ""
    incoming: list[str] = field(default_factory=list)   # one-line commit subjects, newest first
    error: str = ""


@dataclass
class Result:
    ok: bool
    updated: bool = False
    before: str = ""
    after: str = ""
    changes: list[str] = field(default_factory=list)
    reinstalled: bool = False
    log: list[str] = field(default_factory=list)
    error: str = ""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


def _repo_ok(root: Path) -> str:
    try:
        r = _git(root, "rev-parse", "--is-inside-work-tree")
    except FileNotFoundError:
        return "git is not installed - run the Windows installer again (it installs Git)"
    if r.returncode != 0 or r.stdout.strip() != "true":
        return f"{root} is not a git checkout - reinstall with the Windows installer so updates can work"
    return ""


def check(root: Path) -> Status:
    """Fetch and report how many commits the branch is behind its GitHub copy."""
    err = _repo_ok(root)
    if err:
        return Status(False, error=err)
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    f = _git(root, "fetch", "--quiet", "origin", branch)
    if f.returncode != 0:
        return Status(False, branch=branch, error="couldn't reach GitHub: " + (f.stderr.strip() or "no details"))
    behind = int(_git(root, "rev-list", "--count", f"HEAD..origin/{branch}").stdout.strip() or 0)
    log = _git(root, "log", "--format=%s", f"HEAD..origin/{branch}").stdout.splitlines()
    return Status(True, branch, behind, _git(root, "rev-parse", "--short", "HEAD").stdout.strip(),
                  _git(root, "rev-parse", "--short", f"origin/{branch}").stdout.strip(), log)


def update(root: Path, reinstall: bool | None = None) -> Result:
    """Pull the latest version. reinstall=None reinstalls only when pyproject.toml changed."""
    st = check(root)
    if not st.ok:
        return Result(False, error=st.error)
    before = st.current
    res = Result(True, before=before, after=before)
    if st.behind == 0:
        res.log.append(f"Already up to date ({st.branch} @ {before}).")
        return res
    p = _git(root, "pull", "--rebase", "--autostash", "origin", st.branch)
    res.log += [line for line in (p.stdout + p.stderr).splitlines() if line.strip()]
    if p.returncode != 0:
        _git(root, "rebase", "--abort")
        res.ok = False
        res.error = ("the update clashed with a change you made to a toolkit file. Nothing was changed. "
                     "Send this message to Claude: " + (p.stderr.strip().splitlines() or ["(no details)"])[-1])
        return res
    res.after = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    res.updated = res.after != before
    res.changes = st.incoming
    changed = _git(root, "diff", "--name-only", f"{before}..{res.after}").stdout.split()
    if reinstall or (reinstall is None and "pyproject.toml" in changed):
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "-e", ".[llm]"], cwd=root,
                           capture_output=True, text=True, timeout=900)
        res.reinstalled = r.returncode == 0
        if r.returncode != 0:
            res.ok = False
            res.error = "the new version downloaded but installing its packages failed: " + r.stderr.strip()[-400:]
    return res
