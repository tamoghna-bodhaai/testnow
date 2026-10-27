"""Short, complete caption cues for one narrated beat."""
import textwrap


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
