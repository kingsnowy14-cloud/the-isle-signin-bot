"""Small helpers for deciding when the community AI should answer."""

import re

_QUESTION_WORDS = re.compile(
    r"\b(who|what|when|where|why|how|which|can|could|would|will|is|are|"
    r"do|does|did|has|have|should|was|were|"
    r"wer|was|wann|wo|warum|wieso|wie|welche|welcher|welches|"
    r"kann|könnt|könnte|ist|sind|habt|hat|hast|braucht|"
    r"por|que|qué|quien|quién|cuando|cuándo|donde|dónde|"
    r"pourquoi|comment|qui|quand|où|"
    r"perché|come|chi|quando|dove|"
    r"как|что|где|когда|почему|"
    r"どうして|なに|何|どこ|いつ)\b",
    re.IGNORECASE,
)


def is_question_message(content: str, *, has_attachments: bool = False) -> bool:
    """Return True for likely questions, including common non-English forms."""
    text = (content or "").strip()
    if not text:
        return bool(has_attachments)
    if text.startswith(("!", ".", "/")):
        return False
    if "?" in text or "？" in text:
        return True
    if _QUESTION_WORDS.search(text):
        return True
    if has_attachments and re.search(
        r"\b(help|problem|issue|error|hilfe|fehler|bug|ayuda|aide|aiuto)\b",
        text,
        re.IGNORECASE,
    ):
        return True
    return False
