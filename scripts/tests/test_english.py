"""Tests for the English-only front scripts/english.py (ADR-0008 G4, N4)."""

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from scripts import english
from scripts._shared import (
    EXIT_CODE_CRITERIA_UNMET,
    EXIT_CODE_PROTOCOL_FAILURE,
    EXIT_CODE_SUCCESS,
)

if TYPE_CHECKING:
    import pytest

# Accented characters are built from code points: this test file itself
# stays accent-free and scans clean under the front it verifies.
E_ACUTE = chr(0xE9)
E_GRAVE = chr(0xE8)


def test_markdown_strips_fenced_blocks_and_inline_spans() -> None:
    """Fenced code blocks and inline code spans are skipped in Markdown."""
    stripped = english.markdown_without_code_spans(
        "# Title\n\n```text\nle defaut\n```\n\nUse `serialiser` here.\n"
    )
    assert "defaut" not in stripped
    assert "serialiser" not in stripped
    assert "Use" in stripped
    assert "here." in stripped


def test_python_corpus_splits_names_comments_strings() -> None:
    """Tokenize splits one source into its three corpora."""
    names, comments, strings = english.python_corpus(
        'def serialise(data):\n    # le defaut\n    return "chaine"\n'
    )
    assert names == ["def", "serialise", "data", "return"]
    assert comments == ["# le defaut"]
    assert strings == ['"chaine"']


def test_scan_corpus_flags_accented_character() -> None:
    """The accented signal is the first one, in every corpus."""
    offender = english.scan_corpus(
        Path("x.md"), "clean text caf" + E_ACUTE, stop_words=False, identifiers=False
    )
    assert offender is not None
    assert offender.evidence.startswith("accented character")


def test_scan_corpus_flags_french_stop_word() -> None:
    """French function words are looked for as whole words only."""
    offender = english.scan_corpus(
        Path("x"), "the default value le mode", stop_words=True, identifiers=False
    )
    assert offender is not None
    assert offender.evidence == "French stop-word: le"


def test_scan_corpus_flags_french_identifier_parts() -> None:
    """Snake and CamelCase parts are matched against the stem lexicon."""
    snake = english.scan_corpus(
        Path("x.py"), "defaut_value = 1", stop_words=False, identifiers=True
    )
    assert snake is not None
    assert snake.evidence == "French identifier: defaut_value"
    camel = english.scan_corpus(
        Path("x.py"), "SerialiseMode = 1", stop_words=False, identifiers=True
    )
    assert camel is not None
    assert camel.evidence == "French identifier: SerialiseMode"


def test_scan_corpus_leaves_english_identifiers_alone() -> None:
    """Legitimate English words never appear in the stem lexicon."""
    offender = english.scan_corpus(Path("x.py"), "upgrade = 1", stop_words=False, identifiers=True)
    assert offender is None


def test_scan_python_joins_fstring_text_to_strings() -> None:
    """On PEP 701 tokenization, f-string middles cannot hide an accent."""
    offender = english.scan_python(Path("x.py"), f'label = f"probl{E_GRAVE}me {{x}}"\n')
    assert offender is not None
    assert offender.evidence.startswith("accented character")


def test_scan_python_flags_comment_stop_word() -> None:
    """A comment is prose: French function words flag it."""
    offender = english.scan_python(Path("x.py"), "value = 1  # le flux\n")
    assert offender is not None
    assert "stop-word: le" in offender.evidence


def test_scan_python_accepts_accent_free_french_strings() -> None:
    """Strings carry data: only the accented signal applies to them."""
    offender = english.scan_python(Path("x.py"), 'value = "le defaut"\n')
    assert offender is None


def test_scan_subject_flags_french() -> None:
    """A French commit subject fails on the accented signal."""
    offender = english.scan_subject(subject="fix: corrige le probl" + E_GRAVE + "me")
    assert offender is not None
    assert offender.line == 1
    assert offender.relative == Path("<commit-subject>")


def test_scan_subject_flags_accent_free_french() -> None:
    """The stop-word signal catches accent-free French subjects."""
    assert english.scan_subject(subject="fix: corrige le defaut") is not None


def test_scan_subject_passes_english() -> None:
    """An English subject carries neither signal."""
    assert english.scan_subject(subject="fix: probe refuses a dirty tree") is None


def test_run_subject_file_verdicts(tmp_path: Path) -> None:
    """The hook shape: criteria unmet on French, success on English."""
    subject_path = tmp_path / "subject.txt"
    subject_path.write_text("docs: ajoute le front\n\nbody line\n", encoding="utf-8")
    french_arguments = argparse.Namespace(subject_file=str(subject_path))
    assert english.run(arguments=french_arguments) == EXIT_CODE_CRITERIA_UNMET
    subject_path.write_text("docs: add the front\n", encoding="utf-8")
    english_arguments = argparse.Namespace(subject_file=str(subject_path))
    assert english.run(arguments=english_arguments) == EXIT_CODE_SUCCESS


def test_run_subject_file_fails_closed_on_unreadable_file(tmp_path: Path) -> None:
    """No commit message file means: protocol failure, never silence."""
    missing_arguments = argparse.Namespace(subject_file=str(tmp_path / "missing"))
    assert english.run(arguments=missing_arguments) == EXIT_CODE_PROTOCOL_FAILURE


def test_run_whole_scope_fails_on_an_offender(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One offender anywhere fails the whole-scope verdict."""
    (tmp_path / "README.md").write_text(
        "Bienvenue sur le probl" + E_GRAVE + "me\n", encoding="utf-8"
    )
    monkeypatch.setattr(english, "REPOSITORY_ROOT", tmp_path)
    arguments = argparse.Namespace(subject_file=None)
    assert english.run(arguments=arguments) == EXIT_CODE_CRITERIA_UNMET


def test_offender_rendering() -> None:
    """The finding renders as ``path:line: evidence``, line optional."""
    located = english.Offender(Path("a/b.md"), 3, "French stop-word: le")
    assert str(located) == "a/b.md:3: French stop-word: le"
    unlocated = english.Offender(Path("a/b.md"), 0, "French stop-word: le")
    assert str(unlocated) == "a/b.md: French stop-word: le"


def test_is_allowed_skips_lock_files() -> None:
    """Machine-generated lock files never scan; Markdown always does."""
    assert english.is_allowed(Path("uv.lock")) is True
    assert english.is_allowed(Path("docs/rules.md")) is False


def test_scan_file_plain_text_contracts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Plain-text configuration: stop-words apply, identifiers do not."""
    monkeypatch.setattr(english, "REPOSITORY_ROOT", tmp_path)
    (tmp_path / ".gitignore").write_text("le build\n", encoding="utf-8")
    offender = english.scan_file(Path(".gitignore"))
    assert offender is not None
    assert "stop-word" in offender.evidence
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.15-slim\n# pour le build\n", encoding="utf-8"
    )
    offender = english.scan_file(Path("Dockerfile"))
    assert offender is not None
    assert "stop-word" in offender.evidence
