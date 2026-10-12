"""The run's first issue: the task itself, titled from its objective.

The title comes from the objective and never from a fixed phrase that would name one kind of
task; its description is rendered from the `bootstrap_description` template by the board owner.
"""

import re

BOOTSTRAP_TEMPLATE = "bootstrap_description"

_TITLE_LIMIT = 80
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LABEL = re.compile(r"^(?:objective|goal|task)\s*:[\s*_]*", re.IGNORECASE)


def _plain_lines(objective: str) -> list[str]:
    """The objective's non-blank lines with heading and emphasis markers removed.

    A leading label such as "Objective:" is dropped too. A line that is only a label leaves
    nothing and is skipped, so the title comes from the first line that carries words.
    """
    plain: list[str] = []
    for raw in objective.splitlines():
        text = " ".join(_BOLD.sub(r"\1", raw).split())
        text = _LABEL.sub("", text.lstrip("#*_ "), count=1).strip("*_ ")
        if text:
            plain.append(text)
    return plain


def bootstrap_title(objective: str) -> str:
    """The first issue's title: the objective's first sentence, as plain text on one line.

    Markdown headings, emphasis and a leading "Objective:" label are removed. When nothing is
    left, the title is the objective's first non-blank line as written. A blank objective has
    no title.
    """
    plain = _plain_lines(objective)
    if plain:
        first = plain[0]
    else:
        lines = [" ".join(line.split()) for line in objective.splitlines()]
        first = next((line for line in lines if line), "")
    sentence = re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0]
    if len(sentence) <= _TITLE_LIMIT:
        return sentence
    return sentence[: _TITLE_LIMIT - 3].rstrip() + "..."
