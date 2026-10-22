from pathlib import Path

import pytest

from scripts.hooks.check_commit_msg import first_subject_line, is_valid, main


@pytest.mark.parametrize(
    "subject",
    [
        "feat: ajoute les rafales",
        "fix(detecteur): corrige le débit",
        "chore(ci): épingle les actions",
        "refactor!: renomme le port",
        "feat(api)!: change le contrat",
        "docs: met à jour le README",
        "Merge branch 'main' into chore/hooks",
        'Revert "feat: ajoute les rafales"',
        "fixup! feat: ajoute les rafales",
        "squash! fix: corrige",
    ],
)
def test_accepts_conventional_and_git_generated_subjects(subject: str) -> None:
    assert is_valid(subject)


@pytest.mark.parametrize(
    "subject",
    [
        "ajoute les rafales",
        "Feat: majuscule",
        "feat:sans espace",
        "feat: ",
        "feature: type inconnu",
        "feat(): portée vide",
        "",
    ],
)
def test_rejects_other_subjects(subject: str) -> None:
    assert not is_valid(subject)


def test_first_subject_line_skips_blank_lines_and_git_comments() -> None:
    raw = "\n# Please enter the commit message\n\n  feat: le vrai sujet  \n\ncorps\n"
    assert first_subject_line(raw) == "feat: le vrai sujet"
    assert first_subject_line("# seulement un commentaire\n") == ""


def test_main_accepts_a_valid_message_file(tmp_path: Path) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("fix(cli): corrige l'option\n\nExplication.\n", encoding="utf-8")
    assert main([str(message)]) == 0


def test_main_rejects_an_invalid_message_and_explains(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("n'importe quoi\n", encoding="utf-8")
    assert main([str(message)]) == 1
    erreur = capsys.readouterr().err
    assert "Conventional Commit" in erreur and "n'importe quoi" in erreur and "feat, fix" in erreur


def test_main_without_a_path_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 1
    assert "chemin du message manquant" in capsys.readouterr().err


def test_main_reads_the_path_from_the_command_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("feat: depuis sys.argv\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["check_commit_msg.py", str(message)])
    assert main() == 0
