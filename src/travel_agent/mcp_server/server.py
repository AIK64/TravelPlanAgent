from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import date
import os
from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from travel_agent.application.service import TravelApplicationService
from travel_agent.config import Settings
from travel_agent.domain.models import PlanningRequest
from travel_agent.domain.lifecycle_models import (
    ApprovalAction,
    LifecycleResumeRequest,
    SelectCandidateAction,
    TextEditAction,
)
from travel_agent.domain.tool_models import POISearchQuery, RouteQuery, ToolCallContext
from travel_agent.identity.models import Principal
from travel_agent.memory.models import PreferenceCreateRequest
from travel_agent.runtime import PlanningRuntime


MCP_PROTOCOL_VERSION = "2025-11-25"
_CURRENT_PRINCIPAL: ContextVar[Principal | None] = ContextVar(
    "travel_agent_mcp_principal", default=None
)


class MCPIdentityMiddleware:
    """把 Streamable HTTP 可信身份投影到当前 MCP 请求上下文。"""

    def __init__(
        self,
        app: ASGIApp,
        default_principal: Principal,
        *,
        allow_default_identity: bool,
    ) -> None:
        self.app = app
        self.default_principal = default_principal
        self.allow_default_identity = allow_default_identity

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        principal = self.default_principal
        if scope["type"] == "http":
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            tenant_id = headers.get("x-tenant-id")
            user_id = headers.get("x-user-id")
            if (not tenant_id or not user_id) and not self.allow_default_identity:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b'{"code":"authentication_required"}',
                    }
                )
                return
            if tenant_id and user_id:
                scopes = frozenset(
                    item.strip()
                    for item in headers.get("x-scopes", "").split(",")
                    if item.strip()
                )
                principal = Principal(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    scopes=scopes,
                    authentication_method="mcp_http_header",
                )
        token = _CURRENT_PRINCIPAL.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            _CURRENT_PRINCIPAL.reset(token)


def build_mcp_server(
    service_getter: Callable[[], TravelApplicationService],
    *,
    default_principal: Principal,
    stateless_http: bool = True,
    lifespan=None,
    transport_security: TransportSecuritySettings | None = None,
) -> FastMCP:
    server = FastMCP(
        "travel-agent",
        instructions=(
            "约束感知旅行规划 Agent。长任务返回显式 run_id；"
            "不得把内部 Prompt、思维链或 Provider 原始响应暴露给客户端。"
        ),
        stateless_http=stateless_http,
        streamable_http_path="/",
        json_response=True,
        lifespan=lifespan,
        transport_security=transport_security,
    )

    def principal() -> Principal:
        return _CURRENT_PRINCIPAL.get() or default_principal

    @server.tool(
        name="create_travel_plan",
        description="创建旅行输入并提交异步 Agent Run，返回显式 RunHandle。",
        structured_output=True,
    )
    async def create_travel_plan(request: PlanningRequest) -> dict[str, Any]:
        service = service_getter()
        owner = principal()
        trip = await service.create_trip(request, principal=owner)
        handle = await service.start_trip_run(trip.trip_id, principal=owner)
        return handle.model_dump(mode="json")

    @server.tool(
        name="cancel_travel_run",
        description="显式取消仍在当前 Worker 执行的旅行 Run。",
        structured_output=True,
    )
    async def cancel_travel_run(run_id: str) -> dict[str, Any]:
        result = await service_getter().cancel_run(run_id, principal=principal())
        return result.model_dump(mode="json")

    @server.tool(
        name="resume_travel_run",
        description="恢复计划生命周期 Interrupt；需要显式 session_id、interrupt_id 与幂等 request_id。",
        structured_output=True,
    )
    async def resume_travel_run(
        session_id: str, request: LifecycleResumeRequest
    ) -> dict[str, Any]:
        result = await service_getter().resume_plan_session(
            session_id, request, principal=principal()
        )
        return result.model_dump(mode="json")

    @server.tool(
        name="select_plan_candidate",
        description="选择候选计划并恢复 Candidate Selection Interrupt。",
        structured_output=True,
    )
    async def select_plan_candidate(
        session_id: str,
        interrupt_id: str,
        request_id: str,
        candidate_id: str,
        expected_session_revision: int | None = None,
    ) -> dict[str, Any]:
        request = LifecycleResumeRequest(
            interrupt_id=interrupt_id,
            request_id=request_id,
            expected_session_revision=expected_session_revision,
            action=SelectCandidateAction(candidate_id=candidate_id),
        )
        result = await service_getter().resume_plan_session(
            session_id, request, principal=principal()
        )
        return result.model_dump(mode="json")

    @server.tool(
        name="apply_plan_change",
        description="提交自然语言局部修改，生成受硬约束保护的 Preview。",
        structured_output=True,
    )
    async def apply_plan_change(
        session_id: str,
        interrupt_id: str,
        request_id: str,
        text: str,
        expected_active_version_id: str | None = None,
        expected_session_revision: int | None = None,
    ) -> dict[str, Any]:
        request = LifecycleResumeRequest(
            interrupt_id=interrupt_id,
            request_id=request_id,
            expected_active_version_id=expected_active_version_id,
            expected_session_revision=expected_session_revision,
            action=TextEditAction(text=text),
        )
        result = await service_getter().resume_plan_session(
            session_id, request, principal=principal()
        )
        return result.model_dump(mode="json")

    @server.tool(
        name="approve_plan_change",
        description="使用 Preview Approval Token 显式批准局部变更。",
        structured_output=True,
    )
    async def approve_plan_change(
        session_id: str,
        interrupt_id: str,
        request_id: str,
        preview_id: str,
        approval_token: str,
        expected_active_version_id: str | None = None,
        expected_session_revision: int | None = None,
    ) -> dict[str, Any]:
        request = LifecycleResumeRequest(
            interrupt_id=interrupt_id,
            request_id=request_id,
            expected_active_version_id=expected_active_version_id,
            expected_session_revision=expected_session_revision,
            action=ApprovalAction(
                preview_id=preview_id, approval_token=approval_token
            ),
        )
        result = await service_getter().resume_plan_session(
            session_id, request, principal=principal()
        )
        return result.model_dump(mode="json")

    @server.tool(
        name="get_plan_diff",
        description="读取两个计划版本之间的结构化 Diff。",
        structured_output=True,
    )
    async def get_plan_diff(
        session_id: str, from_version_id: str, to_version_id: str
    ) -> dict[str, Any]:
        result = await service_getter().get_plan_diff(
            session_id,
            from_version_id,
            to_version_id,
            principal=principal(),
        )
        return result.model_dump(mode="json")

    @server.tool(
        name="replay_execution_trace",
        description="读取脱敏后的公开执行轨迹，支持 sequence 游标续读。",
        structured_output=True,
    )
    async def replay_execution_trace(
        run_id: str, after_sequence: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        values = await service_getter().get_trace(
            run_id,
            principal=principal(),
            after_sequence=after_sequence,
            limit=min(max(limit, 1), 500),
        )
        return {
            "run_id": run_id,
            "events": [item.model_dump(mode="json") for item in values],
            "next_sequence": values[-1].sequence if len(values) == limit else None,
        }

    @server.tool(
        name="get_or_update_preferences",
        description="查询偏好；传入 create 时只写入显式用户偏好。",
        structured_output=True,
    )
    async def get_or_update_preferences(
        create: PreferenceCreateRequest | None = None,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        service = service_getter()
        memory = service.runtime.preference_service
        if memory is None:
            raise RuntimeError("preference memory is not configured")
        owner = principal()
        created = await memory.create_explicit(owner, create) if create else None
        result = await memory.list(owner, include_inactive=include_inactive)
        return {
            "created": created.model_dump(mode="json") if created else None,
            "preferences": result.model_dump(mode="json"),
        }

    @server.tool(
        name="search_poi",
        description="受限 POI 数据工具；需要 read:data scope。",
        structured_output=True,
    )
    async def search_poi(query: POISearchQuery) -> dict[str, Any]:
        owner = principal()
        _require_scope(owner, "read:data")
        result = await service_getter().runtime.gateway.search_pois(
            [query], ToolCallContext(thread_id=f"mcp:{uuid4()}")
        )
        return result[0].model_dump(mode="json")

    @server.tool(
        name="get_route",
        description="受限路线数据工具；需要 read:data scope。",
        structured_output=True,
    )
    async def get_route(query: RouteQuery) -> dict[str, Any]:
        owner = principal()
        _require_scope(owner, "read:data")
        result = await service_getter().runtime.gateway.get_routes(
            [query], ToolCallContext(thread_id=f"mcp:{uuid4()}")
        )
        return next(iter(result.values())).model_dump(mode="json")

    @server.tool(
        name="get_weather",
        description="受限标准化天气数据工具；需要 read:data scope。",
        structured_output=True,
    )
    async def get_weather(
        destination: str, start_date: date, end_date: date
    ) -> dict[str, Any]:
        owner = principal()
        _require_scope(owner, "read:data")
        gateway = service_getter().runtime.weather_gateway
        if gateway is None:
            raise RuntimeError("weather gateway is not configured")
        context = ToolCallContext(thread_id=f"mcp:{uuid4()}")
        location = await gateway.resolve_location(destination, context)
        if location.data is None:
            return {"location": location.model_dump(mode="json"), "forecast": None}
        forecast = await gateway.get_forecast(
            location.data,
            start_date=start_date,
            end_date=end_date,
            context=context,
        )
        return {
            "location": location.model_dump(mode="json"),
            "forecast": forecast.model_dump(mode="json"),
        }

    @server.resource(
        "travel://runs/{run_id}",
        name="travel-run",
        description="只读 Run 快照。",
        mime_type="application/json",
    )
    async def read_run(run_id: str) -> str:
        record = await service_getter().get_run(run_id, principal=principal())
        return record.model_dump_json()

    @server.resource(
        "travel://runs/{run_id}/trace",
        name="travel-run-trace",
        description="只读且脱敏的完整 Run 轨迹快照。",
        mime_type="application/json",
    )
    async def read_trace(run_id: str) -> str:
        values = await service_getter().get_trace(
            run_id, principal=principal(), limit=500
        )
        return "[" + ",".join(item.model_dump_json() for item in values) + "]"

    @server.resource(
        "travel://plans/{session_id}",
        name="travel-plan-session",
        description="当前计划会话的只读公开快照。",
        mime_type="application/json",
    )
    async def read_plan(session_id: str) -> str:
        result = await service_getter().get_plan_session(
            session_id, principal=principal()
        )
        return result.model_dump_json()

    @server.resource(
        "travel://users/me/preferences",
        name="my-travel-preferences",
        description="当前用户的结构化偏好快照。",
        mime_type="application/json",
    )
    async def read_preferences() -> str:
        memory = service_getter().runtime.preference_service
        if memory is None:
            raise RuntimeError("preference memory is not configured")
        return (await memory.list(principal())).model_dump_json()

    return server


def build_http_mcp_app(
    service_getter: Callable[[], TravelApplicationService],
    *,
    default_principal: Principal,
    allow_default_identity: bool = True,
    allowed_hosts: tuple[str, ...] = (
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
        "testserver",
    ),
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
    ),
) -> tuple[FastMCP, ASGIApp]:
    server = build_mcp_server(
        service_getter,
        default_principal=default_principal,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(allowed_hosts),
            allowed_origins=list(allowed_origins),
        ),
    )
    return server, MCPIdentityMiddleware(
        server.streamable_http_app(),
        default_principal,
        allow_default_identity=allow_default_identity,
    )


def create_stdio_server(settings: Settings | None = None) -> FastMCP:
    resolved = settings or Settings.from_env()
    holder: dict[str, TravelApplicationService] = {}

    @asynccontextmanager
    async def lifespan(_: object) -> AsyncIterator[dict[str, object]]:
        runtime = await PlanningRuntime.create(resolved)
        service = TravelApplicationService(runtime)
        holder["service"] = service
        try:
            yield {}
        finally:
            await service.close()
            await runtime.close()

    default = Principal(
        tenant_id=os.getenv("MCP_TENANT_ID", resolved.dev_tenant_id),
        user_id=os.getenv("MCP_USER_ID", resolved.dev_user_id),
        scopes=frozenset(
            item.strip()
            for item in os.getenv("MCP_SCOPES", "read:data").split(",")
            if item.strip()
        ),
        authentication_method="mcp_stdio_env",
    )
    return build_mcp_server(
        lambda: holder["service"],
        default_principal=default,
        stateless_http=False,
        lifespan=lifespan,
    )


def main() -> None:
    create_stdio_server().run(transport="stdio")


def _require_scope(principal: Principal, scope: str) -> None:
    if not principal.can(scope):
        raise PermissionError(f"missing required scope: {scope}")


if __name__ == "__main__":
    main()
