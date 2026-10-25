"""Apply a unified diff patch to files in the workspace."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile

from ..paths import system_base_dir
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class ApplyPatchResult:
    success: bool
    message: str
    files_touched: List[str] = None

    def __post_init__(self):
        if self.files_touched is None:
            self.files_touched = []


DESCRIPTION_FULL = """Apply a unified diff (patch) anywhere on the system.
- Prefer this for multi-hunk or multi-file edits in one step.
- The patch must be valid unified diff format (--- / +++ / @@).
- Relative paths inside the patch resolve from the current directory; absolute paths are honored."""


def apply_patch(patch: str, workspace_dir: str, dry_run: bool = False) -> ApplyPatchResult:
    if not patch or not patch.strip():
        return ApplyPatchResult(success=False, message="patch content is empty")

    # Normalize line endings
    patch_text = patch.replace("\r\n", "\n")
    if not patch_text.endswith("\n"):
        patch_text += "\n"

    # Detect files mentioned in the patch
    files = re.findall(r"^\+\+\+\s+(?:b/)?(.+)$", patch_text, re.M)
    files = [f.strip() for f in files if f.strip() and f.strip() != "/dev/null"]

    try:
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as tf:
            tf.write(patch_text)
            patch_path = tf.name
    except OSError as e:
        return ApplyPatchResult(success=False, message=f"Cannot write temp patch: {e}")

    base_dir = str(system_base_dir())
    try:
        cmd = ["patch", "-p1", "--forward", "--no-backup-if-mismatch", "-i", patch_path]
        if dry_run:
            cmd.insert(1, "--dry-run")
        proc = subprocess.run(
            cmd,
            cwd=base_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            # Fallback: try -p0
            cmd0 = ["patch", "-p0", "--forward", "--no-backup-if-mismatch", "-i", patch_path]
            if dry_run:
                cmd0.insert(1, "--dry-run")
            proc0 = subprocess.run(cmd0, cwd=base_dir, capture_output=True, text=True, timeout=60)
            out0 = (proc0.stdout or "") + (proc0.stderr or "")
            if proc0.returncode != 0:
                return ApplyPatchResult(
                    success=False,
                    message=f"patch failed:\n{out}\n--- retry -p0 ---\n{out0}",
                    files_touched=files,
                )
            out = out0
        prefix = "[dry-run] " if dry_run else ""
        return ApplyPatchResult(
            success=True,
            message=prefix + (out.strip() or "Patch applied successfully."),
            files_touched=files,
        )
    except FileNotFoundError:
        # Pure-Python fallback for simple single-file hunks is complex; report clearly
        return ApplyPatchResult(
            success=False,
            message="`patch` command not found on PATH. Install patchutils/gnu patch.",
            files_touched=files,
        )
    except Exception as e:
        return ApplyPatchResult(success=False, message=str(e), files_touched=files)
    finally:
        try:
            os.unlink(patch_path)
        except OSError:
            pass
