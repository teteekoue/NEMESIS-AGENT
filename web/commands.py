"""Slash commands for the web UI (terminal style, Claude Code parity).

Registered on a dedicated registry so importing this module never pollutes the
terminal CLI's global command registry. Every command returns plain text that
the browser renders as terminal output (`$ cmd` + body), no rich capture and no
interactive prompts.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional


class WebCommand:
    def __init__(self, name: str, description: str, usage: str, handler: Callable):
        self.name = name
        self.description = description
        self.usage = usage
        self.handler = handler


class WebRegistry:
    def __init__(self):
        self._commands: Dict[str, WebCommand] = {}

    def register(self, name: str, description: str, usage: str = ""):
        def deco(fn: Callable):
            self._commands[name] = WebCommand(name, description, usage or f"/{name}", fn)
            return fn
        return deco

    def get(self, name: str) -> Optional[WebCommand]:
        return self._commands.get(name)

    def list(self) -> List[WebCommand]:
        return sorted(self._commands.values(), key=lambda c: c.name)


registry = WebRegistry()


# ---------------------------------------------------------------- helpers
def _sessions(ctx) -> List[Dict[str, Any]]:
    return ctx.server.list_sessions()


def _fmt_time(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts)) if ts else "—"


def _truncate(s: str, n: int = 48) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _table(rows: List[List[str]], headers: List[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))
    def line(cells): return "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells)).rstrip()
    out = [line(headers)]
    out.append("  ".join("-" * w for w in widths))
    for row in rows:
        out.append(line(row))
    return "\n".join(out)


def _choice(title: str, options: List[Dict[str, Any]], *, subtitle: str = "",
            prompt: str = "Choisis une option", filterable: bool = False) -> Dict[str, Any]:
    """Build a structured, clickable menu the web UI renders as selectable rows.

    Each option may carry a ``command`` run when clicked and optional ``actions``
    (secondary buttons, e.g. delete). This is the web equivalent of an
    interactive prompt, but validated by a click instead of stdin.
    """
    return {
        "type": "choice",
        "title": title,
        "subtitle": subtitle,
        "prompt": prompt,
        "filterable": filterable,
        "options": options,
    }



# ---------------------------------------------------------------- sessions
@registry.register("resume", "Reprendre ou lister les sessions", "/resume [id|nom] [-d id] [-rm id]")
def resume_command(args: List[str], ctx) -> str:
    args = list(args or [])
    flags = {"-d", "--delete", "-rm", "--remove"}
    if args and args[0].lower() in flags:
        target = args[1] if len(args) > 1 else ""
        if not target:
            return "usage : /resume -d <id>"
        items = _sessions(ctx)
        match = next((s for s in items if s["id"] == target or s["title"] == target), None)
        if not match:
            return f"session introuvable : {target}"
        ctx.server.delete_session(match["id"])
        return f"session supprimée : {_truncate(match['title'], 30)} ({match['id']})"

    items = _sessions(ctx)
    if not items:
        return "aucune session enregistrée."

    if args:
        q = " ".join(args).strip().lower()
        match = next((s for s in items if s["id"] == q or q in (s.get("title") or "").lower()), None)
        if not match:
            return f"aucune session correspondant à « {q} ». Voir /resume sans argument."
        sid = ctx.select_session(match["id"])
        return f"→ session reprise : {_truncate(match['title'], 40)} ({sid}) — le contexte NemApi est conservé côté serveur."

    options = []
    for s in items:
        active = s["id"] == ctx.session.id
        options.append({
            "label": _truncate(s.get("title") or "(sans titre)", 40),
            "value": s["id"],
            "hint": f"{s.get('turn_count', 0)} msg · {_fmt_time(s.get('updated_at', 0))} · {s['id']}",
            "current": active,
            "command": f"/resume {s['id']}",
            "actions": [{"label": "supprimer", "command": f"/resume -d {s['id']}", "danger": True}],
        })
    return _choice(
        "Sessions",
        options,
        subtitle="Sélectionne une session à reprendre (le contexte NemApi est conservé).",
        prompt="Reprendre une session",
    )


@registry.register("new", "Démarrer une nouvelle session", "/new")
def new_command(args, ctx) -> str:
    workspace = ctx.session.workspace
    sess = ctx.server.create_session(workspace)
    ctx.broadcast_select(sess.id)
    return f"→ nouvelle session : {sess.id} (contexte NemApi neuf)."


# ---------------------------------------------------------------- models
def _apply_model(ctx, target: str) -> str:
    sess = ctx.session
    prov = sess.provider
    if prov is None:
        return "aucun provider actif."
    known = set()
    try:
        known = {m["id"] for m in (prov.list_models() or []) if isinstance(m, dict) and m.get("id")}
    except Exception:
        known = set()
    note = ""
    if known and target not in known:
        note = " (absent de la liste serveur, appliqué quand même)"
    try:
        prov.model = target
    except Exception:
        pass
    sess.server.config.setdefault("provider", {})["model"] = target
    try:
        sess.shim._save_config()
    except Exception:
        pass
    return f"modèle changé : {target}{note}\n(note : la conversation actuelle est conservée)"


@registry.register("models", "Lister et choisir le modèle NEMAPI", "/models [filtre|id]")
@registry.register("model", "Alias de /models", "/model [filtre|id]")
def models_command(args, ctx) -> Any:
    sess = ctx.session
    prov = sess.provider
    if prov is None:
        return "aucun provider actif."
    args = list(args or [])
    # `/models <id>` selects directly (non-interactive path).
    if args and not args[0].startswith("-"):
        return _apply_model(ctx, args[0].strip())

    current = getattr(prov, "model", "") or ""
    try:
        models = prov.list_models() or []
    except Exception as e:
        return f"impossible de récupérer les modèles : {e}"

    ids = [str(m.get("id")) for m in models if isinstance(m, dict) and m.get("id")]
    if not ids:
        return "NEMAPI n'a retourné aucun modèle. Précise un id : /models <id>"

    sorted_ids = sorted(set(ids))
    options = []
    for mid in sorted_ids:
        owner = ""
        for m in models:
            if isinstance(m, dict) and m.get("id") == mid:
                owner = m.get("owned_by") or ""
                break
        options.append({
            "label": mid,
            "value": mid,
            "hint": owner,
            "current": mid == current,
            "command": f"/models {mid}",
        })
    return _choice(
        "Modèles NEMAPI",
        options,
        subtitle=f"Modèle actuel : {current or '—'}",
        prompt="Choisis un modèle" + (" (filtre ci-dessous)" if len(options) > 8 else ""),
        filterable=len(options) > 8,
    )




# ---------------------------------------------------------------- status/usage
@registry.register("usage", "Statistiques de la session courante", "/usage")
def usage_command(args, ctx) -> str:
    sess = ctx.session
    turns = len([m for m in sess.messages if m.get("role") == "user"])
    tools = len([m for m in sess.messages if m.get("role") == "tool_call"])
    uptime = int(time.time() - sess.created_at)
    mm, ss = divmod(uptime, 60)
    model = getattr(sess.provider, "model", "?") if sess.provider else "?"
    return (
        f"provider   : nemapi\n"
        f"modèle     : {model}\n"
        f"workspace  : {sess.workspace}\n"
        f"session    : {sess.id}\n"
        f"durée      : {mm:02d}:{ss:02d}\n"
        f"tours      : {turns}\n"
        f"appels outil : {tools}"
    )


@registry.register("context", "Aperçu du contexte (géré côté NemApi)", "/context")
def context_command(args, ctx) -> str:
    sess = ctx.session
    by_role: Dict[str, int] = {}
    size = 0
    for m in sess.messages:
        r = m.get("role", "?")
        by_role[r] = by_role.get(r, 0) + 1
        size += len(str(m.get("content", "")))
    lines = ["Le contexte vit chez NemApi (fresh_chat au démarrage de session).",
             "L'UI n'affiche que le transcript local :", ""]
    for r in ("user", "assistant", "tool_call", "tool_result", "system"):
        if by_role.get(r):
            lines.append(f"  {r:<12} {by_role[r]}")
    lines.append(f"\nvolumétrie affichée ≈ {size // 1024} Ko")
    return "\n".join(lines)


# ---------------------------------------------------------------- permissions
@registry.register("permissions", "Règles d'autorisation des outils", "/permissions [list|allow|deny|reset]")
def permissions_command(args, ctx) -> Any:
    args = list(args or [])
    sess = ctx.session
    exec_risk = sess.executor.registry.risk_for if sess.executor else None

    def risk_of(name):
        try:
            return exec_risk(name) if exec_risk else "?"
        except Exception:
            return "?"

    # No argument -> a selectable menu of tools with allow/deny actions.
    if not args:
        names = sorted(n for n, _ in (sess.executor.registry if sess.executor else []))
        options = []
        for n in names:
            risk = risk_of(n)
            mode = "auto" if risk == "read" else "always" if n in sess._authorized_tools else "ask"
            opts = []
            if risk != "read":
                if n in sess._authorized_tools:
                    opts.append({"label": "redemander", "command": f"/permissions deny {n}"})
                else:
                    opts.append({"label": "toujours autoriser", "command": f"/permissions allow {n}"})
            options.append({
                "label": n,
                "value": n,
                "hint": f"risque {risk} · mode {mode}",
                "actions": opts,
                "command": f"/permissions allow {n}" if (risk != "read" and n not in sess._authorized_tools) else "",
            })
        options.append({"label": "réinitialiser toutes les autorisations", "value": "__reset__",
                        "hint": "revient à l'état par défaut", "command": "/permissions reset"})
        return _choice(
            "Autorisations des outils",
            options,
            subtitle="lecture = auto · haut risque = signature exacte mémorisée",
            prompt="Clique sur un outil pour changer son mode",
            filterable=len(options) > 10,
        )

    sub = (args[0].lower() if args else "list")
    if sub == "list":
        rows = []
        names = sorted(n for n, _ in (sess.executor.registry if sess.executor else []))
        for n in names:
            state = "auto" if risk_of(n) == "read" else "always" if n in sess._authorized_tools else "ask"
            rows.append([n, risk_of(n), state])
        return _table(rows, ["outil", "risque", "mode"]) + "\nlecture = auto · signature haut-risque mémorisée par exactitude"
    if sub in ("allow", "deny", "reset"):
        if sub == "reset":
            sess._authorized_tools.clear()
            sess._authorized_signatures.clear()
            return "autorisations de session réinitialisées."
        if len(args) < 2:
            return f"usage : /permissions {sub} <outil>"
        tool = args[1]
        if sess.executor is not None and tool not in sess.executor.registry:
            return f"outil inconnu : {tool}"
        if sub == "allow":
            sess._authorized_tools.add(tool)
            return f"'{tool}' autorisé pour toute la session (sauf haut risque : signature exacte requise)."
        sess._authorized_tools.discard(tool)
        sess._authorized_signatures = {s for s in sess._authorized_signatures if not s.startswith(tool + ":")}
        return f"'{tool}' repassé en demande d'autorisation."
    return "usage : /permissions [list|allow <outil>|deny <outil>|reset]"


# ---------------------------------------------------------------- memory
@registry.register("memory", "Mémoire longue durée du workspace", "/memory [add <texte>|set <texte>|search <mot>|list|clear|del <id>]")
def memory_command(args, ctx) -> str:
    from src.core.tools import memory as mem

    args = list(args or [])
    ws = ctx.session.workspace
    sub = (args[0].lower() if args else "list")
    rest = " ".join(args[1:]) if len(args) > 1 else ""
    if sub in ("add", "set") and rest:
        res = mem.memory(action="set", workspace_dir=ws, content=rest)
        return res.get("stdout") or "mémoire mise à jour."
    if sub == "search" and rest:
        res = mem.memory(action="search", workspace_dir=ws, query=rest)
        return res.get("stdout") or "(rien)"
    if sub == "clear":
        res = mem.memory(action="clear", workspace_dir=ws)
        return res.get("stdout") or "mémoire effacée."
    if sub == "del" and rest:
        res = mem.memory(action="delete", workspace_dir=ws, id=rest)
        return res.get("stdout") or "(supprimé)"
    res = mem.memory(action="list", workspace_dir=ws)
    return res.get("stdout") or "(mémoire vide)"


# ---------------------------------------------------------------- plan
@registry.register("plan", "Planifier avant d'éditer (comme Claude Code)", "/plan <description>")
def plan_command(args, ctx) -> str:
    desc = " ".join(args or []).strip()
    if not desc:
        return "usage : /plan <description>. Mode planification activé : l'agent lit et propose, il n'édite pas."
    ctx.enter_plan_mode()
    ctx.run_turn(
        "[MODE PLANIFICATION] Analyse la demande suivante et produis un plan d'action détaillé "
        "(fichiers concernés, étapes, vérifications). Ne modifie aucun fichier.\n\n" + desc
    )
    return "→ mode planification actif. 'exit' sort du plan ; l'agent répond ci-dessus."


@registry.register("exit", "Sortir du mode planification", "/exit")
def exit_command(args, ctx) -> str:
    if not ctx.leave_plan_mode():
        return "(pas de mode plan actif)"
    return "← retour au mode normal."


# ---------------------------------------------------------------- export
@registry.register("export", "Exporter le transcript", "/export [chemin]")
def export_command(args, ctx) -> str:
    sess = ctx.session
    parts = []
    for m in sess.messages:
        role = m.get("role", "?")
        if role == "user":
            parts.append(f"> {m.get('content','')}\n")
        elif role == "assistant":
            parts.append(f"NEMESIS: {m.get('content','')}\n")
        elif role == "tool_call":
            parts.append(f"[outil {m.get('tool')} {_truncate(str(m.get('params')), 60)}]\n")
        elif role == "tool_result":
            ok = "ok" if m.get("success") else "échec"
            parts.append(f"[→ {ok}] {_truncate(str(m.get('truncated_preview','')), 120)}\n")
    text = "\n".join(parts)
    path = (args or [None])[0]
    if path:
        from pathlib import Path
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = sess.executor.workspace_root / p if sess.executor else Path.cwd() / p
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            return f"transcript exporté → {p}"
        except Exception as e:
            return f"écriture impossible : {e}"
    return text or "(transcript vide)"


# ---------------------------------------------------------------- misc
@registry.register("release-notes", "Nouveautés NEMESIS", "/release-notes")
def release_notes(args, ctx) -> str:
    return (
        "Notes :\n"
        "· UI web terminal (SSE, autorisations y/n/a, sorties masquables)\n"
        "· mémoire longue durée + skills injectés dans le prompt système\n"
        "· multi-provider (openai, ollama, xai, openrouter…) si installé\n"
        "· A2A : délégation non bloquante avec timeout honoré\n"
        "· batch parallèle des appels lecture seule\n"
        "· /config, /model, /tools, /history disponibles via HTTP"
    )


@registry.register("doctor", "Vérifier l'installation", "/doctor")
def doctor_command(args, ctx) -> str:
    sess = ctx.session
    lines = []
    ok = True
    prov = "ok" if sess.provider else "absent"
    conn = "-"
    if sess.provider:
        try:
            conn = "ok" if sess.provider.test_connection() else "hors ligne"
        except Exception as e:
            conn = f"erreur ({e.__class__.__name__})"
    lines.append(f"provider nemapi : {prov} · connexion : {conn}")
    lines.append(f"executor outils : {len(list(sess.executor.registry)) if sess.executor else 0}")
    ws = sess.workspace
    lines.append(f"workspace       : {ws}")
    try:
        writable = __import__("os").access(ws, __import__("os").W_OK)
        lines.append(f"workspace écriture : {'ok' if writable else 'BLOQUÉE'}")
        ok = ok and writable
    except Exception as e:
        lines.append(f"workspace accès    : erreur {e}")
    lines.append(f"config dir      : {ctx.config_dir}")
    return "\n".join(lines) + ("\n✔ tout est en ordre" if ok else "\n✗ voir les alertes ci-dessus")
