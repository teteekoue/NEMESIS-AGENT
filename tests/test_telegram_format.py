from src.core.telegram_format import markdown_to_plain_text


def test_markdown_becomes_structured_plain_text_without_markers():
    source = """## Résumé

Voici **le résultat** et `valeur`.

- Première étape
- Deuxième étape avec [documentation](https://example.test/docs)

> À retenir : *vérifier ensuite*.

```python
print(1)
```
"""
    result = markdown_to_plain_text(source)

    assert "Résumé\n──────" in result
    assert "Voici le résultat et valeur." in result
    assert "• Première étape" in result
    assert "• Deuxième étape avec documentation (https://example.test/docs)" in result
    assert "│ À retenir : vérifier ensuite." in result
    assert "  print(1)" in result
    assert "**" not in result
    assert "```" not in result
    assert "[documentation]" not in result


def test_telegram_sender_sends_plain_text_and_chunks_long_lines():
    import asyncio
    from types import SimpleNamespace

    from telegram_bot import NemesisTelegramBot

    class FakeBot:
        def __init__(self):
            self.calls = []

        async def send_message(self, **kwargs):
            self.calls.append(kwargs)

    sender = NemesisTelegramBot.__new__(NemesisTelegramBot)
    fake = FakeBot()
    asyncio.run(sender._send_long_message(SimpleNamespace(bot=fake), 12, "x" * 8000))

    assert len(fake.calls) == 3
    assert all(len(call["text"]) <= 3900 for call in fake.calls)
    assert all("parse_mode" not in call for call in fake.calls)
    chunks = [call["text"].split("\n\n", 1)[1] for call in fake.calls]
    assert "".join(chunks) == "x" * 8000


def test_tables_and_ordered_lists_remain_readable():
    result = markdown_to_plain_text(
        "| Nom | État |\n| --- | --- |\n| tâche | terminée |\n\n1. Un\n2. Deux"
    )
    assert "Nom   |   État" in result
    assert "tâche   |   terminée" in result
    assert "1. Un" in result
    assert "2. Deux" in result
