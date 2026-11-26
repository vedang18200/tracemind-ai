"""Check whether a quoted passage really appears in a page's text.

Pure functions, no I/O, so the matching rules are easy to test. Used by the
`verify_citation` MCP tool.

Match levels, strictest first:
  exact_match       same characters, only whitespace differences
  normalized_match  same text after Unicode NFKC, case folding, and unifying
                    curly quotes / dashes / ellipses (typical copy-paste noise)
  close_match       not present, but a passage is >= CLOSE_THRESHOLD similar:
                    likely paraphrased, truncated or altered
  not_found         nothing similar enough
"""
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Literal

CLOSE_THRESHOLD = 0.75
CONTEXT_CHARS = 160

Status = Literal["exact_match", "normalized_match", "close_match", "not_found"]

_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    " ": " ",
})


def _norm_char(c: str, fold: bool) -> str:
    if not fold:
        return c
    c = unicodedata.normalize("NFKC", c).translate(_PUNCT)
    return c.casefold()


def _normalize_with_map(text: str, fold: bool) -> tuple[str, list[int]]:
    """Normalize text and keep, for each output char, the index of the source char.

    The map lets us report context from the ORIGINAL page text even when the match
    was found in the folded version.
    """
    out: list[str] = []
    idx: list[int] = []
    prev_space = True                     # also strips leading whitespace
    for i, ch in enumerate(text):
        for c in _norm_char(ch, fold):
            if c.isspace():
                if prev_space:
                    continue
                c, prev_space = " ", True
            else:
                prev_space = False
            out.append(c)
            idx.append(i)
    while out and out[-1] == " ":         # strip trailing whitespace
        out.pop()
        idx.pop()
    return "".join(out), idx


def _normalize(text: str, fold: bool) -> str:
    return _normalize_with_map(text, fold)[0]


def _context(original: str, start: int, end: int) -> str:
    lo, hi = max(0, start - CONTEXT_CHARS), min(len(original), end + CONTEXT_CHARS)
    snippet = re.sub(r"\s+", " ", original[lo:hi]).strip()
    return ("…" if lo > 0 else "") + snippet + ("…" if hi < len(original) else "")


def _find(page: str, quote: str, fold: bool) -> tuple[int, int] | None:
    """Return (start, end) in the ORIGINAL page text, or None."""
    norm_page, idx = _normalize_with_map(page, fold)
    norm_quote = _normalize(quote, fold)
    pos = norm_page.find(norm_quote)
    if pos < 0 or not norm_quote:
        return None
    return idx[pos], idx[pos + len(norm_quote) - 1] + 1


def _closest(page: str, quote: str) -> tuple[float, int, int]:
    """Best-matching passage by word overlap. Returns (similarity, start, end) in original text."""
    norm_page, idx = _normalize_with_map(page, fold=True)
    page_words = [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", norm_page)]
    quote_words = _normalize(quote, fold=True).split()
    if not page_words or not quote_words:
        return 0.0, 0, 0

    pw = [w for w, _, _ in page_words]
    anchor = SequenceMatcher(None, pw, quote_words, autojunk=False).find_longest_match(
        0, len(pw), 0, len(quote_words))
    if anchor.size == 0:
        return 0.0, 0, 0

    # Align a quote-sized window on the anchor, then nudge it a few words either way.
    base = anchor.a - anchor.b
    n = len(quote_words)
    best = (0.0, 0, 0)
    for shift in range(-3, 4):
        lo = max(0, min(base + shift, len(pw) - 1))
        hi = min(len(pw), lo + n)
        ratio = SequenceMatcher(None, pw[lo:hi], quote_words, autojunk=False).ratio()
        if ratio > best[0]:
            best = (ratio, lo, hi)
    ratio, lo, hi = best
    start = idx[page_words[lo][1]]
    end = idx[page_words[hi - 1][2] - 1] + 1
    return round(ratio, 3), start, end


def check_quote(page_text: str, quote: str) -> dict:
    """Classify how well `quote` is supported by `page_text`."""
    hit = _find(page_text, quote, fold=False)
    if hit:
        return {"status": "exact_match", "similarity": 1.0, "context": _context(page_text, *hit)}

    hit = _find(page_text, quote, fold=True)
    if hit:
        return {"status": "normalized_match", "similarity": 1.0, "context": _context(page_text, *hit)}

    ratio, start, end = _closest(page_text, quote)
    if ratio >= CLOSE_THRESHOLD:
        return {"status": "close_match", "similarity": ratio,
                "context": _context(page_text, start, end),
                "closest_passage": page_text[start:end]}
    return {"status": "not_found", "similarity": ratio,
            "context": _context(page_text, start, end) if ratio > 0 else ""}
