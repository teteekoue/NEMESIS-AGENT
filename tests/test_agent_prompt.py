"""Comportement du prompt systeme : defaut nouvelle discussion, /continue pour reprendre."""

from src.core import default_commands as dc


class _FakeApp:
    def __init__(self):
        self._send_system_prompt = True
        self._prompt_sent = False
        self._session_initialized = False
        self.session_id = "test-session"
        self.client = None


def test_default_is_new_discussion():
    """Au lancement, le prompt systeme part au premier message."""
    app = _FakeApp()
    assert app._send_system_prompt is True
    assert app._prompt_sent is False


def test_continue_switches_to_resume_mode():
    """La commande /continue desactive l'envoi du prompt systeme."""
    app = _FakeApp()
    dc.set_active_app(app)
    try:
        dc.continue_command([])
    finally:
        dc.set_active_app(None)

    assert app._send_system_prompt is False
    assert app._prompt_sent is True
    assert app._session_initialized is True


def test_continue_registered():
    """La commande /continue est disponible dans le registre."""
    from src.core.commands import registry

    cmd = registry.get_command("continue")
    assert cmd is not None
    assert cmd.usage == "/continue"
    assert "prompt systeme" in cmd.description


def test_continue_without_app_does_not_crash():
    """Appeler /continue sans application active ne leve pas d'exception."""
    dc.set_active_app(None)
    dc.continue_command([])
