"""Structured appliance knowledge and optional public CPSC recall lookup."""
from __future__ import annotations

import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CPSC_RECALLS_URL = "https://www.saferproducts.gov/RestWebServices/Recall"
CPSC_INFO_URL = "https://www.cpsc.gov/Recalls/CPSC-Recalls-Application-Program-Interface-API-Information"
logger = logging.getLogger("manualmate.knowledge")


class KnowledgeLookupError(RuntimeError):
    """Safe error raised when the public recall feed cannot be read."""


def extract_product_identifier(pages: list[str], fallback: str = "") -> str:
    """Extract only a product/model label for the user-approved recall query."""
    text = "\n".join(pages[:3])
    patterns = (
        r"\bmodel(?:\s*(?:no\.?|number))?\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{1,40})",
        r"\bproduct\s*(?:name|model)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{1,40})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip(".,;:")
    return fallback.strip()[:100]


def _field(record: dict, *names: str) -> str:
    folded = {str(key).casefold(): value for key, value in record.items()}
    for name in names:
        value = folded.get(name.casefold())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def lookup_product_recalls(product_name: str, timeout: float = 5.0) -> dict:
    """Fetch and normalize official public recall records for one product name."""
    query = product_name.strip()[:100]
    if not query:
        raise KnowledgeLookupError("Enter a product or model name before checking recalls.")
    url = f"{CPSC_RECALLS_URL}?{urlencode({'ProductName': query, 'format': 'json'})}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ManualMate/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise KnowledgeLookupError(f"CPSC recall service returned HTTP {exc.code}.") from None
    except (URLError, TimeoutError, OSError):
        raise KnowledgeLookupError("Could not reach the CPSC recall service. Manual guidance is still available.") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise KnowledgeLookupError("The CPSC recall service returned data ManualMate could not read.") from None

    if isinstance(payload, dict):
        records = payload.get("Results", payload.get("results", payload.get("recalls", [payload])))
    else:
        records = payload
    if not isinstance(records, list):
        records = []
    matches = []
    for record in records[:20]:
        if not isinstance(record, dict):
            continue
        matches.append({
            "recall_number": _field(record, "RecallNumber", "Recall Number"),
            "product": _field(record, "ProductName", "Product Name", "Title"),
            "company": _field(record, "Company", "Manufacturer", "FirmName"),
            "hazard": _field(record, "Hazard", "Description", "RecallDescription"),
            "date": _field(record, "RecallDate", "Recall Date"),
            "url": _field(record, "URL", "RecallURL", "Link"),
        })
    logger.info("public recall lookup completed match_count=%d", len(matches))
    return {"status": "checked", "product_query": query, "matches": matches, "source": CPSC_INFO_URL}
