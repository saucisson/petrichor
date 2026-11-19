"""Tests for the review drivers scripts/review.py (ADR-0010, template 2aa4d27)."""

import argparse
import hashlib
import json
from typing import TYPE_CHECKING

import httpx
import pytest

from scripts import _github, _shared, review

if TYPE_CHECKING:
    from pathlib import Path

HEAD_SHA = "abc123"


def parse_review_arguments(command_line_arguments: list[str]) -> argparse.Namespace:
    """Parse arguments through the registered review subcommands."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    review.register(subparsers)
    return parser.parse_args(command_line_arguments)


def green_check_runs() -> list[dict[str, object]]:
    """Build an all-success check-runs list."""
    return [{"name": "quality gate", "status": "completed", "conclusion": "success"}]


def test_register_wires_review_and_review_bundle() -> None:
    """Both review subcommands are registered."""
    arguments = parse_review_arguments(["review", "--pr-number", "64"])
    assert arguments.pr_number == 64
    arguments = parse_review_arguments(["review-bundle", "--base-ref", "origin/daily"])
    assert arguments.base_ref == "origin/daily"
    with pytest.raises(SystemExit):
        parse_review_arguments(["review"])


def test_preconditions_pass_on_matching_head_and_green_ci() -> None:
    """Matching head, clean tree, all checks green: the review may start."""
    refusal = review.review_preconditions_refusal(
        head_sha=HEAD_SHA,
        local_head=HEAD_SHA,
        pending_changes="",
        check_runs=green_check_runs(),
    )
    assert refusal is None


def test_preconditions_refuse_a_stale_head() -> None:
    """PR head != local HEAD: refused, the review never judges a stale head."""
    refusal = review.review_preconditions_refusal(
        head_sha=HEAD_SHA,
        local_head="other",
        pending_changes="",
        check_runs=green_check_runs(),
    )
    assert refusal is not None and "not the local HEAD" in refusal


def test_preconditions_refuse_uncommitted_changes() -> None:
    """A dirty tree: refused."""
    refusal = review.review_preconditions_refusal(
        head_sha=HEAD_SHA,
        local_head=HEAD_SHA,
        pending_changes=" M file.py",
        check_runs=green_check_runs(),
    )
    assert refusal is not None and "uncommitted changes" in refusal


def test_preconditions_refuse_when_no_check_runs() -> None:
    """No CI check run on the head yet: refused."""
    refusal = review.review_preconditions_refusal(
        head_sha=HEAD_SHA, local_head=HEAD_SHA, pending_changes="", check_runs=[]
    )
    assert refusal is not None and "no CI check runs" in refusal


def test_preconditions_refuse_unfinished_ci() -> None:
    """A check still running: refused - the review is the last gate."""
    refusal = review.review_preconditions_refusal(
        head_sha=HEAD_SHA,
        local_head=HEAD_SHA,
        pending_changes="",
        check_runs=[{"name": "quality gate", "status": "in_progress", "conclusion": None}],
    )
    assert refusal is not None and "still running" in refusal


def test_preconditions_refuse_failed_ci() -> None:
    """A completed-but-failed check: refused."""
    refusal = review.review_preconditions_refusal(
        head_sha=HEAD_SHA,
        local_head=HEAD_SHA,
        pending_changes="",
        check_runs=[{"name": "quality gate", "status": "completed", "conclusion": "failure"}],
    )
    assert refusal is not None and "did not succeed" in refusal


def test_write_review_context_records_pr_and_head(tmp_path: Path) -> None:
    """The review context pins the pull request number and the head SHA."""
    review.write_review_context(
        pull_request_number=64, head_sha=HEAD_SHA, review_directory=tmp_path
    )
    context = json.loads((tmp_path / "review-context.json").read_text(encoding="utf-8"))
    assert context["pr_number"] == 64
    assert context["head_sha"] == HEAD_SHA


def test_render_bundle_includes_the_diff_and_decisions() -> None:
    """The bundle carries the diff and the decision records."""
    bundle = review.render_bundle(
        diff_text="diff --git a/x b/x\n",
        description_text="PR body",
        decisions=[("Adr 0009", "merge criteria")],
    )
    assert "diff --git a/x b/x" in bundle
    assert "Adr 0009" in bundle
    assert "PR body" in bundle


def test_run_review_refuses_with_exit_3_on_stale_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End to end: a stale head exits 3 (preconditions unmet), not a verdict."""
    monkeypatch.setattr(review, "load_github_token", lambda: "token")
    monkeypatch.setattr(review, "resolve_repository_slug", lambda: "o/r")
    monkeypatch.setattr(review, "run_git", lambda *, arguments: "other\n")
    monkeypatch.setattr(review, "REVIEW_DIRECTORY", tmp_path)

    pull_request = {
        "number": 64,
        "head": {"sha": HEAD_SHA, "ref": "feat/x"},
        "base": {"ref": "daily"},
    }

    def fake_fetch_pull_request(**kwargs: object) -> dict[str, object]:
        return pull_request

    def fake_fetch_check_runs(**kwargs: object) -> list[dict[str, object]]:
        return green_check_runs()

    monkeypatch.setattr(_github, "fetch_pull_request", fake_fetch_pull_request)
    monkeypatch.setattr(_github, "fetch_check_runs", fake_fetch_check_runs)
    monkeypatch.setattr(
        _github,
        "open_github_client",
        lambda *, token: httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200))
        ),
    )
    arguments = parse_review_arguments(["review", "--pr-number", "64"])
    assert review.run_review(arguments=arguments) == review.EXIT_CODE_PRECONDITIONS_UNMET
    assert review.EXIT_CODE_PRECONDITIONS_UNMET == 3
    assert _shared.EXIT_CODE_PROTOCOL_FAILURE == 2


FIXTURE_DIFF_WITH_MACHINE = """diff --git a/scripts/review.py b/scripts/review.py
index 1111111..2222222 100644
--- a/scripts/review.py
+++ b/scripts/review.py
@@ -1 +1 @@
-old
+new
diff --git a/docs/studies/p/v1/execution/r.json b/docs/studies/p/v1/execution/r.json
index 3333333..4444444 100644
--- a/docs/studies/p/v1/execution/r.json
+++ b/docs/studies/p/v1/execution/r.json
@@ -1 +1 @@
-{"old": true}
+{"new": true}
diff --git a/src/petrichor/study/pilote_v1/model.pnml b/src/petrichor/study/pilote_v1/model.pnml
index 5555555..6666666 100644
--- a/src/petrichor/study/pilote_v1/model.pnml
+++ b/src/petrichor/study/pilote_v1/model.pnml
@@ -1 +1 @@
-<pnml-old/>
+<pnml-new/>
"""

FIXTURE_DIFF_WITHOUT_MACHINE = """diff --git a/scripts/review.py b/scripts/review.py
index 1111111..2222222 100644
--- a/scripts/review.py
+++ b/scripts/review.py
@@ -1 +1 @@
-old
+new
"""


def fake_digest(*, path: str) -> tuple[int, str] | None:
    """Deterministic digest stand-in for tests: sha256 of the path itself."""
    return len(path), hashlib.sha256(path.encode()).hexdigest()


def test_path_is_excluded_matches_every_machine_pattern() -> None:
    """Each documented exclusion pattern matches its machine artifacts."""
    assert review.path_is_excluded(path="docs/studies/pilote/v1/execution/rapport.json")
    assert review.path_is_excluded(path="docs/studies/pilote/v1/audit/audit.json")
    assert review.path_is_excluded(path="audit.json")
    assert review.path_is_excluded(path="src/petrichor/llm/golden/sha256:abc/reading.json")
    assert review.path_is_excluded(path="src/petrichor/study/pilote_v1/model.pnml")
    assert not review.path_is_excluded(path="scripts/review.py")
    assert not review.path_is_excluded(path="tests/fixtures/philosophes3.pnml")
    assert not review.path_is_excluded(path="docs/studies/pilote/v1/audit/frictions.md")


def test_controller_for_path_names_the_controlling_test() -> None:
    """The digest line names the test or command controlling the artifact."""
    execution_path = "docs/studies/p/v1/execution/journal.json"
    assert "study-p-audit" in review.controller_for_path(path=execution_path)
    assert "test_llm_guard" in review.controller_for_path(path="src/petrichor/llm/golden/x.json")
    model_path = "src/petrichor/study/pilote_v1/model.pnml"
    assert "test_study_pilote" in review.controller_for_path(path=model_path)


def test_sanitize_diff_removes_machine_chunks_and_adds_digests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Machine chunks leave the diff; one sorted digest line per excluded path."""
    monkeypatch.setattr(review, "digest_for_head_path", fake_digest)
    sanitized = review.sanitize_diff(diff_text=FIXTURE_DIFF_WITH_MACHINE)
    assert "r.json" not in sanitized.split(review.EXCLUDED_DIGEST_SECTION_HEADER)[0]
    assert "<pnml-new/>" not in sanitized
    assert "+new" in sanitized  # the real code chunk stays
    digest_section = sanitized.split(review.EXCLUDED_DIGEST_SECTION_HEADER)[1]
    assert "sha256:" in digest_section
    assert "audité par `make study-p-audit`" in digest_section
    assert "test_verify_materials_returns_the_pinned_hashes" in digest_section
    lines = [line for line in digest_section.splitlines() if line.startswith("- `")]
    assert lines == sorted(lines)


def test_sanitize_diff_is_deterministic_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two runs on the same diff produce byte-identical output; re-sanitizing is a no-op."""
    monkeypatch.setattr(review, "digest_for_head_path", fake_digest)
    first = review.sanitize_diff(diff_text=FIXTURE_DIFF_WITH_MACHINE)
    second = review.sanitize_diff(diff_text=FIXTURE_DIFF_WITH_MACHINE)
    assert first == second
    assert review.sanitize_diff(diff_text=first) == first


def test_bundle_diff_part_is_equal_with_and_without_machine_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Golden: the diff part of the bundle is identical whether or not the
    fixture diff carries machine files - only the digest lines are added."""
    monkeypatch.setattr(review, "digest_for_head_path", fake_digest)
    with_machine = review.sanitize_diff(diff_text=FIXTURE_DIFF_WITH_MACHINE)
    without_machine = review.sanitize_diff(diff_text=FIXTURE_DIFF_WITHOUT_MACHINE)
    diff_part = with_machine.split(review.EXCLUDED_DIGEST_SECTION_HEADER)[0]
    assert diff_part.rstrip("\n") == without_machine.rstrip("\n")


def test_run_review_bundle_sanitizes_a_diff_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """review-bundle --diff-file applies the exclusions before assembling."""
    monkeypatch.setattr(review, "digest_for_head_path", fake_digest)
    diff_file = tmp_path / "pr.diff"
    diff_file.write_text(FIXTURE_DIFF_WITH_MACHINE, encoding="utf-8")
    output_file = tmp_path / "bundle.md"
    arguments = parse_review_arguments(
        ["review-bundle", "--diff-file", str(diff_file), "--output", str(output_file)]
    )
    assert review.run_review_bundle(arguments=arguments) == _shared.EXIT_CODE_SUCCESS
    bundle = output_file.read_text(encoding="utf-8")
    diff_section = bundle.split("## DIFF UNDER REVIEW")[1]
    assert "<pnml-new/>" not in diff_section
    assert review.EXCLUDED_DIGEST_SECTION_HEADER in diff_section
    assert "+new" in diff_section
