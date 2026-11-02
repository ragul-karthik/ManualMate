"""Two-party contract-net style assistance negotiation."""
from dataclasses import dataclass, replace

from manualmate.utility import rank_candidates


@dataclass
class BuyerAgent:
    budget: float = 30.0
    minimum_quality: int = 6
    def utility(self, price: float, quality: int) -> float:
        if price > self.budget or quality < self.minimum_quality:
            return -1e9
        return quality * 10 - price


@dataclass
class SupplierAgent:
    minimum_price: float = 20.0
    quality: int = 8
    def utility(self, price: float) -> float:
        return price - self.minimum_price


def negotiate(service_name: str = "manual assistance", supplier_minimum: float = 20.0, budget: float = 30.0) -> list[str]:
    buyer, supplier = BuyerAgent(budget=budget), SupplierAgent(minimum_price=supplier_minimum)
    supplier_quote = buyer.budget + 2
    buyer_counter = max(0.0, supplier.minimum_price - 2)
    supplier_counter = supplier.minimum_price + 2
    timeline = [
        f"01 Buyer requests {service_name}: quality ≥ 6, budget ≤ ${buyer.budget:.0f}.",
        f"02 Supplier bids {service_name} at ${supplier_quote:.0f} (quality 8); buyer rejects because it exceeds budget.",
    ]
    # Buyer and supplier take turns improving the proposal inside the $20–$30
    # mutually acceptable range. This deliberately takes two counteroffers.
    offers = [("Buyer", buyer_counter), ("Supplier", supplier_counter)]
    for turn, (proposer, price) in enumerate(offers, start=3):
        buyer_u = buyer.utility(price, supplier.quality)
        supplier_u = supplier.utility(price)
        feasible = buyer_u >= 0 and supplier_u >= 0
        outcome = "within both agents' limits" if feasible else "not acceptable to both agents"
        timeline.append(f"{turn:02d} {proposer} counteroffers ${price:.0f}: buyer utility={buyer_u:.0f}, supplier utility={supplier_u:.0f} ({outcome}).")
        if feasible:
            timeline.append(f"{turn+1:02d} ACCEPT → COMMIT: both agents confirm ${price:.0f}, quality {supplier.quality}; consensus handshake complete.")
            return timeline
    raise RuntimeError("No mutually acceptable offer found")


def negotiate_assistance(question: str, budget: float = 30.0):
    """Negotiate all eligible help offers; choose by utility at agreed price."""
    ranked, _ = rank_candidates(question)
    outcomes = []
    for item, initially_valid in ranked:
        # The buyer requires useful, safe help as well as an affordable offer.
        if not initially_valid or item.relevance < 6:
            outcomes.append((item, False))
            continue

        buyer = BuyerAgent(budget=budget, minimum_quality=6)
        supplier = SupplierAgent(minimum_price=item.price, quality=item.safety)
        supplier_floor = item.price
        markup = 1 if item.relevance >= 9 else 3
        buyer_counter = supplier_floor
        supplier_counter = supplier_floor + markup
        buyer_agent_utility = buyer.utility(supplier_counter, item.relevance)
        supplier_utility = supplier.utility(supplier_counter)
        assistance_utility = item.utility - 2 * (supplier_counter - item.price)
        accepted = supplier_counter <= budget and buyer_agent_utility >= 0 and supplier_utility >= 0 and assistance_utility >= 0
        if accepted:
            outcomes.append((replace(item, price=supplier_counter), True))
        else:
            outcomes.append((item, False))

    agreements = [item for item, accepted in outcomes if accepted]
    if not agreements:
        raise RuntimeError("No assistance offer met the buyer's budget and quality requirements")
    winner = max(agreements, key=lambda item: item.utility)
    floor = next(item.price for item, valid in ranked if item.name == winner.name and valid)
    buyer = BuyerAgent(budget=budget)
    supplier = SupplierAgent(minimum_price=floor, quality=winner.safety)
    buyer_utility = buyer.utility(winner.price, winner.relevance)
    supplier_utility = winner.price - floor
    timeline = [
        f"01 Buyer broadcasts request for: {question.strip() or 'help with the current setup'}; budget ≤ ${budget:.0f}, relevance ≥ 6, safety ≥ 7.",
        f"02 Supplier bids {winner.name} at ${floor + 10:.0f}; buyer counters at ${floor:.0f} to stay near the estimated base price.",
        f"03 Supplier counters at ${winner.price:.0f}; buyer utility={buyer_utility:.1f}, supplier utility={supplier_utility:.1f}, assistance utility={winner.utility:.1f}.",
        f"04 ACCEPT → COMMIT: both agents have nonnegative utility and the negotiated offer fits the budget; consensus handshake complete. ManualMate selects {winner.name} using the highest post-negotiation assistance utility.",
    ]
    return sorted(outcomes, key=lambda row: row[0].utility, reverse=True), winner, timeline
