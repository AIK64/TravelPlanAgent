from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
from langgraph.graph.state import CompiledStateGraph

from travel_agent.config import CriticProviderMode, Settings
from travel_agent.critique.evidence import EvidenceBudget
from travel_agent.critique.gateway import CriticGateway
from travel_agent.critique.protocols import CriticModel
from travel_agent.critique.providers.deepseek import DeepSeekCriticModel
from travel_agent.critique.providers.mock import MockCriticModel
from travel_agent.critique.providers.openai import OpenAICriticModel
from travel_agent.critique.quality import CriticPolicy
from travel_agent.domain.models import PlanningRequest, PlanningResponse
from travel_agent.domain.optimization_models import OptimizationBudget
from travel_agent.domain.tool_models import ProviderMode
from travel_agent.graph.workflow import build_workflow, run_planning
from travel_agent.planning.defaults import POIDefaultPolicy
from travel_agent.planning.policy import PlanningPolicy
from travel_agent.tools.gateway import ToolGateway, build_gateway
from travel_agent.tools.protocols import POIProvider, RouteProvider
from travel_agent.tools.providers.amap import (
    AMapClient,
    AMapPOIProvider,
    AMapRouteProvider,
)
from travel_agent.tools.providers.mock import MockPOIProvider, MockRouteProvider
from travel_agent.requirements.gateway import RequirementGateway
from travel_agent.requirements.checkpoints import open_requirement_checkpointer
from travel_agent.requirements.models import (
    ClarificationResumeRequest,
    NaturalPlanningRequest,
    NaturalPlanningResponse,
    RequirementProviderMode,
)
from travel_agent.requirements.protocols import RequirementModel
from travel_agent.requirements.providers.mock import MockRequirementModel
from travel_agent.requirements.providers.openai import OpenAIRequirementModel
from travel_agent.requirements.providers.deepseek import DeepSeekRequirementModel
from travel_agent.requirements.workflow import (
    build_requirement_workflow,
    resume_natural_planning,
    run_natural_planning,
)


@dataclass(slots=True)
class PlanningRuntime:
    """持有一套显式选定、由应用生命周期共享的规划依赖。"""

    poi_provider: POIProvider
    route_provider: RouteProvider
    gateway: ToolGateway
    workflow: CompiledStateGraph
    client: httpx.AsyncClient | None
    requirement_model: RequirementModel | None = None
    requirement_gateway: RequirementGateway | None = None
    requirement_workflow: CompiledStateGraph | None = None
    model_client: Any | None = None
    critic_model: CriticModel | None = None
    critic_gateway: CriticGateway | None = None
    critic_model_client: Any | None = None
    checkpoint_context: Any | None = field(default=None, repr=False)
    resume_locks: dict[str, asyncio.Lock] = field(default_factory=dict, repr=False)

    @classmethod
    async def create(cls, settings: Settings) -> "PlanningRuntime":
        settings.validate()
        client: httpx.AsyncClient | None = None
        model_client: Any | None = None
        critic_model_client: Any | None = None
        checkpoint_context: Any | None = None
        checkpoint_entered = False
        try:
            if settings.provider is ProviderMode.MOCK:
                poi_provider: POIProvider = MockPOIProvider()
                route_provider: RouteProvider = MockRouteProvider()
            else:
                assert settings.amap_api_key is not None
                client = httpx.AsyncClient(base_url="https://restapi.amap.com")
                amap_client = AMapClient(
                    client,
                    api_key=settings.amap_api_key,
                    timeout_seconds=settings.tool_timeout_seconds,
                )
                poi_provider = AMapPOIProvider(amap_client)
                route_provider = AMapRouteProvider(amap_client)

            gateway = build_gateway(settings, poi_provider, route_provider)
            defaults = POIDefaultPolicy(settings.unknown_fact_policy)
            policy = PlanningPolicy(
                poi_query_limit=settings.poi_query_limit,
                poi_candidate_limit=settings.poi_candidate_limit,
                route_strategy=settings.amap_driving_strategy,
                max_walking_leg_meters=settings.max_walking_leg_meters,
                use_real_walking_routes=settings.use_real_walking_routes,
                poi_max_queries=settings.poi_max_queries,
            )
            optimization_budget = OptimizationBudget(
                max_solve_ms=settings.optimization_max_solve_ms,
                max_search_states=settings.optimization_max_search_states,
                candidate_limit=settings.optimization_candidate_limit,
                variant_count=settings.optimization_variant_count,
            )
            critic_model: CriticModel | None
            critic_gateway: CriticGateway | None
            if settings.critic_provider is CriticProviderMode.DISABLED:
                critic_model = None
                critic_gateway = None
            elif settings.critic_provider is CriticProviderMode.MOCK:
                critic_model = MockCriticModel()
                critic_gateway = CriticGateway(
                    model=critic_model,
                    timeout_seconds=settings.critic_timeout_seconds,
                    max_attempts=settings.critic_max_attempts,
                )
            else:
                try:
                    from openai import AsyncOpenAI
                except ImportError as error:
                    extra = (
                        "llm-openai"
                        if settings.critic_provider is CriticProviderMode.OPENAI
                        else "llm-deepseek"
                    )
                    raise RuntimeError(
                        f"Install the {extra} extra to use "
                        f"CRITIC_PROVIDER={settings.critic_provider.value}"
                    ) from error
                if settings.critic_provider is CriticProviderMode.OPENAI:
                    assert settings.openai_api_key is not None
                    critic_model_client = AsyncOpenAI(
                        api_key=settings.openai_api_key,
                        timeout=settings.critic_timeout_seconds,
                        max_retries=0,
                    )
                    critic_model = OpenAICriticModel(
                        client=critic_model_client,
                        model=settings.critic_model,
                    )
                else:
                    assert settings.deepseek_api_key is not None
                    critic_model_client = AsyncOpenAI(
                        api_key=settings.deepseek_api_key,
                        base_url=settings.deepseek_base_url,
                        timeout=settings.critic_timeout_seconds,
                        max_retries=0,
                    )
                    critic_model = DeepSeekCriticModel(
                        client=critic_model_client,
                        model=settings.critic_model,
                        max_tokens=settings.critic_max_output_tokens,
                    )
                critic_gateway = CriticGateway(
                    model=critic_model,
                    timeout_seconds=settings.critic_timeout_seconds,
                    max_attempts=settings.critic_max_attempts,
                )
            critic_policy = CriticPolicy(
                quality_threshold=settings.critic_quality_threshold,
                min_improvement=settings.critic_min_improvement,
                max_soft_replan_rounds=settings.max_soft_replan_rounds,
                grounding_max_attempts=settings.critic_grounding_max_attempts,
            )
            workflow = build_workflow(
                gateway,
                defaults,
                policy,
                optimization_budget=optimization_budget,
                critic_gateway=critic_gateway,
                critic_policy=critic_policy,
                evidence_budget=EvidenceBudget(
                    max_input_chars=settings.critic_max_input_chars
                ),
            )
            if settings.requirement_provider is RequirementProviderMode.MOCK:
                requirement_model: RequirementModel = MockRequirementModel()
            else:
                try:
                    from openai import AsyncOpenAI
                except ImportError as error:
                    extra = (
                        "llm-openai"
                        if settings.requirement_provider
                        is RequirementProviderMode.OPENAI
                        else "llm-deepseek"
                    )
                    raise RuntimeError(
                        f"Install the {extra} extra to use "
                        f"REQUIREMENT_PROVIDER={settings.requirement_provider.value}"
                    ) from error
                if settings.requirement_provider is RequirementProviderMode.OPENAI:
                    assert settings.openai_api_key is not None
                    model_client = AsyncOpenAI(
                        api_key=settings.openai_api_key,
                        timeout=settings.requirement_timeout_seconds,
                        max_retries=0,
                    )
                    requirement_model = OpenAIRequirementModel(
                        client=model_client,
                        model=settings.requirement_model,
                    )
                else:
                    assert settings.deepseek_api_key is not None
                    model_client = AsyncOpenAI(
                        api_key=settings.deepseek_api_key,
                        base_url=settings.deepseek_base_url,
                        timeout=settings.requirement_timeout_seconds,
                        max_retries=0,
                    )
                    requirement_model = DeepSeekRequirementModel(
                        client=model_client,
                        model=settings.deepseek_model,
                    )
            requirement_gateway = RequirementGateway(
                model=requirement_model,
                timeout_seconds=settings.requirement_timeout_seconds,
                max_attempts=settings.requirement_max_attempts,
                base_delay_seconds=settings.requirement_backoff_base_seconds,
                max_delay_seconds=settings.requirement_max_backoff_seconds,
            )
            checkpoint_context = open_requirement_checkpointer(settings)
            requirement_checkpointer = await checkpoint_context.__aenter__()
            checkpoint_entered = True
            requirement_workflow = build_requirement_workflow(
                requirement_gateway=requirement_gateway,
                tool_gateway=gateway,
                planning_workflow=workflow,
                checkpointer=requirement_checkpointer,
            )
            return cls(
                poi_provider=poi_provider,
                route_provider=route_provider,
                gateway=gateway,
                workflow=workflow,
                client=client,
                requirement_model=requirement_model,
                requirement_gateway=requirement_gateway,
                requirement_workflow=requirement_workflow,
                model_client=model_client,
                critic_model=critic_model,
                critic_gateway=critic_gateway,
                critic_model_client=critic_model_client,
                checkpoint_context=checkpoint_context,
            )
        except BaseException:
            if checkpoint_context is not None and checkpoint_entered:
                await checkpoint_context.__aexit__(None, None, None)
            if model_client is not None:
                await model_client.close()
            if (
                critic_model_client is not None
                and critic_model_client is not model_client
            ):
                await critic_model_client.close()
            if client is not None:
                await client.aclose()
            raise

    async def close(self) -> None:
        if self.model_client is not None:
            await self.model_client.close()
        if (
            self.critic_model_client is not None
            and self.critic_model_client is not self.model_client
        ):
            await self.critic_model_client.close()
        if self.client is not None:
            await self.client.aclose()
        if self.checkpoint_context is not None:
            await self.checkpoint_context.__aexit__(None, None, None)

    async def plan(
        self,
        request: PlanningRequest,
        thread_id: str,
    ) -> PlanningResponse:
        return await run_planning(self.workflow, request, thread_id=thread_id)

    async def plan_from_text(
        self,
        request: NaturalPlanningRequest,
        thread_id: str,
    ) -> NaturalPlanningResponse:
        if self.requirement_workflow is None:
            raise RuntimeError("natural-language planning is not configured")
        return await run_natural_planning(
            self.requirement_workflow,
            request,
            thread_id=thread_id,
        )

    async def resume_from_text(
        self,
        request: ClarificationResumeRequest,
        thread_id: str,
    ) -> NaturalPlanningResponse:
        if self.requirement_workflow is None:
            raise RuntimeError("natural-language planning is not configured")
        lock = self.resume_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            return await resume_natural_planning(
                self.requirement_workflow,
                request,
                thread_id=thread_id,
            )
