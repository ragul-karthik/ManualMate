"""Local, page-cited extraction of setup steps and manual inventory."""
from __future__ import annotations

import re

_SPACE = re.compile(r"\s+")
_SETUP = re.compile(
    r"\b(installation|installing|setup|set[ -]?up|assembly|assemble|quick[ -]?start|"
    r"getting started|first[ -]?use|before you begin|installation check|setup procedure)\b", re.I
)
_END_SECTION = re.compile(
    r"\b(troubleshoot(?:ing)?|maintenance|care and cleaning|cleaning|warranty|"
    r"specifications|reference|basic operation|operation|safety|parts list|tools list)\b", re.I
)
_ACTION = re.compile(
    r"^(?:first\s+)?(?:place|put|position|connect|attach|install|insert|remove|open|close|turn|press|select|"
    r"check|ensure|set|adjust|secure|tighten|loosen|fill|add|plug|unplug|run|start|stop|route|align|level|"
    r"clean|load|mount|assemble|prepare|inspect|configure|hold|wait|choose|slide|lift|push|pull|fasten|"
    r"apply|measure|confirm|verify|make sure|allow|leave|keep|do not|never)\b", re.I
)
_NUMBERED = re.compile(
    r"^\s*(?:(?:step\s*)?(\d{1,2})\s*[.)]|\((\d{1,2})\)|[-•▪]\s+)\s*(.+)$", re.I
)
_SAFETY = re.compile(r"\b(warning|caution|danger|important|do not|never|unplug|disconnect|keep away)\b", re.I)
_TOOL_HEADING = re.compile(r"\b(tool|tools|equipment|what you(?:'ll| will) need)\b", re.I)
_PART_HEADING = re.compile(r"\b(in the box|package contents|contents|parts|components|supplied items|included items|kit contents)\b", re.I)


def clean_text(text: str) -> str:
    """Normalize common PDF extraction artifacts without losing measurements."""
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    text = re.sub(r"(?<=\d)\ufffd(?=\d)", "-", text)
    text = re.sub(r"(?<=\w)\ufffd(?=\w)", " ", text)
    text = text.replace("\ufffd", "")
    text = text.replace("\u00ad", "")
    return _SPACE.sub(" ", text).strip(" •▪\t\r\n")


def _is_heading(line: str) -> bool:
    line = clean_text(line).rstrip(":")
    if not line or len(line) > 90 or len(line.split()) > 13:
        return False
    # Manuals commonly use all caps, but also use short title-case section labels.
    return (line.isupper() or (line.istitle() and not re.search(r"[.!?]", line)))


def _title(instruction: str) -> str:
    lead = re.split(r"[.;]", clean_text(instruction), maxsplit=1)[0]
    words = lead.split()
    return (" ".join(words[:8]) + ("…" if len(words) > 8 else ""))[:80]


def _step(page: int, body: str, number: str | None = None) -> dict[str, object]:
    body = clean_text(body)
    return {"title": _title(body), "body": body, "tip": "", "page": page, "number": number or ""}


def _safety_note(step: dict[str, object], pages: list[str], already_used: set[int]) -> None:
    page = int(step["page"])
    if page in already_used:
        step["tip"] = f"Review page {page} for any additional cautions before continuing."
        return
    body = pages[page - 1]
    notes = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body):
        sentence = clean_text(sentence)
        if _SAFETY.search(sentence) and len(sentence) > 12 and sentence not in notes:
            notes.append(sentence)
    step["tip"] = " ".join(notes[:2]) if notes else f"Check page {page} for safety notes before continuing."
    if notes:
        already_used.add(page)


def _collect_section_steps(pages: list[str]) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = []
    active = False
    current: dict[str, object] | None = None

    def flush() -> None:
        nonlocal current
        if current and len(str(current["body"])) >= 8:
            steps.append(current)
        current = None

    for page_no, page in enumerate(pages, 1):
        for raw in page.splitlines():
            line = clean_text(raw)
            if not line:
                continue
            if _is_heading(line):
                flush()
                if _SETUP.search(line):
                    active = True
                elif _END_SECTION.search(line) or _TOOL_HEADING.search(line) or _PART_HEADING.search(line):
                    active = False
                continue
            match = _NUMBERED.match(line)
            if active and match:
                flush()
                number = match.group(1) or match.group(2) or ""
                body = match.group(3)
                current = _step(page_no, body, number)
                continue
            if active and current:
                # Wrapped PDF lines belong to the numbered instruction above.
                if not _END_SECTION.search(line):
                    current["body"] = clean_text(f"{current['body']} {line}")
                    current["title"] = _title(str(current["body"]))
                continue
            if active and _ACTION.match(line):
                flush()
                current = _step(page_no, line)
        flush()
    return steps


def _fallback_steps(pages: list[str]) -> list[dict[str, object]]:
    """When headings are missing, use only contiguous numbered action lists."""
    found: list[dict[str, object]] = []
    for page_no, page in enumerate(pages, 1):
        lines = [clean_text(line) for line in page.splitlines()]
        candidates = []
        current = None
        for line in lines:
            if not line:
                continue
            match = _NUMBERED.match(line)
            if match:
                if current:
                    candidates.append(current)
                number = match.group(1) or match.group(2) or ""
                body = match.group(3)
                current = _step(page_no, body, number)
            elif current and not _is_heading(line):
                current["body"] = clean_text(f"{current['body']} {line}")
                current["title"] = _title(str(current["body"]))
            elif current:
                candidates.append(current)
                current = None
        if current:
            candidates.append(current)
        actionable = [item for item in candidates if _ACTION.search(str(item["body"]))]
        if len(actionable) >= 2:
            found.extend(actionable)
    return found


def extract_setup_steps(pages: list[str], limit: int = 12) -> list[dict[str, object]]:
    """Find concise setup instructions in source order, retaining PDF page numbers."""
    found = _collect_section_steps(pages)
    if not found:
        found = _fallback_steps(pages)
    result = []
    seen: set[str] = set()
    safety_pages: set[int] = set()
    for item in found:
        body = clean_text(str(item["body"]))
        key = re.sub(r"\W+", "", body.lower())
        if len(body) < 8 or key in seen:
            continue
        item["body"] = body
        item["title"] = _title(body)
        seen.add(key)
        _safety_note(item, pages, safety_pages)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _inventory_heading(line: str) -> tuple[str | None, str]:
    clean = clean_text(line)
    lower = clean.lower().rstrip(":")
    if not clean:
        return None, ""
    # Accept explicit labels and common manual section headings.
    match = re.match(r"^(tools?|equipment|parts|components|included items|supplied items|package contents|in the box|contents)\s*:\s*(.*)$", clean, re.I)
    if match:
        label = match.group(1).lower()
        return ("tools" if _TOOL_HEADING.search(label) else "parts"), match.group(2)
    if _is_heading(clean) and _TOOL_HEADING.search(lower):
        return "tools", ""
    if _is_heading(clean) and _PART_HEADING.search(lower):
        return "parts", ""
    return None, ""


def _split_inventory(raw: str) -> list[str]:
    raw = clean_text(raw)
    raw = re.sub(r"^(?:you(?:'ll| will) need|tools? needed|tools? required|required tools|includes?|contains?)\s*:?\s*", "", raw, flags=re.I)
    # Keep parenthetical quantities. Split common list delimiters, then trim prose punctuation.
    parts = re.split(r"\s*[,;\n]\s*|\s+and\s+", raw, flags=re.I)
    result = []
    for part in parts:
        item = clean_text(part).strip(".:;,- ")
        if not item:
            continue
        item = re.sub(r"^(?:and|&)\s+", "", item, flags=re.I)
        item = re.sub(r"^\d{1,2}[.)-]\s*", "", item)
        item = re.sub(r"^(?:a|an|the)\s+", "", item, flags=re.I)
        if len(item) > 70:
            continue
        if re.search(r"\b(do not|never|before you|then |after you|make sure|ensure that|to install|use the)\b", item, re.I):
            continue
        result.append(item)
    return result


def extract_inventory(pages: list[str], limit: int = 16) -> dict[str, list[dict[str, object]]]:
    """Extract labeled tool and parts lists, each item cited to its source page."""
    result: dict[str, list[dict[str, object]]] = {"tools": [], "parts": []}
    seen: dict[str, set[str]] = {"tools": set(), "parts": set()}
    for page_no, page in enumerate(pages, 1):
        lines = page.splitlines()
        section: str | None = None
        collected: list[str] = []
        for raw in lines + [""]:
            line = clean_text(raw)
            heading_type, inline = _inventory_heading(line)
            if heading_type:
                if section and collected:
                    for item in _split_inventory(" ".join(collected)):
                        key = re.sub(r"\W+", "", item.lower())
                        if key and key not in seen[section] and len(result[section]) < limit:
                            result[section].append({"name": item, "page": page_no})
                            seen[section].add(key)
                section, collected = heading_type, ([inline] if inline else [])
                continue
            if section and _is_heading(line):
                for item in _split_inventory(" ".join(collected)):
                    key = re.sub(r"\W+", "", item.lower())
                    if key and key not in seen[section] and len(result[section]) < limit:
                        result[section].append({"name": item, "page": page_no})
                        seen[section].add(key)
                section, collected = None, []
                continue
            if section:
                if line and not _SAFETY.search(line):
                    collected.append(line)
                elif not line and collected:
                    for item in _split_inventory(" ".join(collected)):
                        key = re.sub(r"\W+", "", item.lower())
                        if key and key not in seen[section] and len(result[section]) < limit:
                            result[section].append({"name": item, "page": page_no})
                            seen[section].add(key)
                    section, collected = None, []
    return result
