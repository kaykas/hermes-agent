"""search_files scope guards: literal-pattern detection and the same-turn retry cap.

Literal detection: models routinely pass code fragments (``obj.method()``,
``foo(bar)``, ``$HOME``, ``*args``) as content patterns. As regexes these either
fail to compile or silently match something else, so a punctuation-heavy pattern
with no regex construct is searched as a fixed string. Escaping (rather than an
engine flag) keeps the behaviour identical across rg, grep -E and remote backends.

Retry cap: the exact-repeat guard in ``search_tool`` is evaded by nudging the
path or glob. Unproductive searches (error or zero results) for the same
pattern+target+ordering+page are counted across one read/search streak — the
dispatcher's ``notify_other_tool_call`` ends a streak by clearing ``last_key`` —
and blocked once the cap is reached. A productive search clears the count.
"""

import re

SEARCH_RETRY_CAP = 3

_REGEX_META = frozenset(".^$*+?{}[]()|\\")
_PUNCT_LITERAL_RATIO = 0.2

# Constructs that only make sense in a regex the caller meant to write.
_REGEX_INTENT_RE = re.compile(
    r"\\."                      # any escape: \d \w \b \. \( ...
    r"|\.[*+?{]"                # .* .+ .? .{n}
    r"|\[\^|\[[^\]]*\w-\w[^\]]*\]|\][*+?{]"  # negated / ranged / quantified class
    r"|\(\?"                    # group flags, lookaround
    r"|\{\d+(?:,\d*)?\}"        # counted repetition
    r"|(?<![|])\|(?![|])"       # single alternation bar (``||`` is code, not regex)
    r"|^\^|\$$"                 # anchors in anchor position
    r"|\w[*+?](?![*+?])"        # quantified atom: colou?r, ab+c (not i++)
    r"|\)[*+?]"                 # quantified group
)


def looks_like_punctuation_literal(pattern: str) -> bool:
    """True when *pattern* reads as a literal code fragment rather than a regex:
    it has regex metacharacters, and either does not compile at all or has no
    regex construct and is punctuation-heavy."""
    if not any(c in _REGEX_META for c in pattern):
        return False
    try:
        re.compile(pattern)
    except re.error:
        return True
    if _REGEX_INTENT_RE.search(pattern):
        return False
    visible = [c for c in pattern if not c.isspace()]
    punct = sum(1 for c in visible if not (c.isalnum() or c == "_"))
    return punct / len(visible) >= _PUNCT_LITERAL_RATIO


def escape_literal_pattern(pattern: str) -> str:
    """Backslash-escape regex metacharacters only; every escape here is valid in
    Rust regex (rg) and POSIX ERE (grep -E) alike, unlike ``re.escape``."""
    return "".join("\\" + c if c in _REGEX_META else c for c in pattern)


def resolve_fixed_strings(pattern: str, target: str, fixed_strings) -> tuple[bool, bool]:
    """``(literal, auto)``: whether to search *pattern* literally, and whether that
    was inferred rather than requested. Filename globs are never touched."""
    if target != "content":
        return False, False
    if isinstance(fixed_strings, str):
        fixed_strings = fixed_strings.strip().lower() in {"true", "1", "yes"}
    if fixed_strings is not None:
        return bool(fixed_strings), False
    auto = looks_like_punctuation_literal(pattern)
    return auto, auto


def start_search_streak(task_data: dict) -> None:
    """Drop retry counts from an ended streak. Call with the tracker lock held,
    BEFORE ``_bump_consecutive`` (a cleared ``last_key`` marks a new streak)."""
    if task_data.get("last_key") is None:
        task_data.pop("search_retries", None)


def retry_count(task_data: dict, pattern: str, target: str,
                order: str = "discovery", limit: int = 50, offset: int = 0) -> int:
    """Unproductive searches for this query shape in the current streak."""
    return task_data.get("search_retries", {}).get((pattern, target, order, limit, offset), 0)


def record_search_outcome(task_data: dict, pattern: str, target: str, productive: bool,
                          order: str = "discovery", limit: int = 50, offset: int = 0) -> None:
    """Count an unproductive search, or clear the count after a productive one.
    Call with the tracker lock held. Ordering and pagination are part of the
    query: newest-first is not a discovery-order retry, and the next page is not
    a retry of the previous one.
    """
    retries = task_data.setdefault("search_retries", {})
    key = (pattern, target, order, limit, offset)
    if productive:
        retries.pop(key, None)
    else:
        retries[key] = retries.get(key, 0) + 1


def is_productive(result) -> bool:
    """Whether a search returned a hit without an error.

    Use attribute defaults for the lightweight SearchResult-compatible objects
    accepted by ``search_tool`` (including older third-party file operations).
    """
    return not getattr(result, "error", None) and bool(
        getattr(result, "total_count", 0) or getattr(result, "matches", None)
        or getattr(result, "files", None) or getattr(result, "counts", None))


def retry_cap_message(pattern: str, target: str, retries: int) -> str:
    return (
        f"BLOCKED: search_files for {pattern!r} (target={target!r}) has failed or "
        f"returned nothing {retries} times in a row across different paths or globs. "
        "Re-scoping the same pattern again will not help. Use what you "
        "already have, change the pattern itself, or take a different approach "
        "(read a known file, list a specific directory, or ask the user).")
