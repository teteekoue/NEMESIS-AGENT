"""Apply a unified diff patch to files anywhere on the system.

The applier is self-contained (pure Python) so it never depends on the
current directory of the process nor on GNU ``patch`` quirks:

- relative paths in the patch resolve from ``workdir`` (the persistent shell
  directory by default), and leading ``a/``/``b/`` components are stripped
  progressively until a file is found;
- absolute paths are honored as-is, including absolute paths that point
  outside ``workdir`` (GNU ``patch`` refuses those: "Ignoring potentially
  dangerous file name");
- creation (``--- /dev/null``) and deletion (``+++ /dev/null``) are supported;
- context matching falls back to whitespace-tolerant comparison so a patch
  produced from a slightly re-indented file still applies;
- every failure names the file, the hunk and the reason.

When the built-in engine cannot apply a section, GNU ``patch`` is used as a
fallback in the directory of the target file.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple


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
- Relative paths inside the patch resolve from `workdir` (the persistent shell directory by default); absolute paths are honored, including paths outside the working directory.
- Supports creating (--- /dev/null) and deleting (+++ /dev/null) files. Use dry_run to validate without writing."""


_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DEV_NULL = "/dev/null"


@dataclass
class _Hunk:
    old_start: int
    old_count: int
    lines: List[str]

    def old_block(self) -> List[str]:
        return [line[1:] if len(line) > 1 else "" for line in self.lines if line[:1] in (" ", "-")]

    def new_block(self) -> List[str]:
        return [line[1:] if len(line) > 1 else "" for line in self.lines if line[:1] in (" ", "+")]


@dataclass
class _FilePatch:
    old_path: Optional[str]
    new_path: Optional[str]
    hunks: List[_Hunk]


class _PatchError(Exception):
    pass


def _clean_patch_path(raw: str) -> Optional[str]:
    """Strip diff prefixes, quotes and diff timestamps from a header path."""
    value = (raw or "").strip()
    if not value:
        return None
    if "\t" in value:
        value = value.split("\t", 1)[0]
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        value = value[1:-1]
    if value == _DEV_NULL:
        return _DEV_NULL
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value or None


def _parse_patch(patch_text: str) -> List[_FilePatch]:
    lines = patch_text.splitlines()
    sections: List[_FilePatch] = []
    current: Optional[_FilePatch] = None
    hunk: Optional[_Hunk] = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("--- "):
            old_raw = line[4:]
            new_raw = ""
            if index + 1 < len(lines) and lines[index + 1].startswith("+++ "):
                new_raw = lines[index + 1][4:]
                index += 1
            current = _FilePatch(
                old_path=_clean_patch_path(old_raw),
                new_path=_clean_patch_path(new_raw),
                hunks=[],
            )
            sections.append(current)
            hunk = None
        elif line.startswith("@@ "):
            match = _HUNK_HEADER_RE.match(line)
            if not match:
                raise _PatchError(f"En-tête de hunk illisible: {line}")
            if current is None:
                raise _PatchError("Hunk trouvé avant tout en-tête --- / +++.")
            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) is not None else 1
            hunk = _Hunk(old_start=old_start, old_count=old_count, lines=[])
            current.hunks.append(hunk)
        elif hunk is not None and (line[:1] in (" ", "+", "-", "\\", "") or line.startswith("\\")):
            hunk.lines.append(line)
        index += 1

    if not sections:
        raise _PatchError("Aucun diff unifié détecté (les lignes --- / +++ / @@ sont absentes).")
    for section in sections:
        if not section.hunks and not (section.old_path == _DEV_NULL or section.new_path == _DEV_NULL):
            raise _PatchError(
                f"Aucun hunk pour {section.new_path or section.old_path or '?'}."
            )
    return sections


def _project_roots(start: str) -> List[str]:
    """Return the project roots found by walking up from ``start``."""
    roots: List[str] = []
    current = os.path.abspath(start)
    for _ in range(12):
        if os.path.isdir(os.path.join(current, ".git")) or os.path.isfile(
            os.path.join(current, "pyproject.toml")
        ):
            roots.append(current)
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return roots


def _resolution_bases(workdir: Optional[str], workspace_dir: Optional[str]) -> List[str]:
    """Ordered directories a relative patch path is resolved from.

    ``patch`` has no notion of a working directory: it is the tool that must
    find the file. Before failing, we try the explicit ``workdir``, the
    persistent shell directory, the project root above the shell directory and
    finally ``$HOME`` so ``a/src/app.py`` resolves even when the shell sits in
    another folder than the project.
    """
    from .bash import resolve_workdir

    bases: List[str] = []

    def _add(candidate: Optional[str]) -> None:
        if not candidate:
            return
        try:
            resolved = os.path.realpath(os.path.expanduser(os.path.expandvars(str(candidate))))
        except OSError:
            return
        if os.path.isdir(resolved) and resolved not in bases:
            bases.append(resolved)

    if workdir not in (None, ""):
        _add(str(workdir))
    shell_dir = str(resolve_workdir(None, workspace_dir))
    _add(shell_dir)
    # Un shell peut être garé loin du fichier visé (sous-dossier profond) : on
    # remonte les ancêtres du répertoire courant, du plus proche au plus haut.
    current = shell_dir
    for _ in range(6):
        parent = os.path.dirname(current)
        if parent == current:
            break
        _add(parent)
        current = parent
    for root in _project_roots(shell_dir):
        _add(root)
    _add(os.path.expanduser("~"))
    return bases


def _path_candidates(raw: str, workdir: str, must_exist: bool) -> List[str]:
    """Return candidate absolute paths for a path found in the patch."""
    expanded = os.path.expandvars(os.path.expanduser(raw.strip()))
    if os.path.isabs(expanded):
        return [os.path.normpath(expanded)]

    parts = [part for part in expanded.split("/") if part]
    candidates: List[str] = []
    for start in range(len(parts)):
        candidate = os.path.join(workdir, *parts[start:])
        if candidate not in candidates:
            candidates.append(os.path.normpath(candidate))
    if not candidates:
        candidates = [os.path.normpath(os.path.join(workdir, expanded))]
    if must_exist:
        for candidate in candidates:
            if os.path.exists(candidate):
                return [candidate] + [c for c in candidates if c != candidate]
    return candidates


def _read_lines(path: str) -> Tuple[List[str], bool]:
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        data = handle.read()
    trailing_newline = data.endswith("\n")
    if not data:
        return [], trailing_newline
    lines = data.split("\n")
    if trailing_newline:
        lines = lines[:-1]
    return [line.rstrip("\r") for line in lines], trailing_newline


def _write_lines(path: str, lines: List[str], trailing_newline: bool) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    text = "\n".join(lines) + ("\n" if trailing_newline and lines else "")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _normalize(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _locate(lines: List[str], block: List[str], expected: int) -> Optional[int]:
    """Find ``block`` in ``lines``; exact match first, then whitespace-tolerant."""
    if not block:
        return max(0, min(expected, len(lines)))
    span = len(block)
    limit = len(lines) - span

    def exact(start: int) -> bool:
        return lines[start:start + span] == block

    if 0 <= expected <= limit and exact(expected):
        return expected
    for start in range(0, max(0, limit) + 1):
        if exact(start):
            return start
    target = [_normalize(line) for line in block]
    for start in range(0, max(0, limit) + 1):
        if [_normalize(line) for line in lines[start:start + span]] == target:
            return start
    return None


def _apply_hunks(lines: List[str], hunks: List[_Hunk], label: str) -> List[str]:
    result = list(lines)
    offset = 0
    for number, hunk in enumerate(hunks, start=1):
        old_block = hunk.old_block()
        new_block = hunk.new_block()
        expected = max(0, hunk.old_start - 1 + offset)
        position = _locate(result, old_block, expected)
        if position is None:
            raise _PatchError(
                f"{label}: hunk #{number} (@@ -{hunk.old_start}) ne correspond à aucune ligne du fichier."
            )
        result[position:position + len(old_block)] = new_block
        drift = position - expected
        offset += drift + len(new_block) - len(old_block)
    return result


def _external_patch(patch_text: str, target: str, workdir: str, dry_run: bool) -> Tuple[bool, str]:
    """Fallback: run GNU patch in the directory of the target file."""
    directory = os.path.dirname(target) or workdir
    name = os.path.basename(target)
    section = [f"--- {name}", f"+++ {name}"]
    keep = False
    for line in patch_text.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        if line.startswith("@@ "):
            keep = True
        if keep:
            section.append(line)
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as handle:
            handle.write("\n".join(section) + "\n")
            patch_path = handle.name
    except OSError as exc:
        return False, f"impossible d'écrire le patch temporaire: {exc}"
    try:
        command = [
            "patch", "-p0", "--forward", "--no-backup-if-mismatch",
            "--reject-file=-", "-i", patch_path,
        ]
        if dry_run:
            command.insert(1, "--dry-run")
        proc = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=60)
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return proc.returncode == 0, output
    except FileNotFoundError:
        return False, "commande `patch` introuvable"
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        return False, str(exc)
    finally:
        try:
            os.unlink(patch_path)
        except OSError:
            pass


def apply_patch(
    patch: str,
    workspace_dir: Optional[str] = None,
    dry_run: bool = False,
    workdir: Optional[str] = None,
) -> ApplyPatchResult:
    """Apply ``patch`` with paths resolved from ``workdir`` (or ``workspace_dir``)."""
    from .bash import resolve_workdir

    if not patch or not str(patch).strip():
        return ApplyPatchResult(success=False, message="Le patch est vide.")

    patch_text = str(patch).replace("\r\n", "\n")
    if not patch_text.endswith("\n"):
        patch_text += "\n"

    requested = workdir if workdir not in (None, "") else workspace_dir
    if requested not in (None, "") and not os.path.isdir(str(resolve_workdir(requested))):
        return ApplyPatchResult(
            success=False,
            message=f"Répertoire de travail introuvable: {requested}",
        )
    bases = _resolution_bases(workdir, workspace_dir)
    if not bases:
        return ApplyPatchResult(success=False, message="Aucun répertoire de résolution disponible.")
    base = bases[0]

    try:
        sections = _parse_patch(patch_text)
    except _PatchError as exc:
        return ApplyPatchResult(success=False, message=str(exc))

    applied: List[str] = []
    failures: List[str] = []

    for section in sections:
        target_raw = section.new_path if section.new_path not in (None, _DEV_NULL) else section.old_path
        if not target_raw or target_raw == _DEV_NULL:
            failures.append("section sans chemin de fichier exploitable")
            continue

        creating = section.old_path == _DEV_NULL
        deleting = section.new_path == _DEV_NULL

        target = ""
        candidates: List[str] = []
        for candidate_base in bases:
            candidates = _path_candidates(target_raw, candidate_base, must_exist=not creating)
            if creating or any(os.path.exists(item) for item in candidates[:1]):
                target = candidates[0]
                break
            target = candidates[0]
        if not target:
            failures.append(f"{target_raw}: aucun chemin candidat")
            continue
        display = target

        try:
            if creating:
                lines, trailing = [], True
            elif not os.path.exists(target):
                raise _PatchError(
                    f"{display}: fichier introuvable (répertoires essayés : "
                    + ", ".join(bases) + ")."
                )
            elif os.path.isdir(target):
                raise _PatchError(f"{display}: c'est un répertoire, pas un fichier.")
            else:
                lines, trailing = _read_lines(target)

            updated = _apply_hunks(lines, section.hunks, display)
            if deleting:
                if not dry_run:
                    os.unlink(target)
            else:
                if not dry_run:
                    _write_lines(target, updated, trailing)
            applied.append(target)
        except _PatchError as exc:
            ok, output = _external_patch(patch_text, target, base, dry_run)
            if ok:
                applied.append(target)
            else:
                failures.append(f"{exc} (repli `patch`: {output or 'aucune sortie'})")
        except OSError as exc:
            failures.append(f"{display}: {exc}")

    prefix = "[dry-run] " if dry_run else ""
    if failures:
        parts = [f"{prefix}{len(failures)} échec(s):"] + [f"- {item}" for item in failures]
        if applied:
            parts.append("Appliqués: " + ", ".join(applied))
        return ApplyPatchResult(success=False, message="\n".join(parts), files_touched=applied)
    if applied:
        verb = "validé" if dry_run else "appliqué"
        return ApplyPatchResult(
            success=True,
            message=f"{prefix}Patch {verb}: " + ", ".join(applied),
            files_touched=applied,
        )
    return ApplyPatchResult(success=False, message=f"{prefix}Aucun fichier touché par le patch.")
