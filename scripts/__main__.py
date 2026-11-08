"""Command-line entry point: ``python -m scripts <command>`` (ADR-0021).

Make stays THE agent surface: every subcommand below matches a make
target one-to-one (ADR-0021). This dispatcher only wires the
subcommands to their domain modules; it owns no protocol of its own.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Protocol

from scripts._shared import load_environment_file

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    class CommandModule(Protocol):
        """A driver domain module: one register() wiring its subcommands."""

        def register(self, subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
            """Wire this domain's subcommands into the parser."""
            ...


COMMAND_MODULES: tuple[CommandModule, ...] = ()  # grown by each domain PR (ADR-0021)


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
    runner: Callable[[argparse.Namespace], int] = arguments.runner
    return runner(arguments)


if __name__ == "__main__":
    sys.exit(main(command_line_arguments=sys.argv[1:]))
