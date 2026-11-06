"""Grammar and spelling checks for manually entered English transcripts."""

from bisect import bisect_right
from typing import Any, Dict, List

import requests
from loguru import logger


LANGUAGETOOL_URL = "https://api.languagetool.org/v2/check"
MAX_CHECK_CHARS = 65000
IGNORED_ISSUE_TYPES = {"style", "register"}


def check_english_texts(texts: List[str], language: str = "en-GB") -> Dict[str, Any]:
    """Check a list of texts with LanguageTool and map issues back to each text.

    Returns a dict with ``checked`` (whether the service answered), ``issues``
    (offset/length relative to each provided text) and an optional ``reason``.
    """
    cleaned = [str(text or "") for text in texts]
    non_empty = [(index, text) for index, text in enumerate(cleaned) if text.strip()]
    if not non_empty:
        return {"checked": True, "issues": [], "reason": None}

    combined = "\n".join(text for _, text in non_empty)
    if len(combined) > MAX_CHECK_CHARS:
        return {
            "checked": False,
            "issues": [],
            "reason": "Transcript is too long for the grammar service.",
        }

    starts: List[int] = []
    cursor = 0
    for _, text in non_empty:
        starts.append(cursor)
        cursor += len(text) + 1

    try:
        response = requests.post(
            LANGUAGETOOL_URL,
            data={"text": combined, "language": language},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as err:
        logger.warning(f"Grammar check unavailable: {err}")
        return {"checked": False, "issues": [], "reason": str(err)}

    issues: List[Dict[str, Any]] = []
    for match in payload.get("matches", []):
        rule = match.get("rule") or {}
        issue_type = str(rule.get("issueType") or "").strip().lower()
        if issue_type in IGNORED_ISSUE_TYPES:
            continue
        try:
            offset = int(match.get("offset") or 0)
            length = int(match.get("length") or 0)
        except (TypeError, ValueError):
            continue
        if length <= 0:
            continue

        position = bisect_right(starts, offset) - 1
        if position < 0 or position >= len(non_empty):
            continue
        text_index, text = non_empty[position]
        local_offset = offset - starts[position]
        if local_offset < 0 or local_offset + length > len(text):
            continue

        replacements = [
            str(item.get("value") or "")
            for item in (match.get("replacements") or [])
            if str(item.get("value") or "").strip()
        ][:3]
        category = (rule.get("category") or {}).get("id") or ""
        issues.append(
            {
                "text_index": text_index,
                "offset": local_offset,
                "length": length,
                "original": text[local_offset:local_offset + length],
                "message": str(match.get("message") or ""),
                "short_message": str(match.get("shortMessage") or ""),
                "category": str(category),
                "rule_id": str(rule.get("id") or ""),
                "replacements": replacements,
            }
        )

    unique: Dict[tuple, Dict[str, Any]] = {}
    for issue in issues:
        key = (issue["text_index"], issue["offset"], issue["length"])
        unique[key] = issue
    ordered = sorted(
        unique.values(),
        key=lambda item: (item["text_index"], item["offset"]),
    )
    return {"checked": True, "issues": ordered, "reason": None}
