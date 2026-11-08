"""Command-line entry point: ``python -m scripts <command>`` (ADR-0021).

Make stays THE agent surface: every subcommand below matches a make
target one-to-one (ADR-0021). This dispatcher only wires the
subcommands to their domain modules; it owns no protocol of its own.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Protocol

from scripts import ci, claims, day, merge, pulls, session
from scripts._shared import load_environment_file

if TYPE_CHECKING:
    from collections.abc import Sequence


class Runner(Protocol):
    """A subcommand entry point: keyword-only arguments in, exit code out."""

    def __call__(self, *, arguments: argparse.Namespace) -> int:
        """Run the subcommand."""
        ...


class CommandModule(Protocol):
    """A driver domain module: one register() wiring its subcommands."""

    def register(self, subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
        """Wire this domain's subcommands into the parser."""
        ...


# Grown by each domain PR (ADR-0021).
COMMAND_MODULES: tuple[CommandModule, ...] = (pulls, ci, claims, merge, day, session)


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the CLI: one subparser per driver subcommand."""
    argument_parser = argparse.ArgumentParser(
        prog="scripts",
        description="Project drivers: one subcommand per make target (ADR-0021).",
    )
    subparsers = argument_parser.add_subparsers(dest="command", required=True)
    for module in COMMAND_MODULES:
        module.register(subparsers)
    return argument_parser


def main(*, command_line_arguments: Sequence[str]) -> int:
    """Entry point: environment loaded once, subcommand dispatched."""
    load_environment_file()
    arguments = build_argument_parser().parse_args(command_line_arguments)
    # ADR-0006 escape: argparse stores the runner default as Any on the
    # Namespace; every registered subcommand sets it via set_defaults.
    runner: Runner = arguments.runner
    return runner(arguments=arguments)


if __name__ == "__main__":
    sys.exit(main(command_line_arguments=sys.argv[1:]))
