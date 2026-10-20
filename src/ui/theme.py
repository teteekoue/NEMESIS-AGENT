"""NEMESIS UI Theme — Catppuccin Mocha inspired.

Palette cohérente pour une interface moderne et professionnelle.
Utilisation de tokens sémantiques pour faciliter la maintenance.
"""

from rich.theme import Theme
from rich.style import Style

# Largeur de contenu : cape pour rester lisible sur grand ecran.
MAX_CONTENT_WIDTH = 110
MIN_CONTENT_WIDTH = 40

# Lignes affichees pour une sortie d'outil avant troncature.
TOOL_OUTPUT_PREVIEW_LINES = 8

# ── Catppuccin Mocha Palette ──────────────────────────
class Catppuccin:
    BASE      = "#1e1e2e"
    MANTLE    = "#181825"
    CRUST     = "#11111b"
    SURFACE0  = "#313244"
    SURFACE1  = "#45475a"
    SURFACE2  = "#585b70"
    OVERLAY0  = "#6c7086"
    OVERLAY1  = "#7f849c"
    SUBTEXT0  = "#a6adc8"
    SUBTEXT1  = "#bac2de"
    TEXT      = "#cdd6f4"
    LAVENDER  = "#b4befe"
    BLUE      = "#89b4fa"
    SAPPHIRE  = "#74c7ec"
    SKY       = "#89dceb"
    TEAL      = "#94e2d5"
    GREEN     = "#a6e3a1"
    YELLOW    = "#f9e2af"
    PEACH     = "#fab387"
    MAROON    = "#eba0ac"
    RED       = "#f38ba8"
    MAUVE     = "#cba6f7"
    PINK      = "#f5c2e7"
    ROSEWATER = "#f5e0dc"


NEMESIS_THEME = Theme({
    # ── Core semantic tokens ──
    "user.message":         f"bold {Catppuccin.GREEN}",
    "ai.message":           f"bold {Catppuccin.BLUE}",
    "system.info":          f"italic {Catppuccin.SUBTEXT0}",
    "system.success":       f"bold {Catppuccin.GREEN}",
    "system.error":         f"bold {Catppuccin.RED}",
    "system.warning":       f"bold {Catppuccin.YELLOW}",
    "system.dim":           f"{Catppuccin.OVERLAY0}",
    "system.debug":         f"dim {Catppuccin.YELLOW}",

    # ── Tool execution ──
    "tool.name":            f"bold {Catppuccin.MAUVE}",
    "tool.param":           f"{Catppuccin.SUBTEXT0}",
    "tool.value":           f"{Catppuccin.TEXT}",
    "tool.output":          f"{Catppuccin.SURFACE1}",
    "tool.output.text":     f"{Catppuccin.TEXT}",
    "tool.output.success":  f"{Catppuccin.GREEN}",
    "tool.output.error":    f"{Catppuccin.RED}",

    # ── Border styles ──
    "border.user":          f"{Catppuccin.GREEN}",
    "border.ai":            f"{Catppuccin.BLUE}",
    "border.tool":          f"{Catppuccin.MAUVE}",
    "border.system":        f"{Catppuccin.SURFACE1}",
    "border.success":       f"{Catppuccin.GREEN}",
    "border.error":         f"{Catppuccin.RED}",
    "border.dim":           f"{Catppuccin.SURFACE1}",

    # ── Header ──
    "header.title":         f"bold {Catppuccin.MAUVE}",
    "header.subtitle":      f"italic {Catppuccin.SUBTEXT0}",
    "header.version":       f"{Catppuccin.OVERLAY0}",
    "header.accent":        f"{Catppuccin.BLUE}",

    # ── Input prompt ──
    "input.prompt":         f"bold {Catppuccin.BLUE}",
    "input.text":           f"{Catppuccin.TEXT}",
    "input.placeholder":    f"{Catppuccin.OVERLAY0}",

    # ── Tool card (v2) ──
    "tool.name":            f"bold {Catppuccin.PEACH}",
    "tool.params":          f"{Catppuccin.SUBTEXT0}",
    "tool.gutter":          f"{Catppuccin.SURFACE1}",
    "tool.body":            f"{Catppuccin.TEXT}",
    "tool.dim":             f"{Catppuccin.OVERLAY0}",
    "tool.ok":              f"bold {Catppuccin.GREEN}",
    "tool.fail":            f"bold {Catppuccin.RED}",

    # ── Messages (v2) ──
    "user.label":           f"bold {Catppuccin.GREEN}",
    "user.gutter":          f"{Catppuccin.GREEN}",
    "ai.label":             f"bold {Catppuccin.BLUE}",
    "ai.gutter":            f"{Catppuccin.BLUE}",

    # ── Header (v2) ──
    "header.brand":         f"bold {Catppuccin.LAVENDER}",
    "header.meta":          f"{Catppuccin.OVERLAY0}",
    "header.sep":           f"{Catppuccin.SURFACE1}",
    "rule":                 f"{Catppuccin.SURFACE0}",
    "prompt":               f"bold {Catppuccin.BLUE}",

    # ── Status indicators ──
    "status.idle":          f"{Catppuccin.SURFACE1}",
    "status.busy":          f"{Catppuccin.YELLOW}",
    "status.done":          f"{Catppuccin.GREEN}",
    "status.fail":          f"{Catppuccin.RED}",
})


class Colors:
    """Sémantique UIColors — points d'accès programmatiques."""
    BASE      = Catppuccin.BASE
    SURFACE0  = Catppuccin.SURFACE0
    SURFACE1  = Catppuccin.SURFACE1
    TEXT      = Catppuccin.TEXT
    SUBTEXT0  = Catppuccin.SUBTEXT0
    SUBTEXT1  = Catppuccin.SUBTEXT1
    BLUE      = Catppuccin.BLUE
    GREEN     = Catppuccin.GREEN
    RED       = Catppuccin.RED
    YELLOW    = Catppuccin.YELLOW
    MAUVE     = Catppuccin.MAUVE
    TEAL      = Catppuccin.TEAL
    PEACH     = Catppuccin.PEACH
    LAVENDER  = Catppuccin.LAVENDER
    OVERLAY0  = Catppuccin.OVERLAY0
    OVERLAY1  = Catppuccin.OVERLAY1

    # ── Shortcuts ──
    PRIMARY   = Catppuccin.BLUE
    SUCCESS   = Catppuccin.GREEN
    ERROR     = Catppuccin.RED
    WARNING   = Catppuccin.YELLOW
    ACCENT    = Catppuccin.MAUVE
    DIM       = Catppuccin.OVERLAY0
    USER      = Catppuccin.GREEN
    AI        = Catppuccin.BLUE
    TOOL      = Catppuccin.MAUVE
