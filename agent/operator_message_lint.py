#!/usr/bin/env python3
"""Deterministic pre-send editorial checks for operator-facing messages.

The linter checks claim structure and supplied evidence; it does not send or edit
messages.  ``evidence`` may be a string or a mapping with ``text`` and/or
``observed_production`` string fields.  Findings are JSON-serializable dicts.
"""

from __future__ import annotations

import argparse
from email.utils import parseaddr
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


HEADING_STYLE = "heading_style"
UNSUPPORTED_STATISTIC = "unsupported_statistic"
ABSOLUTE_GUARANTEE = "absolute_guarantee"
ABSOLUTE_ISOLATION = "absolute_isolation"
CORPORATE_METAPHOR = "corporate_metaphor"
PRODUCTION_OVERCLAIM = "production_overclaim"
SCHEDULE_SCOPE = "schedule_scope"
SENDER_IDENTITY = "sender_identity"
SOURCE_COUNT = "source_count"
UNSUPPORTED_QUALIFIER = "unsupported_qualifier"
OPENING_FRAME = "opening_frame"

_PERCENT_RE = re.compile(
    r"(?P<qualifier>\b(?:over|more than|approximately|about|nearly|at least|under)\s+)?"
    r"(?P<value>\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-z]+(?:[-’'][A-Za-z]+)*|\d+(?:\.\d+)?")
_MINOR_HEADING_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in",
    "into", "of", "on", "or", "the", "to", "vs", "with",
}
_STOP_WORDS = _MINOR_HEADING_WORDS | {
    "about", "across", "after", "all", "are", "because", "been", "comes",
    "come", "during", "each", "has", "have", "is", "it", "its", "more",
    "over", "than", "that", "their", "them", "there", "these", "this",
    "through", "under", "was", "were", "will", "would",
}


def _finding(code: str, quote: str, fix: str) -> dict[str, str]:
    return {"code": code, "quote": quote, "fix": fix}


def _evidence_text(evidence: Any) -> str:
    if evidence is None:
        return ""
    if isinstance(evidence, str):
        return evidence
    if isinstance(evidence, dict):
        parts = []
        for key in ("text", "observed_production"):
            value = evidence.get(key)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"evidence.{key} must be a string")
            if value:
                parts.append(value)
        unknown = set(evidence) - {"text", "observed_production"}
        if unknown:
            raise TypeError(f"unsupported evidence fields: {sorted(unknown)!r}")
        return "\n".join(parts)
    raise TypeError("evidence must be None, a string, or a mapping")


def _line_offsets(text: str) -> Iterable[tuple[int, str]]:
    offset = 0
    for line in text.splitlines(keepends=True):
        yield offset, line.rstrip("\r\n")
        offset += len(line)


def _visible_heading(raw: str) -> tuple[str, str] | None:
    """Return (exact source quote, normalized heading) for standalone headings."""
    stripped = raw.strip()
    if not stripped or stripped.startswith(("- ", "* ", ">")):
        # Numbered bold headings are handled below; bullets are labels, not headings.
        return None
    if re.fullmatch(r"```.*|~~~.*", stripped):
        return None

    match = re.match(r"^#{1,6}\s+(.+?)\s*#*$", stripped)
    if match:
        body = match.group(1).strip()
        body = re.sub(r"^\d+[.)]\s+", "", body)
        return body, _strip_inline_markup(body)

    match = re.match(r"^\d+[.)]\s+(\*\*|__)(.+?)\1\s*:?\s*$", stripped)
    if match:
        return match.group(2).strip(), _strip_inline_markup(match.group(2))

    # Slack messages often use :emoji: ***Heading*** as a section heading.
    match = re.match(
        r"^(?::[a-zA-Z0-9_+-]+:\s*)?(?:\*{3}|_{3})(.+?)(?:\*{3}|_{3})\s*$",
        stripped,
    )
    if match:
        return match.group(1).strip(), _strip_inline_markup(match.group(1))
    return None


def _strip_inline_markup(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[`*_~]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _protected_literal(text: str) -> bool:
    stripped = text.strip()
    return (
        (len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "\"'“”")
        or (stripped.startswith("`") and stripped.endswith("`"))
    )


def _heading_violation(heading: str) -> bool:
    if not heading or _protected_literal(heading):
        return False
    words = _WORD_RE.findall(heading)
    lexical = [w for w in words if re.search(r"[A-Za-z]", w)]
    if len(lexical) < 2:
        return False

    letters = re.sub(r"[^A-Za-z]", "", "".join(lexical))
    if len(lexical) >= 2 and letters and letters.isupper():
        return True

    significant = []
    for word in lexical:
        # Acronyms, initialisms, and camelCase technical identifiers do not
        # establish decorative Title Case on their own.
        if word.isupper() or (any(c.isupper() for c in word[1:]) and "-" not in word):
            continue
        if word.lower() in _MINOR_HEADING_WORDS:
            continue
        significant.append(word)
    if len(significant) < 2:
        return False
    # Hyphenated Title Case components (Single-Source) count explicitly.
    capitalized = sum(
        all(part[:1].isupper() for part in word.split("-") if part)
        for word in significant
    )
    return capitalized == len(significant)


def _lint_headings(text: str) -> list[dict[str, str]]:
    findings = []
    in_fence = False
    for _, line in _line_offsets(text):
        if line.strip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        parsed = _visible_heading(line)
        if not parsed:
            continue
        quote, normalized = parsed
        if _heading_violation(normalized):
            findings.append(_finding(
                HEADING_STYLE,
                quote,
                "Use a literal sentence-case heading that names the subject or supported finding.",
            ))
    return findings


def _sentence_spans(text: str) -> Iterable[tuple[int, str]]:
    """Yield approximate prose sentences while retaining exact source substrings."""
    # Newlines are retained in quotes. Terminal punctuation followed by whitespace
    # is a boundary; decimal points and common deployment IDs are left alone.
    start = 0
    for match in re.finditer(r"[.!?](?=\s|$)|\n{2,}", text):
        end = match.end()
        candidate = text[start:end].strip()
        if candidate:
            leading = start + len(text[start:end]) - len(text[start:end].lstrip())
            yield leading, candidate
        start = end
    if start < len(text):
        candidate = text[start:].strip()
        if candidate:
            leading = start + len(text[start:]) - len(text[start:].lstrip())
            yield leading, candidate


def _paragraph_for(text: str, start: int, end: int) -> str:
    left = text.rfind("\n\n", 0, start)
    right = text.find("\n\n", end)
    return text[0 if left < 0 else left + 2:len(text) if right < 0 else right]


def _is_exact_or_legal_quote(sentence: str) -> bool:
    if not re.search(r"[\"“].+[\"”]", sentence, re.DOTALL):
        return False
    if re.search(r"\b(?:rewrite|draft|suggested wording|option \d+)\b", sentence, re.IGNORECASE):
        return False
    return bool(re.search(
        r"\b(?:according to|agreement|contract|court|exact quote|legal|license|"
        r"quoted|states|technical definition|verbatim|wrote|said)\b",
        sentence,
        re.IGNORECASE,
    ))


def _inside_code_span(text: str, position: int) -> bool:
    # Inline code is protected; fenced blocks are removed before claim checks.
    line_start = text.rfind("\n", 0, position) + 1
    prefix = text[line_start:position]
    return prefix.count("`") % 2 == 1


def _prose_without_fences(text: str) -> str:
    return re.sub(r"(?ms)^```.*?^```\s*|^~~~.*?^~~~\s*", "", text)


def _coordinating_clause(text: str, position: int) -> str:
    start = 0
    end = len(text)
    for delimiter in re.finditer(r";|\b(?:and|but)\b", text, re.IGNORECASE):
        if delimiter.end() <= position:
            start = delimiter.end()
        elif delimiter.start() > position:
            end = delimiter.start()
            break
    return text[start:end]


def _numeric_ratio_support(sentence: str, value: float) -> bool:
    # A ratio supports the claim only when it evaluates to the stated percentage
    # and belongs to the same subject clause.
    percent = re.search(rf"(?<![\d.]){re.escape(f'{value:g}')}(?:\.0+)?\s*%", sentence)
    if not percent:
        return False
    percent_clause = _coordinating_clause(sentence, percent.start())
    percent_terms = {
        w.casefold() for w in _WORD_RE.findall(percent_clause)
        if len(w) > 3 and w.casefold() not in _STOP_WORDS
    }
    for match in re.finditer(r"(?<![\d.])(\d[\d,]*)\s*/\s*(\d[\d,]*)(?![\d.])", sentence):
        numerator = float(match.group(1).replace(",", ""))
        denominator = float(match.group(2).replace(",", ""))
        if not denominator or abs(100 * numerator / denominator - value) >= 0.051:
            continue
        ratio_clause = _coordinating_clause(sentence, match.start())
        ratio_terms = {
            w.casefold() for w in _WORD_RE.findall(ratio_clause)
            if len(w) > 3 and w.casefold() not in _STOP_WORDS
        }
        if ratio_terms & percent_terms:
            return True
    return False


def _external_stat_support(sentence: str, value: float, evidence: str) -> bool:
    if not evidence:
        return False
    claim_terms = {
        w.casefold() for w in _WORD_RE.findall(sentence)
        if len(w) > 3 and w.casefold() not in _STOP_WORDS and not w[0].isdigit()
    }
    for _, evidence_sentence in _sentence_spans(evidence):
        numeric_match = re.search(
            rf"(?<![\d.]){re.escape(f'{value:g}')}(?:\.0+)?\s*%",
            evidence_sentence,
        ) or re.search(rf"(?<![\d.]){re.escape(f'{value:g}')}\s*/\s*100(?!\d)", evidence_sentence)
        if not numeric_match:
            continue
        evidence_terms = {
            w.casefold() for w in _WORD_RE.findall(evidence_sentence)
            if len(w) > 3 and w.casefold() not in _STOP_WORDS and not w[0].isdigit()
        }
        if claim_terms and claim_terms <= evidence_terms:
            return True
    return False


def _non_statistical_percent(sentence: str, match: re.Match[str]) -> bool:
    qualifier = (match.group("qualifier") or "").lower()
    if qualifier in {"at least", "approximately", "about", "nearly"} and re.search(
        r"\b(?:target|goal|budget|threshold|assumption|growth|minimum|plan)\b",
        sentence,
        re.IGNORECASE,
    ):
        return True
    return bool(re.search(
        r"\b(?:target|goal|budget|threshold|rubric ceiling|growth assumption|"
        r"minimum|maximum|cap of|discount of|downsell|offer|forecast|planning)\b",
        sentence,
        re.IGNORECASE,
    ))


def _has_inline_stat_source(text: str, sentence: str) -> bool:
    # Meeting measurements and explicitly attributed operational snapshots are
    # evidence for observations, but never for a new population-level cause.
    if re.search(
        r"\b(?:caused by|comes from|due to|driven by|turnover|of all|across all|"
        r"population|marketwide|industrywide)\b",
        sentence,
        re.IGNORECASE,
    ):
        return False
    if re.search(
        r"\b(?:measured|word count|words|airtime|lagging|readback|collected|scorecard)\b",
        sentence,
        re.IGNORECASE,
    ):
        return True
    return bool(re.search(r"\bSlack-reported\b|\bVerified via SQLite audit\b", text, re.IGNORECASE))


def _lint_statistics(text: str, evidence: str) -> list[dict[str, str]]:
    findings = []
    for _, sentence in _sentence_spans(text):
        if _is_exact_or_legal_quote(sentence):
            continue
        for match in _PERCENT_RE.finditer(sentence):
            absolute = text.find(sentence)  # only used as a conservative code-span guard
            if absolute >= 0 and _inside_code_span(text, absolute + match.start()):
                continue
            value = float(match.group("value"))
            paragraph = _paragraph_for(text, text.find(sentence), text.find(sentence) + len(sentence))
            if re.search(
                r"(?:local profile isolation|profile isolation|firewall|zero access|"
                r"100%\s+intact)",
                paragraph,
                re.IGNORECASE,
            ):
                # Isolation/firewall claims receive their more specific check below.
                if re.search(r"(?:100%|zero access)", match.group(0), re.IGNORECASE):
                    continue
            if _non_statistical_percent(sentence, match):
                continue
            if _numeric_ratio_support(sentence, value):
                continue
            if _external_stat_support(sentence, value, evidence):
                continue
            if _has_inline_stat_source(text, sentence):
                continue
            findings.append(_finding(
                UNSUPPORTED_STATISTIC,
                sentence,
                "Attach the source, cohort, denominator, and period, or remove the unsupported statistic.",
            ))
            break  # one representative unsupported statistic per sentence
    return findings


def _lint_absolute_claims(text: str) -> list[dict[str, str]]:
    findings = []
    guarantee_done = False
    isolation_done = False
    for _, sentence in _sentence_spans(text):
        if _is_exact_or_legal_quote(sentence):
            continue
        has_never_promise = bool(re.search(
            r"\b(?:will|would|shall|must)\s+never\s+\w+|"
            r"\bwon['’]t\s+(?:ever\s+)?(?:see|receive|get)\b",
            sentence,
            re.IGNORECASE,
        ))
        has_guarantee_word = bool(re.search(r"\bguarantee(?:s|d)?\b", sentence, re.IGNORECASE))
        negated_guarantee = bool(re.search(
            r"\b(?:does not|doesn['’]t|cannot|can['’]t|not)\s+"
            r"(?:\w+\s+){0,4}guarantee(?:s|d)?\b",
            sentence,
            re.IGNORECASE,
        ))
        if not guarantee_done and (has_never_promise or (has_guarantee_word and not negated_guarantee)):
            # Descriptions of past absence are scoped observations, not promises.
            if not re.search(r"\b(?:never happened|never occurred|never arrived)\b", sentence, re.IGNORECASE):
                findings.append(_finding(
                    ABSOLUTE_GUARANTEE,
                    sentence,
                    "Limit the claim to the tested behavior and scope; do not promise permanent future behavior.",
                ))
                guarantee_done = True
        if not isolation_done and re.search(
            r"100%\s+(?:local\s+)?profile isolation|\bzero access\s+to\b|"
            r"\b(?:completely|fully)\s+(?:isolated|air-gapped)\b|\bno access ever\b",
            sentence,
            re.IGNORECASE,
        ):
            if not re.search(
                r"\b(?:no recorded|no observed|0 recorded|zero recorded)\s+(?:\w+\s+){0,3}"
                r"(?:access|reads|writes|queries)\b[^.!?\n]*\b(?:in|within|during|across)\s+"
                r"(?:this|the|last|trailing|an|our)?\s*(?:audit|window|scope|sample|\d)",
                sentence,
                re.IGNORECASE,
            ):
                findings.append(_finding(
                    ABSOLUTE_ISOLATION,
                    sentence,
                    "State only recorded access within the audited systems, rows, and time window.",
                ))
                isolation_done = True
    return findings


def _lint_metaphor(text: str) -> list[dict[str, str]]:
    findings = []
    for _, sentence in _sentence_spans(text):
        if _is_exact_or_legal_quote(sentence):
            continue
        if re.search(r"\bwhere you lost altitude\b", sentence, re.IGNORECASE):
            findings.append(_finding(
                CORPORATE_METAPHOR,
                sentence,
                "Use a literal heading such as 'Coaching gaps and rewrites'.",
            ))
            continue
        # Require both a figurative business/aviation frame and a leadership or
        # operating role; technical altitude/runway usage remains literal.
        if re.search(
            r"\b(?:low|high)-altitude\s+(?:\w+[-\s]+){0,3}(?:posture|leadership|advisory)|"
            r"\bhelicopter (?:view|perspective)\b|\bnorth star\b|\bmove the needle\b",
            sentence,
            re.IGNORECASE,
        ) and re.search(
            r"\b(?:advisory|leader|leadership|executive|enterprise|operator|"
            r"strategy|strategic|posture|business|commercial)\b",
            sentence,
            re.IGNORECASE,
        ):
            findings.append(_finding(
                CORPORATE_METAPHOR,
                sentence,
                "Replace the metaphor with the observed behavior and its operational effect.",
            ))
    return findings


def _has_observed_production_evidence(text: str, evidence: str) -> bool:
    if not evidence:
        return False
    patterns = [
        r"\bobserved in production\b",
        r"\bproduction\s+(?:run|alert|delivery|notification|endpoint|page|deployment)\b"
        r"[^.\n]{0,100}\b(?:arrived|received|returned|responded|succeeded|passed|"
        r"confirmed|observed|HTTP\s*200|READY)\b",
        r"\b(?:alert|notification)\b[^.\n]{0,100}\b(?:received|arrived)\b"
        r"[^.\n]{0,100}\bproduction\b",
        r"\bverified live\b[^.\n]{0,160}\b(?:https?://|HTTP\s*\d|READY)\b",
        r"\bconfirmed live\b[^.\n]{0,160}\b(?:https?://|HTTP\s*\d|READY)\b",
        r"\bVercel\b[^.\n]{0,200}\bREADY\b[^.\n]{0,100}\baliasAssigned\s*[:=]\s*true\b",
        r"\bHTTP\s*200\b[^.\n]{0,100}\b(?:live|production)\b",
    ]
    identifiers = re.findall(
        r"\b20\d{6}-[a-z0-9-]+\b|"
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|"
        r"\b[0-9a-f]{7,40}\b",
        text, re.IGNORECASE,
    )
    ignored = _STOP_WORDS | {
        "implemented", "deployed", "rolled", "shipped", "verified", "verification",
        "production", "live", "three", "proactive", "currently", "contract",
    }
    terms = {
        word.casefold() for word in _WORD_RE.findall(text)
        if len(word) > 4 and word.casefold() not in ignored
    }
    for _, evidence_sentence in _sentence_spans(evidence):
        if re.search(
            r"\b(?:failed|failure|unsuccessful|never fired|no safeguards\b|"
            r"no\s+(?:\w+\s+){0,3}observed\b|not observed\b|"
            r"(?:did not|didn['’]t|was not|were not)\s+(?:\w+\s+){0,3}(?:fire|fired|deploy|succeed|pass|complete))",
            evidence_sentence, re.IGNORECASE,
        ):
            continue
        if not any(re.search(pattern, evidence_sentence, re.IGNORECASE) for pattern in patterns):
            continue
        if identifiers:
            if any(identifier in evidence_sentence for identifier in identifiers):
                return True
            continue
        evidence_fold = evidence_sentence.casefold()
        if any(term in evidence_fold for term in terms):
            return True
    return False


def _lint_production_overclaim(text: str, evidence: str) -> list[dict[str, str]]:
    findings = []
    for _, sentence in _sentence_spans(text):
        if _is_exact_or_legal_quote(sentence):
            continue
        normalized = re.sub(r"[*_`]", "", sentence)
        has_verified = bool(re.search(r"\bverif(?:ied|y|ication)\b", normalized, re.IGNORECASE))
        has_implementation = bool(re.search(
            r"\b(?:implemented|deployed|rolled out|shipped|production|live)\b",
            normalized,
            re.IGNORECASE,
        ))
        # A single implementation or deployment statement is not itself an
        # overclaim. The gated structure is implementation + unqualified verification.
        if not (has_verified and has_implementation):
            continue
        if re.search(r"\b(?:not|isn['’]t|aren['’]t|without)\s+(?:\w+\s+){0,2}verified\b", normalized, re.IGNORECASE):
            continue
        if _has_observed_production_evidence(sentence, evidence):
            continue
        findings.append(_finding(
            PRODUCTION_OVERCLAIM,
            sentence,
            "Separate configured/installed and dry-run results from behavior observed in production.",
        ))
    return findings


def _lint_schedule_scope(text: str, evidence: str) -> list[dict[str, str]]:
    if not evidence or not re.search(r"30 9 \* \* 2-5", evidence):
        return []
    findings = []
    occurrences = list(re.finditer(r"\bforecast ticker\b", text, re.IGNORECASE))
    for index, occurrence in enumerate(occurrences):
        end = min(
            occurrences[index + 1].start() if index + 1 < len(occurrences) else len(text),
            occurrence.start() + 180,
        )
        segment = text[occurrence.start():end]
        match = re.search(r"Monday(?:-|–)Friday", segment, re.IGNORECASE)
        if not match:
            continue
        clause_start = segment.rfind(";", 0, match.start()) + 1
        monday_clause = segment[clause_start:]
        if not re.search(r"\bforecast ticker\b", monday_clause, re.IGNORECASE):
            continue
        if re.search(
            r"\b(?:does not|doesn't|never|not)\s+(?:run|runs|running)\b",
            segment, re.IGNORECASE,
        ):
            continue
        findings.append(_finding(
            SCHEDULE_SCOPE, segment[:match.end()],
            "Separate the Monday recap from the Tuesday–Friday ticker, or change the verified schedule.",
        ))
    return findings


def _lint_sender_identity(text: str, evidence: str) -> list[dict[str, str]]:
    sender_names = re.findall(
        r"\b([A-Z][a-z]+)\s+(?:just\s+)?sent\s+(?:me|[A-Z][a-z]+)\b", evidence or "",
    )
    for value in re.findall(r"(?im)^\s*From:\s*([^\n]+)", evidence or ""):
        display_name, address = parseaddr(value)
        cleaned = display_name.strip().strip("\"'") or address.partition("@")[0]
        if cleaned:
            sender_names.append(cleaned)
    if not sender_names:
        return []
    findings = []
    for match in re.finditer(
        r"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?Sender:\s*(?:\*\*)?\s*([^*\n]+)", text,
    ):
        claimed = match.group(1).strip()
        base = re.sub(r"\s*\([^)]*\)\s*$", "", claimed).strip()
        exact = any(base.casefold() == name.strip().casefold() for name in sender_names)
        first_name = " " not in base and any(
            base.casefold() == name.strip().split()[0].casefold()
            for name in sender_names if name.strip()
        )
        if exact or first_name:
            continue
        findings.append(_finding(
            SENDER_IDENTITY, match.group(0),
            "Use the sender identity established by message metadata or the transcript; do not infer a fuller name.",
        ))
    return findings


def _lint_source_count(text: str, evidence: str) -> list[dict[str, str]]:
    found = re.search(r"\b(\d+)\s+(?:meetings?\s+)?found\b", evidence, re.IGNORECASE)
    if not found:
        return []
    source_count = int(found.group(1))
    ordinals = {
        "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
        "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    }
    numbers = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }
    excluded_ordinals = set()
    for match in re.finditer(
        r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))\b"
        r"[^.\n]{0,100}\b(?:excluded|not scored)\b", text, re.IGNORECASE,
    ):
        for raw in re.findall(
            r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))\b",
            match.group(0), re.IGNORECASE,
        ):
            raw = raw.lower()
            ordinal_digits = re.match(r"\d+", raw)
            excluded_ordinals.add(int(ordinal_digits.group()) if ordinal_digits else ordinals[raw])
    excluded_groups = 0
    for _, sentence in _sentence_spans(text):
        if not re.search(r"\b(?:excluded|not scored)\b", sentence, re.IGNORECASE):
            continue
        if re.search(
            r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))\b",
            sentence, re.IGNORECASE,
        ):
            continue
        for match in re.finditer(
            r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+meetings?\b"
            r"[^.\n]{0,100}\b(?:excluded|not scored)\b", sentence, re.IGNORECASE,
        ):
            raw = match.group(1).lower()
            excluded_groups += int(raw) if raw.isdigit() else numbers[raw]
    excluded_count = len(excluded_ordinals) + excluded_groups
    findings = []
    for match in re.finditer(
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
        r"(?:full-transcript\s+|recorded\s+)?meetings?\b", text, re.IGNORECASE,
    ):
        if re.match(
            r"\s*[,;]?\s*(?:were|was|are|is)?\s*(?:(?!scored\b)[^.\n]){0,50}(?:excluded|not scored)\b",
            text[match.end():match.end() + 60], re.IGNORECASE,
        ):
            continue
        raw = match.group(1).lower()
        claimed = int(raw) if raw.isdigit() else numbers[raw]
        if claimed + excluded_count == source_count:
            continue
        findings.append(_finding(
            SOURCE_COUNT, match.group(0),
            f"Reconcile {claimed} scored meetings plus {excluded_count} named exclusions against the source population of {source_count}.",
        ))
    return findings


def _lint_qualifier_and_opening(text: str, evidence: str) -> list[dict[str, str]]:
    findings = []
    if re.search(r"\bapproved ink specs\b", text, re.IGNORECASE) and re.search(
        r"\bEST\. PMS\b|\bLAYOUT FOR VISUALIZATION ONLY\b", evidence, re.IGNORECASE,
    ):
        findings.append(_finding(
            UNSUPPORTED_QUALIFIER, "approved ink specs",
            "Describe these as estimated Pantone references in visualization-only artwork, not approved specifications.",
        ))
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if re.match(r"Here is the (?:plain-English|complete) breakdown\b", first, re.IGNORECASE):
        findings.append(_finding(
            OPENING_FRAME, first,
            "Delete the opening frame and start with the answer.",
        ))
    return findings


def lint(text: str, evidence: Any = None, schedule_evidence: Any = None) -> list[dict[str, str]]:
    """Return deterministic editorial findings with code, exact quote, and fix.

    Empty input returns no findings. Type errors for invalid text/evidence are
    intentional programmer errors; CLI input failures are returned as JSON.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    evidence_text = _evidence_text(evidence)
    schedule_text = _evidence_text(schedule_evidence)
    prose = _prose_without_fences(text)
    findings = []
    findings.extend(_lint_headings(prose))
    findings.extend(_lint_statistics(prose, evidence_text))
    findings.extend(_lint_absolute_claims(prose))
    findings.extend(_lint_metaphor(prose))
    findings.extend(_lint_production_overclaim(prose, evidence_text))
    findings.extend(_lint_schedule_scope(prose, schedule_text))
    findings.extend(_lint_sender_identity(prose, evidence_text))
    findings.extend(_lint_source_count(prose, evidence_text))
    findings.extend(_lint_qualifier_and_opening(prose, evidence_text))

    # Stable source order, then code; remove only exact duplicate findings.
    unique = []
    seen = set()
    for finding in findings:
        key = (finding["code"], finding["quote"])
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    code_order = {
        HEADING_STYLE: 0,
        UNSUPPORTED_STATISTIC: 1,
        ABSOLUTE_GUARANTEE: 2,
        ABSOLUTE_ISOLATION: 3,
        CORPORATE_METAPHOR: 4,
        PRODUCTION_OVERCLAIM: 5,
        SCHEDULE_SCOPE: 6,
        SENDER_IDENTITY: 7,
        SOURCE_COUNT: 8,
        UNSUPPORTED_QUALIFIER: 9,
        OPENING_FRAME: 10,
    }
    return sorted(
        unique,
        key=lambda f: (text.find(f["quote"]) if f["quote"] in text else len(text),
                       code_order[f["code"]], f["quote"]),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text_file", type=Path, help="UTF-8 operator message to lint")
    parser.add_argument("--evidence", type=Path, help="optional UTF-8 evidence file")
    args = parser.parse_args(argv)
    try:
        text = args.text_file.read_text(encoding="utf-8-sig")
        evidence = args.evidence.read_text(encoding="utf-8-sig") if args.evidence else None
        findings = lint(text, evidence=evidence)
    except (OSError, UnicodeError, TypeError) as exc:
        payload = {"ok": False, "error": str(exc), "findings": []}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    payload = {"ok": not findings, "findings": findings}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
