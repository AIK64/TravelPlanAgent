"""显式启用的 DeepSeek 连通性检查；常规测试绝不访问网络。"""

from __future__ import annotations

from datetime import date
import os

import pytest

from travel_agent.requirements.models import NaturalPlanningRequest
from travel_agent.requirements.providers.deepseek import DeepSeekRequirementModel


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DEEPSEEK_LIVE") != "1"
    or not os.getenv("DEEPSEEK_API_KEY")
    or not os.getenv("DEEPSEEK_MODEL"),
    reason=(
        "set RUN_DEEPSEEK_LIVE=1, DEEPSEEK_API_KEY and DEEPSEEK_MODEL "
        "to run live DeepSeek smoke tests"
    ),
)


@pytest.mark.asyncio
async def test_deepseek_requirement_smoke():
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        max_retries=0,
    )
    try:
        output = await DeepSeekRequirementModel(
            client=client,
            model=os.environ["DEEPSEEK_MODEL"],
        ).parse(
            NaturalPlanningRequest(
                text=(
                    "2026年10月2日到10月4日去杭州，两个人，"
                    "2日10:30到杭州东站，4日19:00从杭州东站离开。"
                ),
                reference_date=date(2026, 8, 23),
            )
        )
    finally:
        await client.close()

    assert output.draft.destination == "杭州"
    assert output.draft.start_date == date(2026, 10, 2)
    assert output.draft.end_date == date(2026, 10, 4)
    assert output.draft.travelers == 2
