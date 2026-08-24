from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class ModelPrice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    currency: str = "USD"
    effective_date: date
    source_note: str = Field(min_length=1, max_length=256)


class PricingRegistry(BaseModel):
    """价格由运行者显式提供并版本化，仓库不硬编码易变化价格。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    prices: tuple[ModelPrice, ...]

    def estimate_microunits(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> int | None:
        if input_tokens is None or output_tokens is None:
            return None
        price = next(
            (
                item
                for item in self.prices
                if item.provider == provider and item.model == model
            ),
            None,
        )
        if price is None:
            return None
        amount = (
            Decimal(input_tokens) * price.input_per_million
            + Decimal(output_tokens) * price.output_per_million
        )
        return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
