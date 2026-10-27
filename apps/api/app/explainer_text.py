"""Helpers for mixed prose and delimited mathematical expressions."""
import re

# Match only paired delimiters, preserving ordinary currency and prose.
_MATH = re.compile(r'(?<!\\)(\$\$|\$|\\\(|\\\[)(.+?)(?<!\\)(\1|\\\)|\\\])', re.S)
_CLOSERS = {'$': '$', '$$': '$$', r'\(': r'\)', r'\[': r'\]'}


def split_math(text):
    """Return (is_math, content) segments for the supported delimiters."""
    parts, cursor = [], 0
    for match in _MATH.finditer(text):
        if match.group(3) != _CLOSERS[match.group(1)]:
            continue
        if match.start() > cursor:
            parts.append((False, text[cursor:match.start()]))
        parts.append((True, match.group(2)))
        cursor = match.end()
    if cursor < len(text):
        parts.append((False, text[cursor:]))
    return parts


def narration_text(text):
    """Turn common TeX notation into readable plain narration and captions."""
    text = ''.join(content if is_math else content for is_math, content in split_math(text))
    # Handle nested groups by repeatedly reducing the innermost commands.
    for _ in range(10):
        previous = text
        text = re.sub(r'\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}', r'(\1)/(\2)', text)
        text = re.sub(r'\\sqrt\s*\{([^{}]*)\}', r'square root of \1', text)
        text = re.sub(r'\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}', r'\1', text)
        if text == previous:
            break
    symbols = {'times': '×', 'cdot': '·', 'pm': '±', 'leq': '≤', 'geq': '≥',
               'neq': '≠', 'pi': 'π', 'alpha': 'α', 'beta': 'β', 'theta': 'θ',
               'Delta': 'Δ', 'infty': '∞', 'rightarrow': '→'}
    text = re.sub(r'\\([A-Za-z]+)', lambda m: symbols.get(m.group(1), m.group(1)), text)
    text = re.sub(r'\\[,;! ]', ' ', text)
    text = text.replace(r'\(', '').replace(r'\)', '').replace(r'\[', '').replace(r'\]', '')
    return text.replace('$', '').replace('{', '').replace('}', '').strip()
