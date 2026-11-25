"""License gate over the installed environment (ADR-0008, N2).

Dependency admission is argued evidence, not list membership (N2):
the adding agent states the community-validation case in the pull
request, the reviewer verifies the evidence, and two mechanical
fronts fail closed in CI on every pull request - the vulnerability
audit (pip-audit, already in ci.yml) and this license gate. Every
installed distribution must resolve to a license inside the allowed
set below; a distribution with no license metadata at all fails
closed, never passes silently.

The allowed set is configuration, not constitution: an instance
edits the set in its own repository, and a new dependency with an
off-list license is a pull request that argues both the community
validation and the license exception. Compound SPDX expressions are
evaluated the way they bind: ``OR`` is a choice and passes when one
operand is allowed, ``AND`` multiplies and requires every operand,
``WITH`` carries an exception and binds to the operand before it.
"""

import importlib.metadata
from typing import TYPE_CHECKING

from scripts._shared import (
    EXIT_CODE_CRITERIA_UNMET,
    EXIT_CODE_SUCCESS,
)

if TYPE_CHECKING:
    import argparse

# SPDX identifiers (casefolded) plus the classifier suffixes some
# distributions still carry instead of an expression. Configuration
# lives here on purpose: a new license joins the set through a pull
# request, not through this gate's verdict (N2).
ALLOWED_LICENSES = frozenset(
    {
        "mit",
        "apache-2.0",
        "bsd-2-clause",
        "bsd-3-clause",
        "0bsd",
        "isc",
        "mpl-2.0",
        "unlicense",
        "psf-2.0",
        "mit license",
        "apache software license",
        "bsd license",
        "bsd 2-clause license",
        "bsd 3-clause license",
        "isc license",
        "mozilla public license 2.0 (mpl 2.0)",
        "python software foundation license",
        "the unlicense (unlicense)",
    }
)


def split_outside_parentheses(text: str, separator: str) -> list[str] | None:
    """Split ``text`` on ``separator`` at depth zero; None when absent."""
    depth = 0
    parts: list[str] = []
    start = 0
    position = 0
    while position <= len(text) - len(separator):
        character = text[position]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0 and text[position : position + len(separator)] == separator:
            parts.append(text[start:position])
            start = position + len(separator)
            position += len(separator)
            continue
        position += 1
    if not parts:
        return None
    parts.append(text[start:])
    return parts


def expression_is_allowed(expression: str) -> bool:
    """Evaluate one SPDX license expression against the allowed set."""
    cleaned = expression.strip()
    or_parts = split_outside_parentheses(cleaned, " OR ")
    if or_parts is not None:
        return any(expression_is_allowed(part) for part in or_parts)
    and_parts = split_outside_parentheses(cleaned, " AND ")
    if and_parts is not None:
        return all(expression_is_allowed(part) for part in and_parts)
    if cleaned.startswith("(") and cleaned.endswith(")"):
        inner = cleaned[1:-1].strip()
        if split_outside_parentheses(inner, " OR ") is not None or (
            split_outside_parentheses(inner, " AND ") is not None
        ):
            return expression_is_allowed(inner)
    with_position = cleaned.find(" WITH ")
    if with_position != -1:
        cleaned = cleaned[:with_position].strip()
    return cleaned.casefold() in ALLOWED_LICENSES


def license_violation(
    *,
    expression: str,
    license_classifiers: tuple[str, ...],
    legacy_license: str,
) -> str | None:
    """Return the blocking evidence, or None when the license is allowed.

    Precedence follows the modern core metadata: the license
    expression decides when present, the license classifiers when
    there is none, the legacy license field last. A distribution
    passing none of the three has no license metadata and fails
    closed.
    """
    if expression:
        return None if expression_is_allowed(expression) else expression
    if license_classifiers:
        if any(name.casefold() in ALLOWED_LICENSES for name in license_classifiers):
            return None
        return "; ".join(license_classifiers)
    if legacy_license:
        return None if legacy_license.casefold() in ALLOWED_LICENSES else legacy_license
    return "no license metadata at all"


def distribution_violation(*, distribution: importlib.metadata.Distribution) -> str | None:
    """Extract one distribution's metadata fields, then delegate."""
    metadata = distribution.metadata
    return license_violation(
        expression=metadata.get("License-Expression", ""),
        license_classifiers=tuple(
            classifier.split(" :: ")[-1]
            for classifier in metadata.get_all("Classifier", [])
            if classifier.startswith("License")
        ),
        legacy_license=metadata.get("License", ""),
    )


def scan_offenders() -> list[str]:
    """Scan every installed distribution; return one line per offender."""
    offenders: list[str] = []
    for distribution in importlib.metadata.distributions():
        violation = distribution_violation(distribution=distribution)
        if violation is not None:
            offenders.append(f"{distribution.metadata['Name']} {distribution.version}: {violation}")
    return offenders


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the license-gate subcommand: no option, fail closed."""
    subparser = subparsers.add_parser(
        "license-gate",
        help="Fail on any installed license outside the allowed set (ADR-0008 N2).",
        description=(
            "License gate (ADR-0008 N2): every installed distribution must"
            " resolve to a license inside the configured allowed set of"
            " scripts/license_gate.py; anything off-list - or with no"
            " license metadata at all - fails the gate before review starts."
        ),
    )
    subparser.set_defaults(runner=run)


def run(*, arguments: argparse.Namespace) -> int:
    """Entry point: report offenders; exit 1 as soon as one exists."""
    del arguments  # The gate takes no option, on purpose.
    offenders = scan_offenders()
    for offender in offenders:
        print(f"license gate: {offender} - outside the configured allowed set.")
    if offenders:
        print(
            f"license gate: {len(offenders)} distribution(s) outside the allowed set"
            " (the set lives in scripts/license_gate.py, N2: configuration,"
            " not constitution)."
        )
        return EXIT_CODE_CRITERIA_UNMET
    return EXIT_CODE_SUCCESS
