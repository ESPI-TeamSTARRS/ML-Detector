import subprocess
from typing import Any

import pytest

from scripts.hooks import gitleaks_push
from scripts.hooks.gitleaks_push import main, scan_range

ZEROS = "0" * 40


def test_push_to_main_scans_exactly_the_pushed_range() -> None:
    assert scan_range("bbb", "aaa", "refs/heads/main") == "aaa..bbb"


def test_new_main_branch_without_previous_commit_scans_what_it_adds_to_origin_main() -> None:
    assert scan_range("bbb", ZEROS, "refs/heads/main") == "origin/main..bbb"
    assert scan_range("bbb", "", "refs/heads/main") == "origin/main..bbb"


def test_other_branches_scan_what_they_add_to_origin_main() -> None:
    assert scan_range("bbb", "aaa", "refs/heads/chore/hooks-git") == "origin/main..bbb"


@pytest.mark.parametrize("to_ref", ["", ZEROS])
def test_branch_deletion_has_nothing_to_scan(monkeypatch: pytest.MonkeyPatch, to_ref: str) -> None:
    monkeypatch.setenv("PRE_COMMIT_TO_REF", to_ref)
    assert main() == 0


def test_missing_binary_fails_with_install_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PRE_COMMIT_TO_REF", "bbb")
    monkeypatch.setattr(gitleaks_push.shutil, "which", lambda _name: None)
    assert main() == 1
    assert "gitleaks" in capsys.readouterr().err


def test_runs_gitleaks_on_the_push_range_and_returns_its_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PRE_COMMIT_TO_REF", "bbb")
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", "aaa")
    monkeypatch.setenv("PRE_COMMIT_REMOTE_BRANCH", "refs/heads/main")
    monkeypatch.setattr(gitleaks_push.shutil, "which", lambda _name: "/usr/bin/gitleaks")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(gitleaks_push.subprocess, "run", fake_run)
    assert main() == 7  # le code de sortie de gitleaks remonte tel quel (secret trouvé = échec)
    assert calls == [["/usr/bin/gitleaks", "git", "--redact", "-v", "--log-opts=aaa..bbb", "."]]
    assert "gitleaks sur aaa..bbb" in capsys.readouterr().out
