"""Skill library manager (Claude-compatible SKILL.md layout)."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

try:
    from rich.console import Console
    try:
        from src.ui.theme import NEMESIS_THEME
        console = Console(theme=NEMESIS_THEME)
    except Exception:
        console = Console()
except Exception:  # pragma: no cover
    class _Dummy:
        def print(self, *a, **k):
            pass
    console = _Dummy()


class SkillManager:
    """Install / list / remove skills under tools_library/."""

    def __init__(self, library_path: str = "tools_library"):
        self.library_path = Path(library_path)
        try:
            self.library_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # ── metadata ────────────────────────────────────────────────

    def _get_skill_metadata(self, skill_dir: str) -> Dict[str, Any]:
        skill_path = self.library_path / skill_dir
        skill_md = skill_path / "SKILL.md"
        metadata: Dict[str, Any] = {
            "name": skill_dir,
            "description": "Aucune description disponible.",
            "version": "unknown",
            "path": str(skill_path.resolve()) if skill_path.exists() else str(skill_path),
            "has_skill_md": skill_md.exists(),
        }
        if not skill_md.exists():
            return metadata
        try:
            content = skill_md.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL | re.MULTILINE)
            if match:
                data = yaml.safe_load(match.group(1))
                if isinstance(data, dict):
                    metadata["name"] = data.get("name") or metadata["name"]
                    metadata["description"] = data.get("description") or metadata["description"]
                    metadata["version"] = str(data.get("version") or metadata["version"])
            # Body preview (first non-empty paragraph after frontmatter)
            body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, count=1, flags=re.DOTALL)
            body = body.strip()
            if body:
                metadata["preview"] = body[:300].replace("\n", " ")
        except Exception:
            pass
        return metadata

    def list_installed(self) -> List[Dict[str, Any]]:
        skills: List[Dict[str, Any]] = []
        if not self.library_path.exists():
            return skills
        try:
            entries = sorted(self.library_path.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return skills
        for d in entries:
            if d.is_dir() and not d.name.startswith((".", "__")):
                skills.append(self._get_skill_metadata(d.name))
        return skills

    def get_skill(self, name: str) -> Optional[Dict[str, Any]]:
        """Return metadata + full SKILL.md body for a skill."""
        name = (name or "").strip()
        if not name or ".." in name or "/" in name or "\\" in name:
            return None
        path = self.library_path / name
        if not path.is_dir():
            # try match by metadata name
            for s in self.list_installed():
                if s.get("name") == name:
                    path = Path(s["path"])
                    break
            else:
                return None
        meta = self._get_skill_metadata(path.name)
        skill_md = path / "SKILL.md"
        if skill_md.exists():
            try:
                meta["content"] = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                meta["content_error"] = str(e)
        return meta

    # ── install ─────────────────────────────────────────────────

    def install_from_local(self, source_path: str) -> Tuple[bool, str]:
        source = Path(source_path).expanduser().resolve()
        if not source.exists():
            return False, f"Chemin introuvable: {source_path}"
        if not source.is_dir():
            return False, f"Pas un dossier: {source_path}"
        dest = self.library_path / source.name
        try:
            self.library_path.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(source, dest)
            # Warn if no SKILL.md
            if not (dest / "SKILL.md").exists():
                return True, (
                    f"Skill '{source.name}' installé (attention: pas de SKILL.md — "
                    "ajoutez-en un pour une meilleure intégration)."
                )
            return True, f"Skill '{source.name}' installé avec succès."
        except Exception as e:
            return False, str(e)

    def install_from_url(self, url: str) -> Tuple[bool, str]:
        url = (url or "").strip()
        if not url:
            return False, "URL vide"
        if url.endswith(".zip") or ".zip?" in url:
            return self._install_via_zip(url)
        if url.endswith(".git") or "github.com" in url or "gitlab.com" in url:
            return self._install_via_git(url)
        return False, "Format d'URL non reconnu (utilisez .git, GitHub, ou .zip)."

    def _install_via_git(self, url: str) -> Tuple[bool, str]:
        repo_name = url.rstrip("/").split("/")[-1].replace(".git", "")
        if not repo_name or repo_name in (".", ".."):
            return False, "Nom de dépôt invalide"
        dest = self.library_path / repo_name
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "echo"
        try:
            self.library_path.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            console.print(f"[dim]Clonage de {url}...[/dim]")
            proc = subprocess.run(
                ["git", "clone", "--depth", "1", url, str(dest)],
                check=False,
                capture_output=True,
                env=env,
                timeout=120,
            )
            if proc.returncode != 0:
                err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                return False, f"Erreur Git: {err or proc.returncode}"
            return True, f"Skill '{repo_name}' installé avec succès via Git."
        except subprocess.TimeoutExpired:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            return False, "Timeout lors du clonage Git (120s)"
        except FileNotFoundError:
            return False, "git n'est pas installé sur ce système"
        except Exception as e:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            return False, str(e)

    def _safe_extract_zip(self, zf: zipfile.ZipFile, dest_root: Path) -> List[str]:
        """Extract zip safely (no path traversal). Returns top-level names."""
        top_levels = set()
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            # Block absolute paths and ..
            parts = Path(name).parts
            if name.startswith("/") or any(p == ".." for p in parts):
                continue
            target = (dest_root / name).resolve()
            if not str(target).startswith(str(dest_root.resolve())):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            top_levels.add(parts[0])
        return sorted(top_levels)

    def _install_via_zip(self, url: str) -> Tuple[bool, str]:
        if requests is None:
            return False, "Le module 'requests' est requis pour installer depuis une URL ZIP"
        try:
            console.print(f"[dim]Téléchargement du ZIP depuis {url}...[/dim]")
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            self.library_path.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                tops = self._safe_extract_zip(z, self.library_path)
            if not tops:
                return False, "ZIP vide ou chemins non sûrs"
            return True, f"Skill installé via ZIP ({', '.join(tops)})."
        except Exception as e:
            return False, f"Erreur ZIP: {e}"

    def uninstall(self, name: str) -> Tuple[bool, str]:
        name = (name or "").strip()
        if not name or ".." in name or "/" in name or "\\" in name:
            return False, "Nom de skill invalide"
        path = self.library_path / name
        if not path.is_dir():
            return False, f"Skill '{name}' introuvable"
        try:
            shutil.rmtree(path)
            return True, f"Skill '{name}' supprimé."
        except Exception as e:
            return False, str(e)
