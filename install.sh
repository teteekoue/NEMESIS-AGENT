#!/bin/bash
set -e
echo "=== Installation de NEMESIS CLI (dev local) ==="

if ! command -v python3 &>/dev/null; then
    echo "ERREUR: python3 n'est pas installé."
    exit 1
fi

if ! command -v tree &>/dev/null; then
    echo "AVERTISSEMENT: 'tree' non installé (utile pour list_dir). sudo apt install tree"
fi

echo "Création de l'environnement virtuel..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# Install the Web backend deps in the venv too, so ./web/start.sh (which
# prefers ./venv/bin/python3) never fails with "No module named 'uvicorn'".
echo "Installation des dépendances backend Web (venv)..."
./venv/bin/pip install -r web/requirements.txt

# --- Interface Web (optionnelle) ---
INSTALL_WEB="${NEMESIS_INSTALL_WEB:-ask}"
if [ "$INSTALL_WEB" = "ask" ]; then
    read -r -p "Installer l'interface Web (FastAPI + React) ? [o/N] " REPLY
    case "$REPLY" in
        [oOyY]*) INSTALL_WEB="yes" ;;
        *) INSTALL_WEB="no" ;;
    esac
fi

if [ "$INSTALL_WEB" = "yes" ]; then
    echo ""
    if command -v node &>/dev/null && command -v npm &>/dev/null; then
        echo "Installation des dépendances frontend (npm)..."
        (cd web/frontend && npm ci --no-audit --no-fund)
    else
        echo "AVERTISSEMENT: Node.js/npm introuvable — le frontend Web ne pourra pas être lancé."
        echo "             Installez Node.js 18+ puis: cd web/frontend && npm install"
    fi
fi

echo ""
echo "=== Installation terminée ==="
echo "Lancer (CLI):    ./venv/bin/python3 agent.py"
echo "Lancer (Web):    ./web/start.sh        (dev, backend + frontend)"
echo "Config:          ~/.config/nemesis-cli/  (créée au premier lancement)"
echo "Workspace défaut: ~/nemesis-workspace"
echo ""
echo "Intégration GitHub: onglet GitHub des réglages Web (nécessite Node.js/npx)."
