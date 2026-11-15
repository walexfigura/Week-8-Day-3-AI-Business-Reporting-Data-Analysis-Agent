"""Deterministic checks on the agent's final answer.

The language model writes the narrative; these checks make sure it stays tied
to the evidence:

1. **Evidence citations** – facts and calculations must cite evidence IDs that
   exist (E1, E2, ... from successful tool calls).
2. **Hypothesis hygiene** – hypotheses must say how to verify them.
3. **Number grounding** – every money/percentage/large number in the answer
   must match (within display rounding) a number some tool returned.
4. **Causal language in facts** – a "fact" that names an external cause
   ("because the algorithm changed", "audience fatigue") is flagged: it is
   probably a hypothesis.
5. **Attribution confusion** – flags text that adds Meta-attributed value to
   Shopify sales.

These are heuristics, not proofs. They catch common failure modes cheaply and
their warnings are printed in every report.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_NUMBER = re.compile(
    r"(?P<neg>[-−])?(?P<dollar>\$)?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?(?P<suffix>[kKmM]\b)?(?P<pct>\s?%)?"
)
_SKIP_CONTEXT = re.compile(r"(\d{4}-\d{2}-\d{2}|\bE\d+\b|NS-[A-Z0-9-]+|\b\d{9,}\b|#NS\d+|v\d+\.\d+)")
CAUSAL_PATTERNS = [
    r"\bbecause\b", r"\bcaused\b", r"\bcausing\b", r"\balgorithm", r"\bfatigue", r"\bcompetitor", r"\bsaturat",
    r"\bweather\b", r"\beconom(y|ic)\b", r"\btargeting (became|got|was) (worse|poor)", r"\bless efficient because",
]
_CAUSAL = re.compile("|".join(CAUSAL_PATTERNS), re.IGNORECASE)
_ATTRIBUTION_CONFUSION = re.compile(
    r"(add(ing|ed)?|plus|combined|total revenue of)\b[^.]{0,80}\b(meta|platform)[^.]{0,40}(purchase value|attributed|revenue)"
    r"|(meta|platform)[^.]{0,40}(purchase value|attributed revenue)[^.]{0,40}\b(added to|plus|on top of)\b[^.]{0,30}(shopify|sales)",
    re.IGNORECASE,
)


def extract_numbers(text: str) -> list[tuple[str, float, int]]:
    """Return (raw text, value, decimals) for numbers worth checking.

    Checked: anything with $ or %, numbers with thousands separators or
    decimals, k/M suffixes, and integers >= 100. Skipped: dates, evidence IDs,
    SKUs, long numeric IDs, small counts ("3 campaigns", "7 days").
    """
    scrubbed = _SKIP_CONTEXT.sub(" ", text)
    found = []
    for m in _NUMBER.finditer(scrubbed):
        raw = m.group(0).strip()
        num = m.group("num")
        value = float(num.replace(",", ""))
        decimals = len(num.split(".")[1]) if "." in num else 0
        suffix = (m.group("suffix") or "").lower()
        if suffix == "k":
            value *= 1_000
            decimals = -3 + decimals
        elif suffix == "m":
            value *= 1_000_000
            decimals = -6 + decimals
        important = bool(m.group("dollar") or m.group("pct") or "," in num or "." in num or suffix or value >= 100)
        if not important:
            continue
        found.append((raw, value, decimals))
    return found


def collect_numbers(obj: Any, out: set[float] | None = None) -> set[float]:
    """All numbers in tool outputs, including numbers written inside strings."""
    out = out if out is not None else set()
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(abs(float(obj)))
    elif isinstance(obj, str):
        for _raw, value, _dec in extract_numbers(obj):
            out.add(abs(value))
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            collect_numbers(v, out)
    return out


def is_grounded(value: float, decimals: int, known: Iterable[float]) -> bool:
    # Allow exactly the rounding implied by how the number is written:
    # "80%" matches 79.5-80.5, "$18,600" matches 18,599.50-18,600.50, "$18.6k" matches 18,550-18,650.
    tolerance = 0.5 * 10 ** (-decimals) + 1e-9
    return any(abs(abs(value) - k) <= tolerance for k in known)


def ungrounded_numbers(text: str, known: set[float]) -> list[str]:
    return [raw for raw, value, decimals in extract_numbers(text) if not is_grounded(value, decimals, known)]


def check_submission(submission: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> list[str]:
    """Return human-readable guardrail warnings (empty list = all checks passed)."""
    warnings: list[str] = []
    known = collect_numbers([e.get("data") for e in evidence.values()])
    valid_ids = set(evidence)

    for i, f in enumerate(submission.get("findings", []), start=1):
        kind, ids = f.get("kind"), f.get("evidence_ids") or []
        bad = [x for x in ids if x not in valid_ids]
        if bad:
            warnings.append(f"Finding {i} cites unknown evidence ID(s): {', '.join(bad)}.")
        if kind in {"observed_fact", "calculation"}:
            if not ids:
                warnings.append(f"Finding {i} is a {kind} without evidence IDs.")
            if _CAUSAL.search(f.get("statement", "")):
                warnings.append(f"Finding {i} is labelled {kind} but uses causal language "
                                f"(\"{_CAUSAL.search(f['statement']).group(0)}\"); it may be an unsupported hypothesis.")
            missing = ungrounded_numbers(f.get("statement", ""), known)
            if missing:
                warnings.append(f"Finding {i} has number(s) not found in tool results: {', '.join(missing)}.")
        if kind == "hypothesis" and not (f.get("how_to_verify") or "").strip():
            warnings.append(f"Finding {i} is a hypothesis without a verification step.")
    for field in ("headline", "answer"):
        missing = ungrounded_numbers(submission.get(field, ""), known)
        if missing:
            warnings.append(f"The {field} has number(s) not found in tool results: {', '.join(missing[:8])}.")
    for i, r in enumerate(submission.get("recommendations", []), start=1):
        bad = [x for x in r.get("evidence_ids") or [] if x not in valid_ids]
        if bad:
            warnings.append(f"Recommendation {i} cites unknown evidence ID(s): {', '.join(bad)}.")
    full_text = " ".join([submission.get("headline", ""), submission.get("answer", "")] +
                         [f.get("statement", "") for f in submission.get("findings", [])])
    if _ATTRIBUTION_CONFUSION.search(full_text):
        warnings.append("Text appears to add Meta-attributed value to Shopify sales; they overlap and must not be summed.")
    return warnings


def causal_claims_in_facts(submission: dict[str, Any]) -> list[str]:
    return [f["statement"] for f in submission.get("findings", [])
            if f.get("kind") in {"observed_fact", "calculation"} and _CAUSAL.search(f.get("statement", ""))]
