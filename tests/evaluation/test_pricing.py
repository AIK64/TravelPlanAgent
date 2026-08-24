from datetime import date
from decimal import Decimal

from travel_agent.evaluation.pricing import ModelPrice, PricingRegistry


def test_pricing_requires_known_tokens_and_explicit_versioned_rate():
    registry = PricingRegistry(
        version="test-pricing-v1",
        prices=(
            ModelPrice(
                provider="provider",
                model="model",
                input_per_million=Decimal("1"),
                output_per_million=Decimal("2"),
                effective_date=date(2026, 8, 24),
                source_note="test-only explicit rate",
            ),
        ),
    )

    assert registry.estimate_microunits(
        provider="provider", model="model", input_tokens=1_000, output_tokens=500
    ) == 2_000
    assert registry.estimate_microunits(
        provider="provider", model="model", input_tokens=None, output_tokens=1
    ) is None
    assert registry.estimate_microunits(
        provider="other", model="model", input_tokens=1, output_tokens=1
    ) is None
