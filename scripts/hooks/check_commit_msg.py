"""Valide le message de commit : Conventional Commit.

Appelé par le hook git `commit-msg` (via pre-commit), qui reçoit le chemin du fichier de message.
Format : <type>(<scope>)?!?: <description>
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

TYPES = "feat|fix|docs|chore|refactor|test|ci|build|perf|style|revert"
SUBJECT_RE = re.compile(rf"^(?:{TYPES})(?:\([^)]+\))?!?: .+")
EXEMPT_PREFIXES = ("Merge ", "Revert ", "fixup!", "squash!")


def first_subject_line(raw: str) -> str:
    """Première ligne utile du message : ni vide, ni commentaire de git."""
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def is_valid(subject: str) -> bool:
    return subject.startswith(EXEMPT_PREFIXES) or bool(SUBJECT_RE.match(subject))


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("check_commit_msg: chemin du message manquant.", file=sys.stderr)
        return 1
    subject = first_subject_line(Path(args[0]).read_text(encoding="utf-8"))
    if is_valid(subject):
        return 0
    print(
        "\nMessage de commit non conforme (Conventional Commit).\n"
        f"   Reçu : {subject!r}\n"
        "   Attendu : <type>(<scope>)?: <description>\n"
        f"   Types : {TYPES.replace('|', ', ')}\n"
        "   Exemple : feat(detecteur): ajoute la règle silence\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
