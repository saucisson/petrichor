r"""The reviewer agent: one local session, one structured verdict (ADR-0006).

The reviewer is a headless Vibe Code session (``vibe --prompt-file``)
launched in an ISOLATED empty directory whose only content is the
review bundle (built by ``make review-bundle``). Isolation is enforced
by construction: the session sees no repository, no AGENTS.md and no
session history - only the bundle (ADR-0006). It answers with a JSON
report; this driver recomputes every count locally, fails closed on
anything it cannot verify, and exits with a status the merge driver
(``make merge-pr``) gates on.

The session spends the INCLUDED usage of the local Vibe Code plan,
not pay-per-token API calls - that is the point of ADR-0006.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence

REVIEWER_CLI_COMMAND = "vibe"
REVIEWER_MODEL_VARIABLE = "REVIEWER_MODEL"
REVIEWER_MAX_TOKENS_VARIABLE = "REVIEWER_MAX_TOKENS"
SESSION_TIME_LIMIT_VARIABLE = "REVIEWER_TIME_LIMIT_SECONDS"
DEFAULT_SESSION_TIME_LIMIT_SECONDS = 600
MAXIMUM_SESSION_TURNS = 8

BUNDLE_FILE_NAME = "bundle.md"
SYSTEM_PROMPT_FILE_NAME = "system-prompt.md"
PROMPT_FILE_NAME = "prompt.md"

EXIT_CODE_REVIEWED_CLEAN = 0
EXIT_CODE_BLOCKING_FINDINGS = 1
EXIT_CODE_PROTOCOL_FAILURE = 2

UNKNOWN_SEVERITY_NOTE = (
    "unknown severity reported by the reviewer - treated as BLOCKING (fail-closed)"
)


class ReviewProtocolError(RuntimeError):
    """The reviewer answer violated the report contract."""


class FindingSeverity(StrEnum):
    """The severity ladder of the review report (ADR-0006)."""

    BLOCKING = "BLOCKING"
    TO_FIX = "TO-FIX"
    SUGGESTION = "SUGGESTION"


@dataclass(frozen=True)
class ReviewFinding:
    """One finding: a rule, a place, and the evidence for it."""

    severity: FindingSeverity
    file: str
    line: int
    rule: str
    evidence: str


SYSTEM_PROMPT_TEXT = """You are the reviewer agent of this project. You judge \
ONLY the material given to you in this bundle: the pull request \
specification and the diff under review, measured against the role \
contract, the architecture rules, the review checklist and the decisions \
(ADRs) included below. You do not guess what the author meant; you seek \
counterexamples. Every finding must carry file, line, the violated rule, \
and concrete evidence (a counterexample, a quote, a missing assertion). \
A checklist item you cannot verify from the bundle is a finding, not a \
benefit of the doubt.

Answer with a single JSON object, nothing else, in exactly this shape:

{
  "findings": [
    {
      "severity": "BLOCKING" | "TO-FIX" | "SUGGESTION",
      "file": "path from the diff",
      "line": 42,
      "rule": "the violated rule, in a few words",
      "evidence": "the counterexample or evidence, one or two sentences"
    }
  ],
  "recommendation": "merge" | "iterate"
}

Severity contract: BLOCKING = violates a checklist rule, an ADR or the \
acceptance criteria, no merge until fixed. TO-FIX = real problem, merge \
can proceed only if trivial and tracked. SUGGESTION = advisory, never \
blocks. Zero findings is a valid answer. You never merge, never modify \
code, and never soften a violation.\""""


REVIEWER_PROMPT_TEXT = """You are reviewing a pull request. The file \
`bundle.md` in this directory is your ENTIRE context: the role contract, \
the architecture rules, the review checklist, the decisions (ADRs), the \
pull request specification and the diff under review. There is nothing \
else in this directory, and nothing else exists for you.

Read `bundle.md`, judge ONLY the diff it contains against the rules and \
the acceptance criteria it carries, seeking counterexamples, then answer \
with a single JSON object, nothing else, in exactly the shape the role \
contract in the bundle defines (findings with severity, file, line, rule, \
evidence, then recommendation). Zero findings is a valid answer."""


def load_model_name() -> str:
    """The reviewing model; empty means the CLI's active model (ADR-0006).

    Set ``REVIEWER_MODEL`` to point the session at a different model
    family than the developer agent - the model-diversity lever of
    ADR-0006 survives the move to local sessions.
    """
    return os.environ.get(REVIEWER_MODEL_VARIABLE, "").strip()


def load_max_tokens() -> str:
    """Optional hard cap on the session's total tokens; empty = uncapped."""
    return os.environ.get(REVIEWER_MAX_TOKENS_VARIABLE, "").strip()


def load_time_limit_seconds() -> int:
    """The session's wall-clock budget (``--time-limit``)."""
    raw_value = os.environ.get(SESSION_TIME_LIMIT_VARIABLE, "").strip()
    if not raw_value:
        return DEFAULT_SESSION_TIME_LIMIT_SECONDS
    try:
        return int(raw_value)
    except ValueError:
        print(
            f"Error: {SESSION_TIME_LIMIT_VARIABLE} is not an integer: {raw_value}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from None


def run_reviewer_session(*, model_name: str, bundle_path: Path) -> str:
    """Run the headless reviewer session; return its final answer text.

    The session runs in a fresh empty directory containing only the
    bundle and the two prompt files - isolation by construction: no
    repository, no project configuration, no session history. The
    session cannot spend approval prompts either: programmatic mode
    denies any tool call that would need one, so the review answers
    from the bundle alone.
    """
    session_directory = Path(tempfile.mkdtemp(prefix="reviewer-session-"))
    try:
        shutil.copyfile(bundle_path, session_directory / BUNDLE_FILE_NAME)
        (session_directory / SYSTEM_PROMPT_FILE_NAME).write_text(
            SYSTEM_PROMPT_TEXT, encoding="utf-8"
        )
        (session_directory / PROMPT_FILE_NAME).write_text(REVIEWER_PROMPT_TEXT, encoding="utf-8")
        command = [
            REVIEWER_CLI_COMMAND,
            "--prompt-file",
            str(session_directory / PROMPT_FILE_NAME),
            "--system-prompt-file",
            str(session_directory / SYSTEM_PROMPT_FILE_NAME),
            "--output",
            "json",
            "--max-turns",
            str(MAXIMUM_SESSION_TURNS),
            "--time-limit",
            str(load_time_limit_seconds()),
        ]
        if model_name:
            command += ["--model", model_name]
        if load_max_tokens():
            command += ["--max-tokens", load_max_tokens()]
        completed_process = subprocess.run(
            command,
            cwd=session_directory,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        shutil.rmtree(session_directory, ignore_errors=True)
    if completed_process.returncode != EXIT_CODE_REVIEWED_CLEAN:
        print(
            f"Error: the reviewer session exited with code"
            f" {completed_process.returncode} (not started, runtime failure,"
            f" or a budget limit reached): {completed_process.stderr.strip()}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return extract_answer_text(raw_session_output=completed_process.stdout)


def extract_answer_text(*, raw_session_output: str) -> str:
    """Narrow the CLI's session messages down to the final answer text.

    The ``--output json`` format is a JSON array of session messages;
    the answer is the last assistant message. Fails closed on any other
    shape.
    """
    try:
        session_messages: object = json.loads(raw_session_output)
    except json.JSONDecodeError as json_error:
        raise ReviewProtocolError(
            f"the session output is not valid JSON: {json_error}"
        ) from json_error
    if not isinstance(session_messages, list):
        raise ReviewProtocolError("the session output is not a message list")
    for message_container in reversed(session_messages):
        if not isinstance(message_container, dict):
            continue
        if message_container.get("role") != "assistant":
            continue
        content_container: object = message_container.get("content")
        if isinstance(content_container, str) and content_container.strip():
            return content_container
        if isinstance(content_container, list):
            answer_text = joined_block_text(blocks=content_parts(blocks=content_container))
            if answer_text is not None:
                return answer_text
    raise ReviewProtocolError("the session output has no assistant answer")


def content_parts(*, blocks: object) -> list[dict[str, object]]:
    """Narrow message content blocks to objects, ignoring the rest."""
    if not isinstance(blocks, list):
        return []
    return [block for block in blocks if isinstance(block, dict)]


def joined_block_text(*, blocks: list[dict[str, object]]) -> str | None:
    """Join the textual blocks of a multi-block assistant answer, if any."""
    text_parts: list[str] = []
    for block in blocks:
        text = block.get("text")
        if isinstance(text, str) and text:
            text_parts.append(text)
    if not text_parts:
        return None
    return "\n".join(text_parts)


def narrow_json_answer(*, answer_text: str) -> str:
    """Narrow a possibly fenced answer down to its JSON object."""
    cleaned_text = answer_text.strip()
    if cleaned_text.startswith("```"):
        # ```json ... ``` fences are common; keep only the payload.
        first_newline = cleaned_text.find("\n")
        closing_fence = cleaned_text.rfind("```")
        if first_newline != -1 and closing_fence > first_newline:
            cleaned_text = cleaned_text[first_newline + 1 : closing_fence].strip()
    try:
        json.loads(cleaned_text)
    except json.JSONDecodeError:
        opening_brace = cleaned_text.find("{")
        closing_brace = cleaned_text.rfind("}")
        if opening_brace == -1 or closing_brace <= opening_brace:
            raise ReviewProtocolError("the answer contains no JSON object") from None
        cleaned_text = cleaned_text[opening_brace : closing_brace + 1]
    return cleaned_text


def parse_finding(*, finding_container: object) -> ReviewFinding:
    """Validate and narrow one finding entry, or fail closed."""
    if not isinstance(finding_container, dict):
        raise ReviewProtocolError("a finding is not an object")
    severity_container: object = finding_container.get("severity")
    file_container: object = finding_container.get("file")
    line_container: object = finding_container.get("line")
    rule_container: object = finding_container.get("rule")
    evidence_container: object = finding_container.get("evidence")
    if not isinstance(severity_container, str):
        raise ReviewProtocolError("a finding has no severity")
    if not isinstance(file_container, str):
        raise ReviewProtocolError("a finding has no file")
    if not isinstance(rule_container, str):
        raise ReviewProtocolError("a finding has no rule")
    if not isinstance(evidence_container, str):
        raise ReviewProtocolError("a finding has no evidence")
    if not isinstance(line_container, int):
        raise ReviewProtocolError("a finding has no integer line")
    try:
        severity = FindingSeverity(severity_container)
    except ValueError:
        # Fail closed: an unreadable severity blocks the merge, never enables it.
        return ReviewFinding(
            severity=FindingSeverity.BLOCKING,
            file=file_container,
            line=line_container,
            rule=severity_container,
            evidence=UNKNOWN_SEVERITY_NOTE,
        )
    return ReviewFinding(
        severity=severity,
        file=file_container,
        line=line_container,
        rule=rule_container,
        evidence=evidence_container,
    )


def parse_report(*, answer_text: str) -> list[ReviewFinding]:
    """Parse the JSON answer into validated findings; counts are recomputed here."""
    try:
        report_container: object = json.loads(answer_text)
    except json.JSONDecodeError as json_error:
        raise ReviewProtocolError(f"the answer is not valid JSON: {json_error}") from json_error
    if not isinstance(report_container, dict):
        raise ReviewProtocolError("the report is not an object")
    findings_container: object = report_container.get("findings")
    if not isinstance(findings_container, list):
        raise ReviewProtocolError("the report has no findings list")
    return [parse_finding(finding_container=entry) for entry in findings_container]


def count_by_severity(*, findings: Sequence[ReviewFinding]) -> dict[FindingSeverity, int]:
    """Recompute the counts locally - the session's own counts are never trusted."""
    counts = dict.fromkeys(FindingSeverity, 0)
    for finding in findings:
        counts[finding.severity] += 1
    return counts


def render_markdown_comment(
    *,
    findings: Sequence[ReviewFinding],
    counts: dict[FindingSeverity, int],
) -> str:
    """Render the report as a pull request comment."""
    comment_sections: list[str] = []
    comment_sections.append("## Reviewer agent report")
    comment_sections.append("")
    comment_sections.append(
        f"- **BLOCKING**: {counts[FindingSeverity.BLOCKING]}"
        f" - **TO-FIX**: {counts[FindingSeverity.TO_FIX]}"
        f" - **SUGGESTION**: {counts[FindingSeverity.SUGGESTION]}"
    )
    comment_sections.append("")
    if findings:
        comment_sections.append("| Severity | Location | Rule | Evidence |")
        comment_sections.append("| --- | --- | --- | --- |")
        for finding in findings:
            safe_evidence = finding.evidence.replace("|", "\\|").replace("\n", " ")
            safe_rule = finding.rule.replace("|", "\\|").replace("\n", " ")
            comment_sections.append(
                f"| {finding.severity.value} | {finding.file}:{finding.line}"
                f" | {safe_rule} | {safe_evidence} |"
            )
    else:
        comment_sections.append("Zero findings.")
    comment_sections.append("")
    comment_sections.append(
        "The reviewer never merges and never modifies code (ADR-0006)."
        " The merge driver (`make merge-pr`) refuses to merge while any"
        " BLOCKING finding stands; merging into `daily` is executed by"
        " the developer agent on this report (ADR-0005)."
    )
    return "\n".join(comment_sections)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the agent-review subcommand."""
    subparser = subparsers.add_parser(
        "agent-review",
        help="Run the local reviewer session on a review bundle (ADR-0006).",
        description="Run the local reviewer session on a review bundle (ADR-0006).",
    )
    subparser.add_argument(
        "--bundle-file",
        required=True,
        help="Path to the review bundle built by `make review-bundle`.",
    )
    subparser.add_argument(
        "--report-file",
        required=True,
        help="Path where the machine-readable JSON report is written.",
    )
    subparser.add_argument(
        "--comment-file",
        default=None,
        help="Optional path where the Markdown comment is written.",
    )
    subparser.set_defaults(runner=run)


def run(*, arguments: argparse.Namespace) -> int:
    """Entry point: bundle in, verified verdict out."""
    answer_text = run_reviewer_session(
        model_name=load_model_name(),
        bundle_path=Path(arguments.bundle_file),
    )
    try:
        reported_findings = parse_report(answer_text=narrow_json_answer(answer_text=answer_text))
    except ReviewProtocolError as protocol_error:
        print(
            f"Error: the reviewer answer violated the contract: {protocol_error}",
            file=sys.stderr,
        )
        return EXIT_CODE_PROTOCOL_FAILURE
    findings = reported_findings
    counts = count_by_severity(findings=findings)
    report_payload = {
        "findings": [
            {
                "severity": finding.severity.value,
                "file": finding.file,
                "line": finding.line,
                "rule": finding.rule,
                "evidence": finding.evidence,
            }
            for finding in findings
        ],
        "blocking_count": counts[FindingSeverity.BLOCKING],
        "to_fix_count": counts[FindingSeverity.TO_FIX],
        "suggestion_count": counts[FindingSeverity.SUGGESTION],
    }
    Path(arguments.report_file).write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
    if arguments.comment_file is not None:
        Path(arguments.comment_file).write_text(
            render_markdown_comment(findings=findings, counts=counts),
            encoding="utf-8",
        )
    print(
        f"Review complete: {counts[FindingSeverity.BLOCKING]} BLOCKING,"
        f" {counts[FindingSeverity.TO_FIX]} TO-FIX,"
        f" {counts[FindingSeverity.SUGGESTION]} SUGGESTION."
    )
    if counts[FindingSeverity.BLOCKING] > 0:
        return EXIT_CODE_BLOCKING_FINDINGS
    return EXIT_CODE_REVIEWED_CLEAN
