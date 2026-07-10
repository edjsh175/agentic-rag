from scripts.check_repo_hygiene import find_forbidden_paths


def test_forbidden_paths_are_reported():
    lines = ["?? NUL", "?? output.tmp", "?? result_debug.json"]
    assert find_forbidden_paths(lines) == ["NUL", "output.tmp", "result_debug.json"]


def test_current_tracked_agent_files_are_not_forbidden():
    lines = [" M AGENTS.md", " M .agents/skills/code-simplifier/SKILL.md"]
    assert find_forbidden_paths(lines) == []
