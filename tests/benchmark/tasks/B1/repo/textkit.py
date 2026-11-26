"""Small text helpers."""


def word_count(text):
    """Number of whitespace-separated words."""
    return len(text.split())


def truncate(text, width, suffix="..."):
    """text cut to `width` characters, the cut marked with `suffix`."""
    if len(text) <= width:
        return text
    return text[:max(0, width - len(suffix))] + suffix
