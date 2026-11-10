"""Find sentences about each fund in a timestamped transcript ("[m:ss] text" lines).

Used by tools/ (to build the seed) and by the weekly video job (to tag new videos)."""
from __future__ import annotations

import re
from typing import Iterable, Optional

LINE = re.compile(r"^\[(\d+):(\d{2})(?::(\d{2}))?\]\s*(.*)$")
EVAL_WORDS = re.compile(
    r"\b(love|like|favorite|favourite|best|king|great|good|bad|terrible|atrocious|sell|sold|buy|bought|hold|holding|add|adding|"
    r"cut|total return|beat|beating|outperform|underperform|avoid|swap|obsolete|recommend|consider|pick|core|worst|surpris\w*)\b", re.I)


def parse(text: str) -> tuple[str, list[tuple[int, int]]]:
    """-> (plain text, [(char_offset, seconds)]) so that any offset can be mapped back to a timestamp."""
    parts, marks, pos = [], [], 0
    for ln in text.splitlines():
        m = LINE.match(ln.strip())
        if m:
            h = int(m.group(3)) if m.group(3) else 0
            secs = (int(m.group(1)) * 60 + int(m.group(2))) if not m.group(3) else int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            body = m.group(4)
        else:
            body, secs = ln.strip(), None
        if not body:
            continue
        if secs is not None:
            marks.append((pos, secs))
        parts.append(body)
        pos += len(body) + 1
    return " ".join(parts), marks


def secs_at(marks: list[tuple[int, int]], offset: int) -> Optional[int]:
    best = None
    for p, s in marks:
        if p <= offset:
            best = s
        else:
            break
    return best


def fmt_t(s: Optional[int]) -> str:
    if s is None:
        return ""
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def sentences(text: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for m in re.finditer(r"[^.!?]+[.!?]?", text):
        raw = m.group(0)
        s = raw.strip()
        if s:
            out.append((m.start() + len(raw) - len(raw.lstrip()), s))
    return out


# tickers that are also ordinary words: only match them when written in capitals ("BANK", not "bank")
CASE_SENSITIVE = {"BANK", "EASY", "BASE", "REIT", "DIV", "HD", "HIF", "ECI", "OVL", "BK", "AIPI", "GIF"}


def token_regex(names: Iterable[str]) -> re.Pattern:
    """Word-boundary match; ordinary-word tickers (CASE_SENSITIVE) must be upper-case, the rest ignore case."""
    names = [n for n in names if n]
    ci = sorted({re.escape(n) for n in names if n.upper() not in CASE_SENSITIVE}, key=len, reverse=True)
    cs = sorted({re.escape(n) for n in names if n.upper() in CASE_SENSITIVE}, key=len, reverse=True)
    parts = []
    if ci:
        parts.append(r"(?i:" + "|".join(ci) + r")")
    if cs:
        parts.append("|".join(cs))
    return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(parts) + r")(?![A-Za-z0-9])")


def find_mentions(text: str, ticker_names: dict[str, list[str]], max_snippets: int = 3, max_len: int = 300) -> dict[str, dict]:
    """ticker_names: {ticker: [spoken names/aliases]} -> {ticker: {n, snippets:[{t, snippet}]}}.

    Snippets are the sentences with the most evaluative words (ties: earliest)."""
    plain, marks = parse(text)
    sents = sentences(plain)
    out: dict[str, dict] = {}
    for tk, names in ticker_names.items():
        rx = token_regex(names)
        hits = [(off, s) for off, s in sents if rx.search(s)]
        if not hits:
            continue
        scored = sorted(hits, key=lambda h: (-len(EVAL_WORDS.findall(h[1])), h[0]))[:max_snippets]
        snips = [{"t": secs_at(marks, off), "snippet": (s[:max_len] + "…") if len(s) > max_len else s} for off, s in sorted(scored)]
        out[tk] = {"n": len(hits), "snippets": snips}
    return out
