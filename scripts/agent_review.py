"""The reviewer agent: one API call, one structured verdict (ADR-0016).

The reviewer receives ONLY the review bundle (built by
``build_review_bundle.py``) - it cannot explore the repository, so context
isolation is enforced by construction. It answers with a JSON report;
this script recomputes every count locally, fails closed on anything it
cannot verify, and exits with a status the CI can gate merges on.

Usage::

    python scripts/agent_review.py \
        --bundle-file .review/bundle.md \
        --report-file .review/review-report.json \
        --comment-file .review/review-comment.md
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx

API_ENDPOINT_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_REVIEWER_MODEL = "mistral-large-latest"
REQUEST_TIMEOUT_SECONDS = 120.0
MODEL_TEMPERATURE = 0.2
MAXIMUM_COMPLETION_TOKENS = 6000

MISTRAL_API_KEY_VARIABLE = "MISTRAL_API_KEY"
REVIEWER_MODEL_VARIABLE = "REVIEWER_MODEL"

EXIT_CODE_REVIEWED_CLEAN = 0
EXIT_CODE_BLOCKING_FINDINGS = 1
EXIT_CODE_PROTOCOL_FAILURE = 2

UNKNOWN_SEVERITY_NOTE = "unknown severity reported by the reviewer - treated as BLOCKING (fail-closed)"


class ReviewProtocolError(RuntimeError):
    """The reviewer answer violated the report contract."""


class FindingSeverity(StrEnum):
    """The severity ladder of the review report (ADR-0011)."""

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
code, and never soften a violation."""


def load_api_key() -> str:
    """Read the API key from the environment; absent means protocol failure."""
    api_key = os.environ.get(MISTRAL_API_KEY_VARIABLE, "")
    if not api_key:
        print(
            f"Error: {MISTRAL_API_KEY_VARIABLE} is not set - cannot run the reviewer.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return api_key


def load_model_name() -> str:
    """Pick the reviewer model; a different family than the developer is preferred."""
    return os.environ.get(REVIEWER_MODEL_VARIABLE, "") or DEFAULT_REVIEWER_MODEL


def request_review(*, api_key: str, model_name: str, bundle_text: str) -> str:
    """Send the bundle to the model and return the raw JSON answer text."""
    request_body = {
        "model": model_name,
        "temperature": MODEL_TEMPERATURE,
        "max_tokens": MAXIMUM_COMPLETION_TOKENS,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_TEXT},
            {"role": "user", "content": bundle_text},
        ],
    }
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as http_client:
        response = http_client.post(
            API_ENDPOINT_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=request_body,
        )
    if response.is_success:
        return extract_answer_text(response=response)
    print(
        f"Error: the API rejected the request with status {response.status_code}.",
        file=sys.stderr,
    )
    raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)


def extract_answer_text(*, response: httpx.Response) -> str:
    """Narrow the API response to the answer text, or fail closed."""
    response_body: object = response.json()
    if not isinstance(response_body, dict):
        raise ReviewProtocolError("the API answer is not an object")
    choices: object = response_body.get("choices")
    if not isinstance(choices, list) or len(choices) == 0:
        raise ReviewProtocolError("the API answer has no choices")
    first_choice: object = choices[0]
    if not isinstance(first_choice, dict):
        raise ReviewProtocolError("the first choice is not an object")
    message: object = first_choice.get("message")
    if not isinstance(message, dict):
        raise ReviewProtocolError("the first choice has no message")
    content: object = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ReviewProtocolError("the answer message is empty")
    return content


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
    findings = [
        parse_finding(finding_container=entry) for entry in findings_container
    ]
    return findings


def count_by_severity(*, findings: Sequence[ReviewFinding]) -> dict[FindingSeverity, int]:
    """Recompute the counts locally - the model's own counts are never trusted."""
    counts = {severity: 0 for severity in FindingSeverity}
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
        "The reviewer never merges and never modifies code (ADR-0011)."
        " The `reviewer` status check mirrors the BLOCKING count of this"
        " report; merging stays a platform action (ADR-0016)."
    )
    return "\n".join(comment_sections)


def parse_arguments(*, command_line_arguments: Sequence[str]) -> argparse.Namespace:
    """Parse the script contract."""
    argument_parser = argparse.ArgumentParser(
        prog="agent_review",
        description="Run the reviewer agent on a review bundle (ADR-0016).",
    )
    argument_parser.add_argument(
        "--bundle-file",
        required=True,
        help="Path to the review bundle built by build_review_bundle.py.",
    )
    argument_parser.add_argument(
        "--report-file",
        required=True,
        help="Path where the machine-readable JSON report is written.",
    )
    argument_parser.add_argument(
        "--comment-file",
        default=None,
        help="Optional path where the Markdown comment is written.",
    )
    return argument_parser.parse_args(command_line_arguments)


def main(*, command_line_arguments: Sequence[str]) -> int:
    """Entry point: bundle in, verified verdict out."""
    arguments = parse_arguments(command_line_arguments=command_line_arguments)
    bundle_text = Path(arguments.bundle_file).read_text(encoding="utf-8")
    answer_text = request_review(
        api_key=load_api_key(),
        model_name=load_model_name(),
        bundle_text=bundle_text,
    )
    try:
        findings = parse_report(answer_text=answer_text)
    except ReviewProtocolError as protocol_error:
        print(
            f"Error: the reviewer answer violated the contract: {protocol_error}",
            file=sys.stderr,
        )
        return EXIT_CODE_PROTOCOL_FAILURE
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
    Path(arguments.report_file).write_text(
        json.dumps(report_payload, indent=2), encoding="utf-8"
    )
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


if __name__ == "__main__":
    sys.exit(main(command_line_arguments=sys.argv[1:]))
