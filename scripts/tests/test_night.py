"""Pin the headless session command: readable text, never a JSON dump."""

from pathlib import Path

from scripts.night import SESSION_OUTPUT_FORMAT, build_session_command


def test_session_command_uses_text_output() -> None:
    """The session prints only its final answer - no whole-session JSON."""
    command = build_session_command(
        prompt_path=Path("session-1.md"),
        maximum_session_turns=100,
        session_time_limit_seconds=3600,
    )
    assert SESSION_OUTPUT_FORMAT == "text"
    output_index = command.index("--output")
    assert command[output_index + 1] == "text"
    assert "json" not in command
