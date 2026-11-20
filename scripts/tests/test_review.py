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
    assert review.path_is_excluded(path="src/petrichor/api/ui/node_modules/react/package.json")
    assert review.path_is_excluded(path="node_modules/left-pad/index.js")
    assert review.path_is_excluded(path="src/petrichor/api/static/assets/index-abc123.js")
    assert review.path_is_excluded(path="src/petrichor/api/ui/package-lock.json")
    assert not review.path_is_excluded(path="scripts/review.py")
    assert not review.path_is_excluded(path="tests/fixtures/philosophes3.pnml")
    assert not review.path_is_excluded(path="docs/studies/pilote/v1/audit/frictions.md")


def test_glob_match_double_star_matches_zero_directories() -> None:
    """Git `**` semantics: `**` matches zero or more whole segments."""
    assert review.glob_match(pattern="**/audit.json", path="audit.json")
    assert review.glob_match(pattern="**/audit.json", path="a/b/audit.json")
    assert review.glob_match(
        pattern="docs/studies/**/execution/**", path="docs/studies/execution/r.json"
    )
    assert review.glob_match(
        pattern="docs/studies/**/execution/**", path="docs/studies/pilote/v1/execution/runs/r.jsonl"
    )
    assert review.glob_match(
        pattern="src/petrichor/study/**/*.pnml", path="src/petrichor/study/model.pnml"
    )
    assert not review.glob_match(
        pattern="docs/studies/**/execution/**", path="docs/studies/pilote/v1/audit/audit.json"
    )
    assert not review.glob_match(pattern="**/audit.json", path="audit.json.bak")


def test_path_is_excluded_zero_directory_cases() -> None:
    """The zero-directory `**` cases match too (git pathspec semantics)."""
    assert review.path_is_excluded(path="docs/studies/execution/record.json")
    assert review.path_is_excluded(path="src/petrichor/study/model.pnml")


def test_digest_for_path_falls_back_to_the_working_tree_for_additions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A machine artifact added by the PR (absent at HEAD) is digested from disk."""
    monkeypatch.setattr(review, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(review, "digest_for_head_path", lambda *, path: None)
    added = tmp_path / "docs/studies/p/v1/execution/new.json"
    added.parent.mkdir(parents=True)
    added.write_bytes(b'{"new": true}')
    digest = review.digest_for_path(path="docs/studies/p/v1/execution/new.json")
    assert digest == (13, hashlib.sha256(b'{"new": true}').hexdigest())
    assert review.digest_for_path(path="docs/studies/p/v1/execution/gone.json") is None


def test_controller_for_path_names_the_controlling_test() -> None:
    """The digest line names the test or command controlling the artifact."""
    execution_path = "docs/studies/p/v1/execution/journal.json"
    assert "study-p-audit" in review.controller_for_path(path=execution_path)
    assert "test_llm_guard" in review.controller_for_path(path="src/petrichor/llm/golden/x.json")
    model_path = "src/petrichor/study/pilote_v1/model.pnml"
    assert "test_study_pilote" in review.controller_for_path(path=model_path)
    assert "ui-gates" in review.controller_for_path(
        path="src/petrichor/api/ui/node_modules/react/package.json"
    )
    assert review.pattern_for_path(path="src/petrichor/api/ui/node_modules/x/y.js") == (
        "**/node_modules/**"
    )


def test_sanitize_diff_removes_machine_chunks_and_adds_digests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Machine chunks leave the diff; one sorted digest line per excluded path."""
    monkeypatch.setattr(review, "digest_for_path", fake_digest)
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


def test_sanitize_diff_aggregates_deleted_machine_files_per_pathspec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleted machine artifacts yield one aggregated line per pathspec (PR #145).

    A mass deletion (untracked node_modules) must not flood the bundle with
    thousands of per-path lines: the count and the controller suffice, there
    is no content left to audit in a deletion.
    """

    def digest_or_none(*, path: str) -> tuple[int, str] | None:
        if "node_modules" in path:
            return None  # supprimé dans ce diff
        return fake_digest(path=path)

    monkeypatch.setattr(review, "digest_for_path", digest_or_none)
    diff_text = (
        "diff --git a/scripts/review.py b/scripts/review.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/scripts/review.py\n"
        "+++ b/scripts/review.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/node_modules/a/index.js b/node_modules/a/index.js\n"
        "deleted file mode 100644\n"
        "--- a/node_modules/a/index.js\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-a\n"
        "diff --git a/node_modules/b/index.js b/node_modules/b/index.js\n"
        "deleted file mode 100644\n"
        "--- a/node_modules/b/index.js\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-b\n"
    )
    sanitized = review.sanitize_diff(diff_text=diff_text)
    assert "+new" in sanitized  # the real code chunk stays
    assert "node_modules/a/index.js`" not in sanitized  # no per-path line
    digest_section = sanitized.split(review.EXCLUDED_DIGEST_SECTION_HEADER)[1]
    assert (
        "- `**/node_modules/**` — 2 fichiers supprimés dans ce diff"
        " — audités par `make ui-gates`" in digest_section
    )
    # Deterministic: same input, byte-identical output.
    assert review.sanitize_diff(diff_text=diff_text) == sanitized


def test_sanitize_diff_is_deterministic_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two runs on the same diff produce byte-identical output; re-sanitizing is a no-op."""
    monkeypatch.setattr(review, "digest_for_path", fake_digest)
    first = review.sanitize_diff(diff_text=FIXTURE_DIFF_WITH_MACHINE)
    second = review.sanitize_diff(diff_text=FIXTURE_DIFF_WITH_MACHINE)
    assert first == second
    assert review.sanitize_diff(diff_text=first) == first


def test_bundle_diff_part_is_equal_with_and_without_machine_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Golden: the diff part of the bundle is identical whether or not the
    fixture diff carries machine files - only the digest lines are added."""
    monkeypatch.setattr(review, "digest_for_path", fake_digest)
    with_machine = review.sanitize_diff(diff_text=FIXTURE_DIFF_WITH_MACHINE)
    without_machine = review.sanitize_diff(diff_text=FIXTURE_DIFF_WITHOUT_MACHINE)
    diff_part = with_machine.split(review.EXCLUDED_DIGEST_SECTION_HEADER)[0]
    assert diff_part.rstrip("\n") == without_machine.rstrip("\n")


def test_run_review_bundle_sanitizes_a_diff_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """review-bundle --diff-file applies the exclusions before assembling."""
    monkeypatch.setattr(review, "digest_for_path", fake_digest)
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


def test_load_bundle_max_bytes_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The size limit defaults to 256 KiB and is configurable by env (P59)."""
    monkeypatch.delenv(review.BUNDLE_SIZE_LIMIT_VARIABLE, raising=False)
    assert review.load_bundle_max_bytes() == 256 * 1024
    monkeypatch.setenv(review.BUNDLE_SIZE_LIMIT_VARIABLE, "1024")
    assert review.load_bundle_max_bytes() == 1024
    monkeypatch.setenv(review.BUNDLE_SIZE_LIMIT_VARIABLE, "not-an-int")
    with pytest.raises(SystemExit) as exit_info:
        review.load_bundle_max_bytes()
    assert exit_info.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_bundle_size_refusal_reports_both_sizes() -> None:
    """Over the limit, the refusal names total and diff-part sizes (P59)."""
    small_bundle = "x" * 100
    assert review.bundle_size_refusal(bundle_text=small_bundle, diff_text="d" * 10) is None
    big_bundle = "x" * (256 * 1024 + 1)
    refusal = review.bundle_size_refusal(bundle_text=big_bundle, diff_text="d" * 42)
    assert refusal is not None
    assert str(256 * 1024 + 1) in refusal
    assert "42" in refusal
    assert "split the pull request" in refusal


def test_run_review_bundle_refuses_an_oversized_diff_before_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fixture diff over the limit exits 3 and writes no bundle (P59)."""
    monkeypatch.setattr(review, "digest_for_path", fake_digest)
    monkeypatch.setenv(review.BUNDLE_SIZE_LIMIT_VARIABLE, "200")
    diff_file = tmp_path / "big.diff"
    diff_file.write_text(FIXTURE_DIFF_WITH_MACHINE, encoding="utf-8")
    output_file = tmp_path / "bundle.md"
    arguments = parse_review_arguments(
        ["review-bundle", "--diff-file", str(diff_file), "--output", str(output_file)]
    )
    assert review.run_review_bundle(arguments=arguments) == review.EXIT_CODE_PRECONDITIONS_UNMET
    assert not output_file.exists()


def test_run_review_bundle_accepts_a_diff_under_the_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Under the limit the bundle is written and the exit code is 0 (P59)."""
    monkeypatch.setattr(review, "digest_for_path", fake_digest)
    monkeypatch.setenv(review.BUNDLE_SIZE_LIMIT_VARIABLE, str(10 * 1024 * 1024))
    diff_file = tmp_path / "small.diff"
    diff_file.write_text(FIXTURE_DIFF_WITHOUT_MACHINE, encoding="utf-8")
    output_file = tmp_path / "bundle.md"
    arguments = parse_review_arguments(
        ["review-bundle", "--diff-file", str(diff_file), "--output", str(output_file)]
    )
    assert review.run_review_bundle(arguments=arguments) == _shared.EXIT_CODE_SUCCESS
    assert output_file.exists()


def test_run_review_propagates_the_size_refusal_as_preconditions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_review maps a bundle-step exit 3 to exit 3, never to protocol failure."""
    monkeypatch.setattr(review, "load_github_token", lambda: "token")
    monkeypatch.setattr(review, "resolve_repository_slug", lambda: "o/r")
    monkeypatch.setattr(
        review,
        "run_git",
        lambda *, arguments: HEAD_SHA + "\n" if arguments[:1] == ["rev-parse"] else "",
    )
    monkeypatch.setattr(review, "REVIEW_DIRECTORY", tmp_path)
    monkeypatch.setattr(
        review,
        "run_script",
        lambda *, command: review.EXIT_CODE_PRECONDITIONS_UNMET,
    )

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


def test_load_max_attempts_default_override_and_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P61: REVIEWER_MAX_ATTEMPTS defaults to 2 and is configurable."""
    monkeypatch.delenv(review.MAX_ATTEMPTS_VARIABLE, raising=False)
    assert review.load_max_attempts() == 2
    monkeypatch.setenv(review.MAX_ATTEMPTS_VARIABLE, "5")
    assert review.load_max_attempts() == 5
    monkeypatch.setenv(review.MAX_ATTEMPTS_VARIABLE, "nope")
    with pytest.raises(SystemExit) as exit_info:
        review.load_max_attempts()
    assert exit_info.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE
    monkeypatch.setenv(review.MAX_ATTEMPTS_VARIABLE, "0")
    with pytest.raises(SystemExit) as exit_info:
        review.load_max_attempts()
    assert exit_info.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_record_and_load_attempts_roundtrip_and_cap_counting(tmp_path: Path) -> None:
    """P61: the journal round-trips per (PR, head); only failed sessions count toward the cap."""
    journal = tmp_path / review.ATTEMPTS_JOURNAL_FILE_NAME
    review.record_attempt(
        journal_path=journal,
        pull_request_number=64,
        head_sha=HEAD_SHA,
        phase=review.AttemptPhase.PRECONDITIONS,
        exit_code=3,
        cause=review.AttemptCause.PRECONDITIONS,
        escalated=False,
    )
    review.record_attempt(
        journal_path=journal,
        pull_request_number=64,
        head_sha=HEAD_SHA,
        phase=review.AttemptPhase.SESSION,
        exit_code=2,
        cause=review.AttemptCause.DEADLINE,
        escalated=False,
    )
    review.record_attempt(
        journal_path=journal,
        pull_request_number=64,
        head_sha="other-head",
        phase=review.AttemptPhase.SESSION,
        exit_code=2,
        cause=review.AttemptCause.TURN_LIMIT,
        escalated=False,
    )
    attempts = review.load_attempts(journal_path=journal, pull_request_number=64, head_sha=HEAD_SHA)
    assert len(attempts) == 2
    assert review.count_failed_session_attempts(attempts=attempts) == 1
    assert (
        review.load_attempts(
            journal_path=tmp_path / "missing.jsonl", pull_request_number=64, head_sha=HEAD_SHA
        )
        == []
    )


def test_cause_for_exit_code_reads_the_session_failure_report(tmp_path: Path) -> None:
    """P61: exit 2 reads failure_cause from the report; verdicts and exit 3 map directly."""
    (tmp_path / "review-report.json").write_text(
        json.dumps({"exit_code": 2, "failure_cause": "turn-limit"}), encoding="utf-8"
    )
    assert review.cause_for_exit_code(exit_code=2, review_directory=tmp_path) == "turn-limit"
    assert review.cause_for_exit_code(exit_code=0, review_directory=tmp_path) == "clean"
    assert review.cause_for_exit_code(exit_code=1, review_directory=tmp_path) == "blocking"
    assert review.cause_for_exit_code(exit_code=3, review_directory=tmp_path) == "preconditions"
    empty_directory = tmp_path / "empty"
    assert review.cause_for_exit_code(exit_code=2, review_directory=empty_directory) == "protocol"


def _mock_github_for_run_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, session_exit_code: int
) -> list[str]:
    """Mock the GitHub layer and the script runner; return the agent-review launch log."""
    monkeypatch.setattr(review, "load_github_token", lambda: "token")
    monkeypatch.setattr(review, "resolve_repository_slug", lambda: "o/r")
    monkeypatch.setattr(
        review,
        "run_git",
        lambda *, arguments: HEAD_SHA + "\n" if arguments[:1] == ["rev-parse"] else "",
    )
    monkeypatch.setattr(review, "REVIEW_DIRECTORY", tmp_path)
    launches: list[str] = []

    def fake_run_script(*, command: list[str]) -> int:
        if "review-bundle" in command:
            return _shared.EXIT_CODE_SUCCESS
        launches.append(" ".join(command))
        (tmp_path / "review-report.json").write_text(
            json.dumps({"exit_code": session_exit_code, "failure_cause": "deadline"}),
            encoding="utf-8",
        )
        return session_exit_code

    monkeypatch.setattr(review, "run_script", fake_run_script)
    pull_request = {
        "number": 64,
        "head": {"sha": HEAD_SHA, "ref": "feat/x"},
        "base": {"ref": "daily"},
    }
    monkeypatch.setattr(_github, "fetch_pull_request", lambda **kwargs: pull_request)
    monkeypatch.setattr(_github, "fetch_check_runs", lambda **kwargs: green_check_runs())
    monkeypatch.setattr(
        _github,
        "open_github_client",
        lambda *, token: httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200))
        ),
    )
    return launches


def test_run_review_two_failures_escalate_then_third_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """P61 end to end: two failed sessions -> escalation journaled, third run refused (exit 3)."""
    monkeypatch.delenv(review.MAX_ATTEMPTS_VARIABLE, raising=False)
    launches = _mock_github_for_run_review(monkeypatch, tmp_path, session_exit_code=2)
    escalations: list[dict[str, object]] = []
    monkeypatch.setattr(
        review,
        "escalate_to_human",
        lambda **kwargs: escalations.append(kwargs),
    )
    arguments = parse_review_arguments(["review", "--pr-number", "64"])

    assert review.run_review(arguments=arguments) == _shared.EXIT_CODE_PROTOCOL_FAILURE
    assert len(escalations) == 0
    assert review.run_review(arguments=arguments) == _shared.EXIT_CODE_PROTOCOL_FAILURE
    assert len(escalations) == 1
    assert escalations[0]["failed_attempts"] == 2
    assert review.run_review(arguments=arguments) == review.EXIT_CODE_PRECONDITIONS_UNMET
    assert len(escalations) == 1
    assert len(launches) == 2  # the third run never launched a session

    journal = tmp_path / review.ATTEMPTS_JOURNAL_FILE_NAME
    entries = [
        json.loads(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    session_entries = [
        entry for entry in entries if entry["phase"] == review.AttemptPhase.SESSION.value
    ]
    assert len(session_entries) == 2
    assert session_entries[0]["cause"] == "deadline"
    assert session_entries[0]["escalated"] is False
    assert session_entries[1]["escalated"] is True
    assert any(entry["phase"] == "cap" for entry in entries)


def test_run_review_cap_is_configurable_and_resets_on_new_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """P61: REVIEWER_MAX_ATTEMPTS=1 escalates on the first failure, the second run is refused."""
    monkeypatch.setenv(review.MAX_ATTEMPTS_VARIABLE, "1")
    launches = _mock_github_for_run_review(monkeypatch, tmp_path, session_exit_code=2)
    escalations: list[dict[str, object]] = []
    monkeypatch.setattr(review, "escalate_to_human", lambda **kwargs: escalations.append(kwargs))
    arguments = parse_review_arguments(["review", "--pr-number", "64"])
    assert review.run_review(arguments=arguments) == _shared.EXIT_CODE_PROTOCOL_FAILURE
    assert len(escalations) == 1
    assert review.run_review(arguments=arguments) == review.EXIT_CODE_PRECONDITIONS_UNMET
    assert len(launches) == 1


def test_measure_bundle_sizes_reports_total_and_diff_part(tmp_path: Path) -> None:
    """P62: the journaled sizes are the bundle total and the DIFF UNDER REVIEW part."""
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        "## ROLE CONTRACT\n\nabc\n\n---\n\n## DIFF UNDER REVIEW\n\ndiff --git a/x b/x\n",
        encoding="utf-8",
    )
    total, diff_part = review.measure_bundle_sizes(review_directory=tmp_path)
    assert total == len(bundle.read_bytes())
    assert diff_part == len(b"\n\ndiff --git a/x b/x\n")
    assert review.measure_bundle_sizes(review_directory=tmp_path / "empty") == (0, 0)
