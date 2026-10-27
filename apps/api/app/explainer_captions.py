"""Short, complete caption cues for one narrated beat."""
import textwrap
import re
import html


def caption_markup(text: str) -> str:
    """Format simple typed subscripts and exponents without treating prose as markup."""
    pattern = re.compile(r"(?<!\w)([A-Za-z])(?:_\{([A-Za-z0-9+-]+)\}|_([A-Za-z0-9]+))?(?:\^\{([A-Za-z0-9+-]+)\}|\^([A-Za-z0-9+-]+))?(?!\w)")
    parts, cursor = [], 0
    for match in pattern.finditer(text):
        sub, sup = match.group(2) or match.group(3), match.group(4) or match.group(5)
        if not sub and not sup:
            continue
        parts.append(html.escape(text[cursor:match.start()]))
        parts.append(html.escape(match.group(1)))
        if sub:
            parts.append(f'<sub>{html.escape(sub)}</sub>')
        if sup:
            parts.append(f'<sup>{html.escape(sup)}</sup>')
        cursor = match.end()
    parts.append(html.escape(text[cursor:]))
    return ''.join(parts)


def caption_cues(narration: str, duration: float) -> list[tuple[str, float]]:
    """Show one short spoken line at a time across the beat's audio duration."""
    if duration <= 0:
        raise ValueError("Caption duration must be positive")
    cues = textwrap.wrap(" ".join(narration.split()), width=45,
                         break_long_words=False, break_on_hyphens=False)
    if not cues:
        return []
    weights = [max(1, len(cue)) for cue in cues]
    total = sum(weights)
    lengths = [duration * weight / total for weight in weights[:-1]]
    lengths.append(duration - sum(lengths))
    return list(zip(cues, lengths))
