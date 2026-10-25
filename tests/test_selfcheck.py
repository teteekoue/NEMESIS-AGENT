"""Auto-vérification : les correctifs doivent être présents ET chargés."""

import os

import pytest

from src.core.paths import session_cwd, set_session_cwd
from src.core.selfcheck import code_origin, module_report, run_selfcheck


@pytest.fixture(autouse=True)
def _restore_cwd():
    original = session_cwd()
    yield
    set_session_cwd(original)


def test_every_check_passes_on_the_working_copy():
    results = run_selfcheck()
    assert results, "aucune vérification exécutée"
    failures = [f"{r.name}: {r.detail}" for r in results if not r.ok]
    assert failures == [], " | ".join(failures)


def test_selfcheck_leaves_the_shell_directory_untouched(tmp_path):
    set_session_cwd(tmp_path)
    run_selfcheck()
    assert session_cwd() == tmp_path


def test_module_report_names_the_loaded_files():
    report = module_report()
    assert "tools.py" in report
    assert "apply_patch.py" in report


def test_code_origin_is_reported():
    kind, directory = code_origin()
    assert kind in {"depot", "paquet", "inconnu"}
    if kind != "inconnu":
        assert os.path.isdir(directory)
