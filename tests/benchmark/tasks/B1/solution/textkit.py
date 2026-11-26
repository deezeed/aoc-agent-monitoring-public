"""Small text helpers."""
import re
import unicodedata


def word_count(text):
    """Number of whitespace-separated words."""
    return len(text.split())


def truncate(text, width, suffix="..."):
    """text cut to `width` characters, the cut marked with `suffix`."""
    if len(text) <= width:
        return text
    return text[:max(0, width - len(suffix))] + suffix


def slugify(text, max_len=None):
    """URL-safe lowercase ASCII slug of `text`."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if max_len is not None:
        slug = slug[:max_len].rstrip("-")
    return slug
