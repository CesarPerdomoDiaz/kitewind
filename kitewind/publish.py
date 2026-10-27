"""Publish the forecast to the public GitHub repository, for the GitHub Pages copy of the page.

The forecast goes to its own branch as a single commit that replaces the previous one, so the
repository does not grow. Pushing uses a deploy key (write access to this one repository only),
kept in the data folder as deploy_key. Without that file nothing is published.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger("kitewind")


def public_snapshot(snap: dict, include_windy: bool, site_name: str = "Test site") -> dict:
    """What the public page gets: a neutral site name and no coordinates, no request counts,
    and no Windy data unless it is allowed."""
    pub = copy.deepcopy(snap)
    pub.pop("budget", None)
    pub["site"] = {"name": site_name}
    pub["key_mode"] = "public"
    if not (include_windy and snap.get("windy_used")):
        pub["windy_used"] = False
        pub["models"] = [m for m in pub["models"] if m.get("source") != "Windy"]
        for h in pub["hours"]:
            if "models" in h:
                h["models"] = [m for m in h["models"] if "Testing key" not in m[0] and "(Windy)" not in m[0]]
    for m in pub["models"]:
        if m.get("error"):
            m["error"] = {"t": m["error"]["t"], "text": "fetch failed"}
    return pub


class Publisher:
    def __init__(self, data_dir: Path, settings: dict, demo: bool = False):
        self.dir = data_dir / "publish"
        self.key = data_dir / "deploy_key"
        self.known_hosts = data_dir / "known_hosts"
        self.repo = settings.get("repo", "")
        self.branch = settings.get("branch", "data")
        self.every_ms = int(settings.get("every_minutes", 10)) * 60_000
        self.include_windy = bool(settings.get("include_windy", False))
        self.site_name = settings.get("site_name", "Test site")
        self.author = settings.get("author", "Kite Wind")
        self.email = settings.get("email", "kitewind@example.invalid")
        enabled = settings.get("enabled", "auto")
        self.active = (not demo and bool(self.repo)
                       and (enabled is True or (enabled == "auto" and self.key.exists())))
        self.last_ms = 0
        self.last_ok: int | None = None

    def due(self, now_ms: int) -> bool:
        return self.active and now_ms - self.last_ms >= self.every_ms

    def _git(self, *args: str, env: dict) -> str:
        res = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(self.dir), *args],
                             capture_output=True, text=True, timeout=90, env=env)
        if res.returncode != 0:
            raise RuntimeError(f"git {args[0]}: {(res.stderr or res.stdout).strip()[:300]}")
        return res.stdout.strip()

    def publish(self, snap: dict, now_ms: int) -> bool:
        self.last_ms = now_ms
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "forecast.json").write_text(
                json.dumps(public_snapshot(snap, self.include_windy, self.site_name), separators=(",", ":")),
                encoding="utf-8")
            ssh = (f'ssh -i "{self.key}" -o IdentitiesOnly=yes -o BatchMode=yes '
                   f'-o UserKnownHostsFile="{self.known_hosts}" -o StrictHostKeyChecking=accept-new')
            env = {**os.environ, "GIT_SSH_COMMAND": ssh.replace("\\", "/"),
                   "GIT_AUTHOR_NAME": self.author, "GIT_AUTHOR_EMAIL": self.email,
                   "GIT_COMMITTER_NAME": self.author, "GIT_COMMITTER_EMAIL": self.email}
            if not (self.dir / ".git").exists():
                self._git("init", "-q", env=env)
            self._git("add", "forecast.json", env=env)
            tree = self._git("write-tree", env=env)
            stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now_ms / 1000))
            commit = self._git("commit-tree", tree, "-m", f"Forecast {stamp}", env=env)
            self._git("push", "-q", "-f", self.repo, f"{commit}:refs/heads/{self.branch}", env=env)
            self.last_ok = now_ms
            log.info("published forecast to %s (%s)", self.branch, stamp)
            return True
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
            log.warning("publish failed: %s", e)
            return False
