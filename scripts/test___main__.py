"""Tests for the scripts CLI dispatcher __main__.py (ADR-0021)."""

from typing import TYPE_CHECKING

import pytest

from scripts import __main__ as scripts_cli

if TYPE_CHECKING:
    import argparse


def test_build_argument_parser_exposes_no_subcommand_yet() -> None:
    """The base PR registers no domain: the parser builds and requires a command."""
    parser = scripts_cli.build_argument_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_main_dispatches_a_registered_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """A registered domain module receives its subcommand and its runner is called."""
    calls: list[str] = []

    class FakeModule:
        def register(self, subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
            subparser = subparsers.add_parser("fake")

            def runner(arguments: argparse.Namespace) -> int:
                calls.append("ran")
                return 0

            subparser.set_defaults(runner=runner)

    monkeypatch.setattr(scripts_cli, "COMMAND_MODULES", (FakeModule(),))
    assert scripts_cli.main(command_line_arguments=["fake"]) == 0
    assert calls == ["ran"]
