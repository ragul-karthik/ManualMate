"""Transparent self-interested utility scoring for assistance bundles."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Assistance:
    name: str
    relevance: int  # 1..10
    time_saved_min: int
    price: float
    safety: int  # 1..10
    tags: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.price <= 30 and self.safety >= 7

    @property
    def utility(self) -> float:
        # U = 5*relevance + 1.5*time_saved_minutes + 3*safety - 2*price.
        return 5*self.relevance + 1.5*self.time_saved_min + 3*self.safety - 2*self.price


CANDIDATES = [
    Assistance("Quick leak checklist", 8, 8, 4, 10, ("leak", "water", "inlet", "hose")),
    Assistance("Video call support", 9, 20, 28, 9, ("support", "technician", "help")),
    Assistance("Replacement hose kit", 7, 18, 24, 9, ("hose", "leak", "inlet", "drain")),
    Assistance("Filter cleaning guide", 8, 12, 2, 9, ("filter", "clean", "drain", "water")),
    Assistance("General setup article", 5, 6, 0, 7, ("setup", "install", "start")),
    Assistance("Technician visit", 10, 35, 45, 10, ("technician", "service", "electrical")),
    Assistance("Unverified forum tip", 7, 15, 0, 3, ("forum", "tip")),
    Assistance("Parts diagram", 6, 10, 5, 8, ("parts", "hose", "filter")),
    Assistance("Drain hose extender", 8, 16, 18, 8, ("drain", "hose", "water")),
    Assistance("Warranty service request", 9, 25, 12, 10, ("warranty", "service", "repair", "technician")),
]


def rank_candidates(question: str = "") -> tuple[list[tuple[Assistance, bool]], Assistance]:
    """Rank the same ten offers, boosting relevance for the user's issue terms."""
    terms = set(re.findall(r"[a-z]{3,}", question.lower()))
    terms = {term[:-3] if term.endswith("ing") else term[:-1] if term.endswith("s") and len(term) > 3 else term for term in terms}
    ranked = []
    for item in CANDIDATES:
        overlap = len(terms.intersection(set(item.tags)))
        relevance = item.relevance
        if terms:
            relevance = min(10, relevance + min(3, overlap)) if overlap else max(1, relevance - 3)
        contextual = Assistance(item.name, relevance, item.time_saved_min, item.price, item.safety, item.tags)
        ranked.append((contextual, contextual.valid))
    valid = [item for item, ok in ranked if ok]
    return ranked, max(valid, key=lambda item: item.utility)
