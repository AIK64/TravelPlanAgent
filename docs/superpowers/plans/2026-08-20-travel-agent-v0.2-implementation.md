# Travel Agent v0.2 Tool Use Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a provider-independent, reliable, observable Tool Use path that lets the travel Agent retrieve POIs and driving routes, write standardized results into LangGraph State, and use validation feedback to select, replan, or stop.

**Architecture:** FastAPI creates one runtime containing the selected async Provider pair, a ToolGateway, and a dependency-injected LangGraph workflow. The Graph exposes `Search Intent → Tool Use → State Update → Plan → Validate → Replan` as explicit nodes; Providers perform one supplier call, while the Gateway owns caching, concurrency, retry, error classification metadata, and tool logs.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, LangGraph 1.x, httpx, pytest, pytest-asyncio, pytest-cov.

**Spec:** `docs/superpowers/specs/2026-08-19-travel-agent-v0.2-design.md`

## Global Constraints

- Agent behavior is the product: every integration must expose Tool Use, State changes, conditional routing, validation feedback, and bounded Replan in tests or logs.
- Default `TRAVEL_PROVIDER=mock`; `TRAVEL_PROVIDER=amap` requires `AMAP_API_KEY` and never falls back to Mock.
- v0.2 route mode is driving only, with default AMap `strategy=32`.
- AMap retry exhaustion is infrastructure failure and maps to HTTP 503; it must never become `infeasible`.
- Retryable failures: transport failures, HTTP 429/502/503/504, and AMap 10004/10015/10016/10017/10019/10020/10021.
- Non-retryable failures include invalid credentials/permissions/parameters and daily quota code 10003.
- Default total attempts: 3; base backoff: 0.25 seconds; maximum backoff: 2 seconds; jitter enabled.
- Default maximum concurrent tool calls: 5; POI TTL: 3600 seconds; route TTL: 300 seconds; cache capacity: 2048.
- Default per-query POI limit: 10; merged planning pool limit: 12.
- Default missing-fact policy is `assume_with_warning`; conservative hours are 10:00–16:00 and duration is 90 minutes; unknown cost remains `None`.
- State never contains API keys, HTTP clients, Provider objects, raw AMap JSON, complete headers, or exception objects.
- Ordinary tests never use live network; optional live smoke tests are skipped unless explicitly enabled.
- Branch coverage remains at least 90%; all existing v0.1 behavior remains regression-tested.
- Preserve all pre-existing working-tree changes. Execution must begin with `superpowers:using-git-worktrees`; if dirty v0.1 changes are not on the worktree base, checkpoint them before creating the v0.2 worktree.

---

## File Structure

### New runtime and tool files

```text
src/travel_agent/config.py                  Environment parsing and startup validation
src/travel_agent/runtime.py                 Provider/Gateway/Workflow lifecycle assembly
src/travel_agent/domain/tool_models.py      Supplier-neutral query, fact, route, and result models
src/travel_agent/planning/search_plan.py    Deterministic search-intent construction
src/travel_agent/planning/defaults.py       Missing-fact resolution policy
src/travel_agent/planning/drafts.py         Candidate drafts and route-segment collection
src/travel_agent/tools/errors.py             Typed provider and unavailable errors
src/travel_agent/tools/protocols.py          Async POIProvider and RouteProvider contracts
src/travel_agent/tools/cache.py              Bounded async TTL cache with per-key locking
src/travel_agent/tools/retry.py              Bounded exponential retry policy
src/travel_agent/tools/gateway.py            Reliable Tool Use orchestration and metadata
src/travel_agent/tools/providers/mock.py     Mock protocol adapters
src/travel_agent/tools/providers/amap.py     AMap POI and driving-route adapters
```

### Modified production files

```text
pyproject.toml                               Version and runtime/test dependencies
.env.example                                Provider and reliability configuration
src/travel_agent/__init__.py                Version 0.2.0
src/travel_agent/domain/models.py            Planning facts, assumptions, costs, validation status
src/travel_agent/planning/planner.py          Route-aware candidate materialization
src/travel_agent/planning/validator.py        Warning-aware validity and unknown-cost handling
src/travel_agent/graph/state.py               Tool/search/draft/route State fields
src/travel_agent/graph/workflow.py            Async Tool Use nodes and bounded planning loop
src/travel_agent/api/dependencies.py          Runtime lookup from FastAPI request
src/travel_agent/api/errors.py                ToolUnavailableError → HTTP 503 mapping
src/travel_agent/api/routes.py                Async planning endpoint
src/travel_agent/app.py                       Lifespan-managed runtime
src/travel_agent/logging_config.py            Existing logging configuration retained
```

### New and modified tests

```text
tests/fixtures/amap/poi_success.json
tests/fixtures/amap/poi_empty.json
tests/fixtures/amap/route_success.json
tests/fixtures/amap/server_busy.json
tests/test_config.py
tests/test_search_plan.py
tests/test_defaults.py
tests/test_mock_providers.py
tests/test_tool_cache.py
tests/test_retry.py
tests/test_tool_gateway.py
tests/test_amap_provider.py
tests/test_route_aware_planner.py
tests/test_agent_trajectory.py
tests/test_api.py
tests/test_logging.py
tests/test_workflow.py
```

### v0.2 learning documentation

```text
docs/v0.2/README.md
docs/v0.2/01-architecture.md
docs/v0.2/02-provider-contracts.md
docs/v0.2/03-tool-gateway-reliability.md
docs/v0.2/04-async-langgraph-flow.md
docs/v0.2/05-running-and-testing.md
docs/v0.2/06-learning-guide.md
```

---

### Task 1: Supplier-neutral tool models, contracts, and settings

**Files:**
- Create: `src/travel_agent/config.py`
- Create: `src/travel_agent/domain/tool_models.py`
- Create: `src/travel_agent/tools/__init__.py`
- Create: `src/travel_agent/tools/errors.py`
- Create: `src/travel_agent/tools/protocols.py`
- Create: `tests/test_config.py`
- Create: `tests/test_tool_models.py`
- Modify: `pyproject.toml:7-24`
- Modify: `.env.example`

**Interfaces:**
- Produces: `Settings.from_env(env: Mapping[str, str] | None = None) -> Settings`
- Produces: `POIProvider.search_pois(query: POISearchQuery) -> list[POIFacts]`
- Produces: `RouteProvider.get_driving_route(query: RouteQuery) -> RouteResult`
- Produces: `ToolProviderError`, `ToolUnavailableError`, and all supplier-neutral tool models used by later tasks.

- [ ] **Step 1: Add the runtime and async-test dependencies**

Change `pyproject.toml` to version `0.2.0`, move `httpx>=0.27,<1` into `[project].dependencies`, and add `pytest-asyncio>=0.24,<1` to `dev`. Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Expected: editable install succeeds and `pip show pytest-asyncio` reports an installed package.

- [ ] **Step 2: Write failing configuration tests**

```python
from travel_agent.config import Settings
from travel_agent.domain.tool_models import ProviderMode


def test_settings_default_to_mock():
    settings = Settings.from_env({})
    assert settings.provider is ProviderMode.MOCK
    assert settings.tool_max_attempts == 3
    assert settings.poi_candidate_limit == 12


def test_amap_requires_key():
    with pytest.raises(ValueError, match="AMAP_API_KEY"):
        Settings.from_env({"TRAVEL_PROVIDER": "amap"})


def test_amap_accepts_non_empty_key():
    settings = Settings.from_env(
        {"TRAVEL_PROVIDER": "amap", "AMAP_API_KEY": "test-key"}
    )
    assert settings.provider is ProviderMode.AMAP
    assert settings.amap_api_key == "test-key"
```

- [ ] **Step 3: Run the configuration tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py -v
```

Expected: collection fails because `travel_agent.config` does not exist.

- [ ] **Step 4: Implement the exact settings boundary**

```python
@dataclass(frozen=True, slots=True)
class Settings:
    provider: ProviderMode = ProviderMode.MOCK
    amap_api_key: str | None = None
    tool_timeout_seconds: float = 5.0
    tool_max_attempts: int = 3
    tool_backoff_base_seconds: float = 0.25
    tool_max_backoff_seconds: float = 2.0
    tool_max_concurrency: int = 5
    tool_cache_max_entries: int = 2048
    poi_cache_ttl_seconds: int = 3600
    route_cache_ttl_seconds: int = 300
    poi_query_limit: int = 10
    poi_candidate_limit: int = 12
    unknown_fact_policy: UnknownFactPolicy = UnknownFactPolicy.ASSUME_WITH_WARNING
    amap_driving_strategy: int = 32

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env
        settings = cls(
            provider=ProviderMode(source.get("TRAVEL_PROVIDER", "mock").strip().lower()),
            amap_api_key=source.get("AMAP_API_KEY", "").strip() or None,
            tool_timeout_seconds=float(source.get("TOOL_TIMEOUT_SECONDS", "5")),
            tool_max_attempts=int(source.get("TOOL_MAX_ATTEMPTS", "3")),
            tool_backoff_base_seconds=float(source.get("TOOL_BACKOFF_BASE_SECONDS", "0.25")),
            tool_max_backoff_seconds=float(source.get("TOOL_MAX_BACKOFF_SECONDS", "2")),
            tool_max_concurrency=int(source.get("TOOL_MAX_CONCURRENCY", "5")),
            tool_cache_max_entries=int(source.get("TOOL_CACHE_MAX_ENTRIES", "2048")),
            poi_cache_ttl_seconds=int(source.get("POI_CACHE_TTL_SECONDS", "3600")),
            route_cache_ttl_seconds=int(source.get("ROUTE_CACHE_TTL_SECONDS", "300")),
            poi_query_limit=int(source.get("POI_QUERY_LIMIT", "10")),
            poi_candidate_limit=int(source.get("POI_CANDIDATE_LIMIT", "12")),
            unknown_fact_policy=UnknownFactPolicy(
                source.get("UNKNOWN_FACT_POLICY", "assume_with_warning").strip().lower()
            ),
            amap_driving_strategy=int(source.get("AMAP_DRIVING_STRATEGY", "32")),
        )
        settings.validate()
        return settings
```

`from_env` must trim strings, parse numeric values, reject non-positive timeouts/concurrency/TTLs, reject attempts below 1, and raise `ValueError("AMAP_API_KEY is required when TRAVEL_PROVIDER=amap")` for missing AMap credentials. Do not add `pydantic-settings`.

- [ ] **Step 5: Write failing tool-model tests**

```python
def test_route_key_is_stable_and_directional():
    forward = RouteQuery(origin=A, destination=B)
    reverse = RouteQuery(origin=B, destination=A)
    assert route_key(forward) == route_key(forward.model_copy())
    assert route_key(forward) != route_key(reverse)


def test_tool_result_never_requires_raw_payload():
    result = ToolResult[list[POIFacts]](
        status=ToolStatus.SUCCESS,
        data=[],
        provider="mock",
        fetched_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        cache_hit=False,
        attempt_count=1,
    )
    assert "raw" not in result.model_dump()
```

- [ ] **Step 6: Implement models, typed errors, and Protocols**

Define these exact public names in `domain/tool_models.py`:

```python
class ProviderMode(StrEnum): MOCK = "mock"; AMAP = "amap"
class RouteMode(StrEnum): DRIVING = "driving"
class ToolStatus(StrEnum): SUCCESS = "success"; FAILED = "failed"
class ValueSource(StrEnum): PROVIDER = "provider"; DERIVED = "derived"; DEFAULT = "default"; USER_CONFIRMED = "user_confirmed"
class UnknownFactPolicy(StrEnum): ASSUME_WITH_WARNING = "assume_with_warning"; STRICT = "strict"
class ToolErrorCategory(StrEnum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"

class POISearchQuery(BaseModel):
    city: str
    keyword: str
    exact_match: bool = False
    limit: int = Field(default=10, ge=1, le=25)
    priority: int = 0

class POIFacts(BaseModel):
    id: str
    name: str
    city: str
    coordinate: Coordinate
    categories: list[str]
    opening_windows_by_weekday: dict[int, TimeWindow] = Field(default_factory=dict)
    today_opening_window: TimeWindow | None = None
    today_opening_date: date | None = None
    average_cost_per_person: Decimal | None = Field(default=None, ge=0)
    suggested_duration_minutes: int | None = Field(default=None, gt=0)
    provider: str
    fetched_at: datetime
    data_confidence: float = Field(default=1.0, ge=0, le=1)
    field_sources: dict[str, ValueSource] = Field(default_factory=dict)

class RouteQuery(BaseModel):
    origin: Coordinate
    destination: Coordinate
    origin_poi_id: str | None = None
    destination_poi_id: str | None = None
    mode: RouteMode = RouteMode.DRIVING
    strategy: int = 32

class RouteResult(BaseModel):
    distance_meters: int = Field(gt=0)
    duration_minutes: int = Field(gt=0)
    mode: RouteMode = RouteMode.DRIVING
    provider: str
    data_confidence: float = Field(ge=0, le=1)
    fetched_at: datetime
```

Also define these exact result models:

```python
class ToolErrorInfo(BaseModel):
    category: ToolErrorCategory
    code: str
    operation: str
    retryable: bool
    safe_message: str

class ToolResult(BaseModel, Generic[T]):
    status: ToolStatus
    data: T | None = None
    provider: str
    fetched_at: datetime | None = None
    expires_at: datetime | None = None
    cache_hit: bool = False
    attempt_count: int = Field(default=0, ge=0)
    elapsed_ms: float | None = Field(default=None, ge=0)
    error: ToolErrorInfo | None = None

class ToolCallContext(BaseModel):
    thread_id: str

class ToolExecutionSummary(BaseModel):
    provider: str
    operation: str
    status: ToolStatus
    cache_hit: bool
    attempt_count: int
```

Add `ToolResult.success` and `ToolResult.failed` class methods that enforce data for success and error for failure. Add `ToolErrorInfo.from_provider_error(error)`. Define `route_key(query) -> str` using six-decimal directional coordinates plus mode and strategy.

In `tools/errors.py`, implement `ToolProviderError` as a dataclass exception with `category`, `code`, `operation`, `retryable`, `safe_message`, and optional `retry_after_seconds`; add `timeout(operation)` and `authentication(operation)` factories used by tests. Implement `ToolUnavailableError.from_result(result, thread_id)` and `safe_detail()` without raw payload fields. In `tools/protocols.py`, add runtime-checkable async Protocols with the signatures in **Interfaces**.

```python
@runtime_checkable
class POIProvider(Protocol):
    name: str
    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        raise NotImplementedError

@runtime_checkable
class RouteProvider(Protocol):
    name: str
    async def get_driving_route(self, query: RouteQuery) -> RouteResult:
        raise NotImplementedError
```

- [ ] **Step 7: Run focused and full regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_tool_models.py -v
.\.venv\Scripts\python.exe -m pytest
```

Expected: new tests pass and the current 11-test baseline remains green.

- [ ] **Step 8: Commit the foundation**

```powershell
git add pyproject.toml .env.example src/travel_agent/config.py src/travel_agent/domain/tool_models.py src/travel_agent/tools tests/test_config.py tests/test_tool_models.py
git commit -m "feat: define v0.2 tool contracts and settings"
```

---

### Task 2: Deterministic search intent and missing-fact resolution

**Files:**
- Create: `src/travel_agent/planning/search_plan.py`
- Create: `src/travel_agent/planning/defaults.py`
- Create: `tests/test_search_plan.py`
- Create: `tests/test_defaults.py`
- Modify: `src/travel_agent/domain/models.py:110-123`

**Interfaces:**
- Consumes: `POISearchQuery`, `POIFacts`, `UnknownFactPolicy`, and `ValueSource` from Task 1.
- Produces: `build_search_plan(trip: TripSpec, per_query_limit: int = 10) -> list[POISearchQuery]`.
- Produces: `POIDefaultPolicy.resolve(facts: POIFacts, trip: TripSpec) -> POIResolution`.
- Produces: `PlanningPOI`, `PlanningAssumption`, and `POIResolution` models.

- [ ] **Step 1: Write the failing search-intent tests**

```python
def test_must_visit_queries_precede_interests(hangzhou_trip):
    queries = build_search_plan(hangzhou_trip, per_query_limit=10)
    assert queries[0].keyword == "灵隐寺"
    assert queries[0].exact_match is True
    assert [query.keyword for query in queries[1:]] == ["自然", "美食", "人文"]


def test_empty_preferences_use_scenic_default(hangzhou_trip):
    trip = hangzhou_trip.model_copy(update={"must_visit": [], "interests": []})
    assert [query.keyword for query in build_search_plan(trip)] == ["景点"]
```

- [ ] **Step 2: Run search tests and verify RED**

Run `\.venv\Scripts\python.exe -m pytest tests/test_search_plan.py -v`.

Expected: import failure for `planning.search_plan`.

- [ ] **Step 3: Implement deterministic query construction**

```python
def build_search_plan(trip: TripSpec, per_query_limit: int = 10) -> list[POISearchQuery]:
    seen: set[str] = set()
    queries: list[POISearchQuery] = []
    for keyword, exact, priority in [
        *((name, True, 100) for name in trip.must_visit),
        *((interest, False, 50) for interest in trip.interests),
    ]:
        normalized = keyword.strip().casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            queries.append(POISearchQuery(
                city=trip.destination,
                keyword=keyword.strip(),
                exact_match=exact,
                limit=per_query_limit,
                priority=priority,
            ))
    if not queries:
        queries.append(POISearchQuery(
            city=trip.destination, keyword="景点", limit=per_query_limit, priority=10
        ))
    return queries
```

- [ ] **Step 4: Write failing default-policy tests**

```python
def test_assume_policy_marks_missing_hours_duration_and_cost(hangzhou_trip, poi_facts):
    resolution = POIDefaultPolicy(UnknownFactPolicy.ASSUME_WITH_WARNING).resolve(
        poi_facts, hangzhou_trip
    )
    assert resolution.poi is not None
    assert resolution.poi.opening_windows[hangzhou_trip.start_date] == TimeWindow(
        start=time(10), end=time(16)
    )
    assert resolution.poi.duration_minutes == 90
    assert resolution.poi.party_cost is None
    assert {item.field for item in resolution.poi.assumptions} == {
        "opening_window", "duration_minutes", "party_cost"
    }


def test_strict_policy_rejects_missing_critical_facts(hangzhou_trip, poi_facts):
    resolution = POIDefaultPolicy(UnknownFactPolicy.STRICT).resolve(
        poi_facts, hangzhou_trip
    )
    assert resolution.poi is None
    assert resolution.missing_fields == [
        "opening_window", "duration_minutes", "party_cost"
    ]
```

- [ ] **Step 5: Implement provenance-aware planning models and policy**

Add to `domain/models.py`:

```python
class PlanningAssumption(BaseModel):
    field: str
    value: str
    reason: str
    policy_version: str = "v0.2-default-1"
    created_at: datetime

class PlanningPOI(BaseModel):
    facts: POIFacts
    opening_windows: dict[date, TimeWindow]
    duration_minutes: int
    party_cost: Decimal | None
    assumptions: list[PlanningAssumption] = Field(default_factory=list)
    data_confidence: float = Field(ge=0, le=1)

class POIResolution(BaseModel):
    poi: PlanningPOI | None
    missing_fields: list[str] = Field(default_factory=list)

class POIResolutionIssue(BaseModel):
    poi_id: str
    poi_name: str
    missing_fields: list[str]
    required: bool = False
```

`POIDefaultPolicy.resolve` must build `opening_windows` for every date from `trip.start_date` through `trip.end_date`, choose weekly hours by `trip_date.weekday()`, accept `today_opening_window` only when `today_opening_date == trip_date`, otherwise use 10:00–16:00 in assume mode. It must use 90 minutes when duration is absent, multiply known per-person cost by `trip.travelers`, keep unknown cost as `None`, append one assumption per substituted/unknown field, and reduce `facts.data_confidence` by `0.15` per assumption with a floor of `0.1`.

- [ ] **Step 6: Run focused and regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_search_plan.py tests/test_defaults.py -v
.\.venv\Scripts\python.exe -m pytest
```

Expected: all tests pass.

- [ ] **Step 7: Commit search intent and data policy**

```powershell
git add src/travel_agent/domain/models.py src/travel_agent/planning/search_plan.py src/travel_agent/planning/defaults.py tests/test_search_plan.py tests/test_defaults.py
git commit -m "feat: add search intent and POI fact policy"
```

---

### Task 3: Mock Provider adapters

**Files:**
- Create: `src/travel_agent/tools/providers/__init__.py`
- Create: `src/travel_agent/tools/providers/mock.py`
- Create: `tests/test_mock_providers.py`
- Modify: `src/travel_agent/planning/mock_data.py`

**Interfaces:**
- Consumes: Task 1 Protocols and Task 1 tool models.
- Produces: `MockPOIProvider.search_pois` and `MockRouteProvider.get_driving_route`.
- Preserves: current Hangzhou Mock dataset and deterministic route behavior.

- [ ] **Step 1: Write failing async protocol tests**

```python
@pytest.mark.asyncio
async def test_mock_poi_provider_filters_city_and_keyword():
    provider = MockPOIProvider()
    results = await provider.search_pois(
        POISearchQuery(city="杭州", keyword="自然", limit=10)
    )
    assert results
    assert all(item.city == "杭州" for item in results)
    assert any("自然" in item.categories for item in results)
    assert all(item.provider == "mock" for item in results)


@pytest.mark.asyncio
async def test_mock_route_provider_returns_driving_result():
    result = await MockRouteProvider().get_driving_route(RouteQuery(origin=A, destination=B))
    assert result.distance_meters > 0
    assert result.duration_minutes > 0
    assert result.mode is RouteMode.DRIVING
```

- [ ] **Step 2: Verify RED**

Run `\.venv\Scripts\python.exe -m pytest tests/test_mock_providers.py -v`.

Expected: import failure for `tools.providers.mock`.

- [ ] **Step 3: Implement Mock adapters without Graph changes**

`MockPOIProvider` must map existing `POI` records into `POIFacts`, use the current opening window as weekday facts for all seven days, treat current cost as per-person Mock cost, preserve categories and coordinates, and filter normalized keyword against name/categories/tags. Exact-name queries must prefer name matches but remain deterministic.

`MockRouteProvider` must call the current `estimate_route`, map road distance and travel minutes into `RouteResult`, set `provider="mock"`, and set `data_confidence=0.65` because the route is estimated.

- [ ] **Step 4: Run focused and full tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_mock_providers.py -v
.\.venv\Scripts\python.exe -m pytest
```

Expected: all tests pass; the Graph still uses its old Mock path until Task 11.

- [ ] **Step 5: Commit Mock adapters**

```powershell
git add src/travel_agent/tools/providers src/travel_agent/planning/mock_data.py tests/test_mock_providers.py
git commit -m "feat: adapt mock data to provider protocols"
```

---

### Task 4: Bounded async TTL cache

**Files:**
- Create: `src/travel_agent/tools/cache.py`
- Create: `tests/test_tool_cache.py`

**Interfaces:**
- Produces: `AsyncTTLCache[T](max_entries: int, clock: Callable[[], float])`.
- Produces: `get_or_load(key, ttl_seconds, loader, should_cache=lambda value: True) -> CacheLookup[T]`.
- Produces: `CacheLookup(value: T, hit: bool, expires_at_monotonic: float)`.

- [ ] **Step 1: Write failing cache tests**

```python
@pytest.mark.asyncio
async def test_cache_loads_once_then_hits():
    calls = 0
    async def loader():
        nonlocal calls
        calls += 1
        return "value"
    cache = AsyncTTLCache[str](max_entries=10, clock=lambda: 100.0)
    first = await cache.get_or_load("k", 30, loader)
    second = await cache.get_or_load("k", 30, loader)
    assert (first.hit, second.hit, calls) == (False, True, 1)


@pytest.mark.asyncio
async def test_concurrent_same_key_uses_single_loader():
    calls = 0
    async def loader():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return "value"
    cache = AsyncTTLCache[str](max_entries=10)
    results = await asyncio.gather(*[
        cache.get_or_load("same", 30, loader) for _ in range(8)
    ])
    assert calls == 1
    assert sum(result.hit for result in results) == 7
```

- [ ] **Step 2: Verify RED**

Run `\.venv\Scripts\python.exe -m pytest tests/test_tool_cache.py -v`.

Expected: import failure for `tools.cache`.

- [ ] **Step 3: Implement cache semantics**

Use a dictionary of `_CacheEntry(value, expires_at, inserted_at)` and a per-key `asyncio.Lock`. `get_or_load` must re-check the cache after acquiring the lock, never retain loader exceptions, store the loaded value only when `should_cache(value)` is true, remove expired entries before capacity eviction, and evict the entry with the earliest `(expires_at, inserted_at)` when capacity is exceeded. Remove an unused per-key lock after the protected load completes.

- [ ] **Step 4: Add expiry, failure, and capacity tests**

```python
@pytest.mark.asyncio
async def test_expired_value_is_reloaded():
    now = [100.0]
    calls = 0
    async def loader():
        nonlocal calls
        calls += 1
        return calls
    cache = AsyncTTLCache[int](max_entries=2, clock=lambda: now[0])
    assert (await cache.get_or_load("k", 5, loader)).value == 1
    now[0] = 106.0
    assert (await cache.get_or_load("k", 5, loader)).value == 2

@pytest.mark.asyncio
async def test_loader_failure_is_not_cached():
    calls = 0
    async def loader():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return "ok"
    cache = AsyncTTLCache[str](max_entries=2)
    with pytest.raises(RuntimeError, match="boom"):
        await cache.get_or_load("k", 5, loader)
    assert (await cache.get_or_load("k", 5, loader)).value == "ok"
    assert calls == 2

@pytest.mark.asyncio
async def test_capacity_evicts_oldest_entry():
    now = [100.0]
    cache = AsyncTTLCache[str](max_entries=1, clock=lambda: now[0])
    await cache.get_or_load("first", 30, lambda: async_value("one"))
    now[0] = 101.0
    await cache.get_or_load("second", 30, lambda: async_value("two"))
    reloaded = await cache.get_or_load("first", 30, lambda: async_value("new"))
    assert reloaded.hit is False
    assert reloaded.value == "new"
```

Define `async_value(value)` in the test module as an async function returning `value`.

- [ ] **Step 5: Run cache tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tool_cache.py -v
```

Expected: all cache tests pass without network or real waiting.

- [ ] **Step 6: Commit cache**

```powershell
git add src/travel_agent/tools/cache.py tests/test_tool_cache.py
git commit -m "feat: add bounded async tool cache"
```

---

### Task 5: Retry policy and typed exhaustion

**Files:**
- Create: `src/travel_agent/tools/retry.py`
- Create: `tests/test_retry.py`
- Modify: `src/travel_agent/tools/errors.py`

**Interfaces:**
- Consumes: `ToolProviderError(retryable: bool, retry_after_seconds: float | None)`.
- Produces: `RetryPolicy.execute(call, on_retry=None) -> RetryOutcome[T]`.
- Produces: `ToolRetryExhausted(last_error: ToolProviderError, attempts: int)`.

- [ ] **Step 1: Write failing retry tests**

```python
async def record_sleep(sleeps: list[float], delay: float) -> None:
    sleeps.append(delay)

async def raise_async(error: Exception):
    raise error

@pytest.mark.asyncio
async def test_retryable_error_uses_backoff_then_succeeds():
    attempts = 0
    sleeps: list[float] = []
    async def call():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ToolProviderError.timeout("route")
        return "ok"
    policy = RetryPolicy(
        max_attempts=3,
        base_delay_seconds=0.25,
        max_delay_seconds=2,
        sleeper=lambda delay: record_sleep(sleeps, delay),
        jitter=lambda: 0.0,
    )
    outcome = await policy.execute(call)
    assert outcome.value == "ok"
    assert outcome.attempts == 3
    assert sleeps == [0.25, 0.5]


@pytest.mark.asyncio
async def test_non_retryable_error_stops_after_one_attempt():
    policy = RetryPolicy(max_attempts=3, sleeper=AsyncMock(), jitter=lambda: 0.0)
    with pytest.raises(ToolRetryExhausted) as captured:
        await policy.execute(lambda: raise_async(ToolProviderError.authentication("poi")))
    assert captured.value.attempts == 1
```

- [ ] **Step 2: Verify RED**

Run `\.venv\Scripts\python.exe -m pytest tests/test_retry.py -v`.

Expected: import failure for `tools.retry`.

- [ ] **Step 3: Implement bounded exponential retry**

```python
@dataclass(frozen=True, slots=True)
class RetryOutcome(Generic[T]):
    value: T
    attempts: int

class RetryPolicy:
    async def execute(
        self,
        call: Callable[[], Awaitable[T]],
        on_retry: Callable[[RetryEvent], Awaitable[None]] | None = None,
    ) -> RetryOutcome[T]:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return RetryOutcome(value=await call(), attempts=attempt)
            except ToolProviderError as error:
                if not error.retryable or attempt == self.max_attempts:
                    raise ToolRetryExhausted(error, attempt) from error
                exponential = min(
                    self.max_delay_seconds,
                    self.base_delay_seconds * (2 ** (attempt - 1)),
                )
                requested = error.retry_after_seconds or 0.0
                delay = min(self.max_delay_seconds, max(exponential, requested))
                delay += min(1.0, max(0.0, self.jitter())) * exponential
                if on_retry is not None:
                    await on_retry(RetryEvent(
                        attempt=attempt,
                        next_attempt=attempt + 1,
                        delay_seconds=delay,
                        error=error,
                    ))
                await self.sleeper(delay)
```

Define frozen `RetryEvent(attempt, next_attempt, delay_seconds, error)`. The Retry Policy must not know HTTP or AMap codes; Providers classify them into `ToolProviderError` first.

- [ ] **Step 4: Add exhaustion and Retry-After tests**

Assert three failed calls produce `ToolRetryExhausted.attempts == 3`, and a `retry_after_seconds=1.5` error sleeps 1.5 seconds when the exponential delay is lower.

- [ ] **Step 5: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_retry.py -v
git add src/travel_agent/tools/errors.py src/travel_agent/tools/retry.py tests/test_retry.py
git commit -m "feat: add bounded tool retry policy"
```

---

### Task 6: ToolGateway reliability orchestration

**Files:**
- Create: `src/travel_agent/tools/gateway.py`
- Create: `tests/test_tool_gateway.py`
- Modify: `src/travel_agent/tools/cache.py`

**Interfaces:**
- Consumes: Provider Protocols, `AsyncTTLCache`, `RetryPolicy`, `ToolCallContext`, and `ToolResult`.
- Produces: `ToolGateway.search_pois(queries, context) -> list[ToolResult[list[POIFacts]]]`.
- Produces: `ToolGateway.get_routes(queries, context) -> dict[str, ToolResult[RouteResult]]` keyed by `route_key`.
- Produces: `build_gateway(settings, poi_provider, route_provider) -> ToolGateway`.
- Guarantees: no fallback Provider reference or branch exists.

- [ ] **Step 1: Write failing success, cache, and deduplication tests**

```python
@pytest.mark.asyncio
async def test_gateway_wraps_poi_result_and_hits_cache(fake_poi_provider):
    gateway = make_gateway(poi_provider=fake_poi_provider)
    context = ToolCallContext(thread_id="gateway-test")
    first = await gateway.search_pois([QUERY], context)
    second = await gateway.search_pois([QUERY], context)
    assert first[0].status is ToolStatus.SUCCESS
    assert first[0].cache_hit is False
    assert second[0].cache_hit is True
    assert fake_poi_provider.calls == 1


@pytest.mark.asyncio
async def test_gateway_deduplicates_route_queries(fake_route_provider):
    gateway = make_gateway(route_provider=fake_route_provider)
    results = await gateway.get_routes([ROUTE, ROUTE.model_copy()], CONTEXT)
    assert list(results) == [route_key(ROUTE)]
    assert fake_route_provider.calls == 1
```

- [ ] **Step 2: Run tests and verify RED**

Run `\.venv\Scripts\python.exe -m pytest tests/test_tool_gateway.py -v`.

Expected: import failure for `tools.gateway`.

- [ ] **Step 3: Implement one reliable execution path**

Implement one private generic method used by POI and route calls:

```python
async def _execute(
    self,
    *,
    cache_key: str,
    ttl_seconds: int,
    provider: str,
    operation: str,
    context: ToolCallContext,
    call: Callable[[], Awaitable[T]],
) -> ToolResult[T]:
    async def load() -> ToolResult[T]:
        started = perf_counter()
        async def one_attempt() -> T:
            async with self._semaphore:
                return await call()
        try:
            outcome = await self._retry.execute(
                one_attempt,
                on_retry=lambda event: self._log_retry(event, context, provider, operation),
            )
        except ToolRetryExhausted as exhausted:
            return ToolResult.failed(
                provider=provider,
                error=ToolErrorInfo.from_provider_error(exhausted.last_error),
                attempt_count=exhausted.attempts,
            )
        fetched_at = self._utcnow()
        return ToolResult.success(
            data=outcome.value,
            provider=provider,
            fetched_at=fetched_at,
            expires_at=fetched_at + timedelta(seconds=ttl_seconds),
            cache_hit=False,
            attempt_count=outcome.attempts,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
        )

    lookup = await self._cache.get_or_load(
        cache_key,
        ttl_seconds,
        load,
        should_cache=lambda result: result.status is ToolStatus.SUCCESS,
    )
    return lookup.value.model_copy(update={
        "cache_hit": lookup.hit,
        "attempt_count": 0 if lookup.hit else lookup.value.attempt_count,
        "elapsed_ms": 0.0 if lookup.hit else lookup.value.elapsed_ms,
    })
```

The semaphore protects each Provider attempt, not the retry sleep, so the permit is released before backoff. `build_gateway` uses all timeout-independent reliability values from `Settings`, one shared cache, one semaphore, and the selected Provider pair; it has no fallback argument.

Use exact cache keys:

```text
POI:   {provider}|poi|{city.casefold()}|{keyword.casefold()}|{exact_match}|{limit}
Route: {provider}|route|{route_key(query)}
```

- [ ] **Step 4: Write failure and concurrency tests**

```python
@pytest.mark.asyncio
async def test_gateway_returns_failed_tool_result_after_retry_exhaustion():
    provider = AlwaysTimeoutPOIProvider()
    result = (await make_gateway(poi_provider=provider).search_pois([QUERY], CONTEXT))[0]
    assert result.status is ToolStatus.FAILED
    assert result.attempt_count == 3
    assert result.error.category is ToolErrorCategory.TIMEOUT
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_gateway_never_exceeds_configured_concurrency():
    provider = MeasuringRouteProvider()
    gateway = make_gateway(route_provider=provider, max_concurrency=2)
    await gateway.get_routes(THREE_DISTINCT_ROUTES, CONTEXT)
    assert provider.maximum_active_calls == 2
```

Also assert a failed call is invoked again on the next request, proving failures are not cached.

- [ ] **Step 5: Add Tool Use logs**

Emit `tool.started`, `tool.cache_hit`, `tool.retry_scheduled`, `tool.completed`, and `tool.failed`. Each message must contain `thread_id`, `provider`, `operation`, and the relevant attempt/cache/elapsed fields. Add a callback from `RetryPolicy` to Gateway for `tool.retry_scheduled`; do not log call parameters or secrets.

- [ ] **Step 6: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tool_gateway.py tests/test_tool_cache.py tests/test_retry.py -v
git add src/travel_agent/tools/cache.py src/travel_agent/tools/gateway.py src/travel_agent/tools/retry.py tests/test_tool_gateway.py
git commit -m "feat: add reliable observable tool gateway"
```

---

### Task 7: AMap POI adapter and offline contract fixtures

**Files:**
- Create: `src/travel_agent/tools/providers/amap.py`
- Create: `tests/fixtures/amap/poi_success.json`
- Create: `tests/fixtures/amap/poi_empty.json`
- Create: `tests/fixtures/amap/server_busy.json`
- Create: `tests/test_amap_provider.py`

**Interfaces:**
- Consumes: `httpx.AsyncClient`, `POISearchQuery`, `POIFacts`, and `ToolProviderError`.
- Produces: `AMapPOIProvider(amap_client).search_pois(query)`.
- Produces: shared `AMapClient.request_json(operation, path, params) -> dict[str, object]` for Task 8.

- [ ] **Step 1: Add exact POI fixtures**

`poi_success.json`:

```json
{
  "status": "1",
  "info": "OK",
  "infocode": "10000",
  "count": "1",
  "pois": [{
    "id": "B0TEST001",
    "name": "测试博物馆",
    "location": "120.123456,30.123456",
    "type": "科教文化服务;博物馆",
    "cityname": "杭州市",
    "business": {
      "opentime_today": "09:00-17:00",
      "opentime_week": "周二至周日:09:00-17:00",
      "cost": "50"
    }
  }]
}
```

`poi_empty.json` uses the same success envelope with `"count":"0","pois":[]`. `server_busy.json` is `{"status":"0","info":"SERVER_IS_BUSY","infocode":"10016"}`.

- [ ] **Step 2: Write failing POI contract tests**

```python
@pytest.mark.asyncio
async def test_amap_poi_provider_normalizes_success(load_fixture):
    provider, seen = amap_poi_provider(load_fixture("poi_success.json"))
    facts = await provider.search_pois(
        POISearchQuery(city="杭州", keyword="博物馆", limit=10)
    )
    assert facts[0].id == "B0TEST001"
    assert facts[0].coordinate == Coordinate(longitude=120.123456, latitude=30.123456)
    assert facts[0].average_cost_per_person == Decimal("50")
    assert facts[0].provider == "amap"
    assert seen[0].url.path == "/v5/place/text"
    assert seen[0].url.params["city_limit"] == "true"
    assert seen[0].url.params["show_fields"] == "business"


@pytest.mark.asyncio
async def test_amap_empty_poi_response_is_success(load_fixture):
    provider, _ = amap_poi_provider(load_fixture("poi_empty.json"))
    assert await provider.search_pois(QUERY) == []
```

- [ ] **Step 3: Verify RED**

Run `\.venv\Scripts\python.exe -m pytest tests/test_amap_provider.py -k poi -v`.

Expected: import failure for `AMapPOIProvider`.

- [ ] **Step 4: Implement safe AMap request handling**

`AMapClient.request_json` must call `client.get(path, params={**params, "key": self.__api_key}, timeout=self.timeout_seconds)`, call `raise_for_status`, parse an object JSON body, require `status/info/infocode`, and classify errors:

```python
RETRYABLE_AMAP_CODES = {
    "10004", "10015", "10016", "10017", "10019", "10020", "10021"
}
AUTH_CODES = {"10001"}
PERMISSION_CODES = {"10002", "10005", "10009", "10012"}
RATE_LIMIT_CODES = {"10003", "10004", "10014", "10019", "10020", "10021"}
```

Map `httpx.TimeoutException`, `httpx.ConnectError`, HTTP 429, HTTP 502/503/504, invalid JSON, and invalid Schema into safe `ToolProviderError` values. Store the Key only in a name-mangled private attribute and never include the request URL or response body in exception messages.

- [ ] **Step 5: Implement POI normalization**

Parse `location` as longitude then latitude; split the semicolon-delimited type string; parse `business.cost` as non-negative Decimal or `None`; parse `opentime_today` only for `date.today()`; parse the common `周X至周Y:HH:MM-HH:MM` weekly form into weekday keys and leave unsupported descriptions empty. Set `suggested_duration_minutes=None` because AMap does not provide it.

- [ ] **Step 6: Add malformed and error tests**

Add parameterized cases asserting:

```python
(
    response_payload,
    expected_category,
    expected_retryable,
)
```

for invalid Key (`10001`, authentication, false), daily quota (`10003`, rate_limit, false), server busy (`10016`, upstream_unavailable, true), missing coordinates (invalid_response, false), HTTP 503 (upstream_unavailable, true), and timeout (timeout, true). Assert `"test-secret-key"` is absent from `str(error)`.

- [ ] **Step 7: Run POI contract tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_amap_provider.py -k "poi or error" -v
git add src/travel_agent/tools/providers/amap.py tests/fixtures/amap tests/test_amap_provider.py
git commit -m "feat: add AMap POI provider contract"
```

---

### Task 8: AMap driving-route adapter

**Files:**
- Modify: `src/travel_agent/tools/providers/amap.py`
- Create: `tests/fixtures/amap/route_success.json`
- Modify: `tests/test_amap_provider.py`

**Interfaces:**
- Consumes: Task 7 `AMapClient` and Task 1 `RouteQuery`.
- Produces: `AMapRouteProvider(amap_client).get_driving_route(query)`.

- [ ] **Step 1: Add an exact route fixture**

```json
{
  "status": "1",
  "info": "OK",
  "infocode": "10000",
  "route": {
    "origin": "120.100000,30.100000",
    "destination": "120.200000,30.200000",
    "paths": [{
      "distance": "8230",
      "cost": {"duration": "1260"}
    }]
  }
}
```

- [ ] **Step 2: Write the failing route contract test**

```python
@pytest.mark.asyncio
async def test_amap_route_provider_normalizes_distance_and_seconds(load_fixture):
    provider, seen = amap_route_provider(load_fixture("route_success.json"))
    result = await provider.get_driving_route(ROUTE_QUERY)
    assert result.distance_meters == 8230
    assert result.duration_minutes == 21
    assert result.provider == "amap"
    assert seen[0].url.path == "/v5/direction/driving"
    assert seen[0].url.params["strategy"] == "32"
```

- [ ] **Step 3: Verify RED**

Run `\.venv\Scripts\python.exe -m pytest tests/test_amap_provider.py::test_amap_route_provider_normalizes_distance_and_seconds -v`.

Expected: import or attribute failure for `AMapRouteProvider`.

- [ ] **Step 4: Implement driving-route parsing**

Format both coordinates with six decimals. Pass POI IDs only when present. Require at least one path, parse positive integer `distance`, parse positive seconds from `path.cost.duration`, and convert seconds with `math.ceil(seconds / 60)`. Set `mode=RouteMode.DRIVING`, `provider="amap"`, `data_confidence=0.95`, and a timezone-aware `fetched_at`.

- [ ] **Step 5: Add route error tests**

Test empty `paths`, missing `cost.duration`, zero distance, and a server-busy envelope. The first three must be non-retryable `INVALID_RESPONSE`; server busy must remain retryable.

- [ ] **Step 6: Run all AMap tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_amap_provider.py -v
git add src/travel_agent/tools/providers/amap.py tests/fixtures/amap/route_success.json tests/test_amap_provider.py
git commit -m "feat: add AMap driving route provider"
```

---

### Task 9: Warning-aware validation and truthful cost models

**Files:**
- Modify: `src/travel_agent/domain/models.py:125-182`
- Modify: `src/travel_agent/planning/validator.py`
- Modify: `tests/test_domain_models.py`
- Create: `tests/test_data_quality_validation.py`

**Interfaces:**
- Produces: `ValidationStatus.VALID`, `VALID_WITH_WARNINGS`, `INVALID`.
- Preserves: `ValidationResult.valid` as a serialized computed compatibility property.
- Changes: `PlanItem.estimated_cost: Decimal | None`.
- Adds: known-cost totals, unknown-cost counts, assumptions, and estimated-walking markers.

- [ ] **Step 1: Write failing validation-status tests**

```python
def test_warning_only_result_is_valid_with_warnings():
    result = ValidationResult.from_violations([
        Violation(
            type="opening_hours_unverified",
            severity=ViolationSeverity.WARNING,
            message="营业时间来自默认假设",
        )
    ])
    assert result.status is ValidationStatus.VALID_WITH_WARNINGS
    assert result.valid is True


def test_error_result_is_invalid():
    result = ValidationResult.from_violations([
        Violation(type="time_conflict", severity=ViolationSeverity.ERROR, message="冲突")
    ])
    assert result.status is ValidationStatus.INVALID
    assert result.valid is False
```

- [ ] **Step 2: Verify RED**

Run `\.venv\Scripts\python.exe -m pytest tests/test_data_quality_validation.py -v`.

Expected: missing `ValidationStatus` or `from_violations` failure.

- [ ] **Step 3: Implement compatible model changes**

Use `pydantic.computed_field` for `ValidationResult.valid`. Add:

```python
class ValidationStatus(StrEnum):
    VALID = "valid"
    VALID_WITH_WARNINGS = "valid_with_warnings"
    INVALID = "invalid"

class ValidationResult(BaseModel):
    status: ValidationStatus
    violations: list[Violation] = Field(default_factory=list)

    @computed_field
    @property
    def valid(self) -> bool:
        return self.status is not ValidationStatus.INVALID

    @classmethod
    def from_violations(cls, violations: list[Violation]) -> "ValidationResult":
        if any(item.severity is ViolationSeverity.ERROR for item in violations):
            status = ValidationStatus.INVALID
        elif violations:
            status = ValidationStatus.VALID_WITH_WARNINGS
        else:
            status = ValidationStatus.VALID
        return cls(status=status, violations=violations)
```

Apply these exact changes:

```python
class PlanItem(BaseModel):
    # existing fields remain
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    walking_distance_estimated: bool = False

class DayPlan(BaseModel):
    # existing fields remain
    known_estimated_cost: Decimal = Field(
        default=Decimal("0"),
        validation_alias=AliasChoices("known_estimated_cost", "estimated_cost"),
    )
    unknown_cost_item_count: int = Field(default=0, ge=0)

class PlanMetrics(BaseModel):
    # existing fields remain
    known_estimated_cost: Decimal = Field(
        validation_alias=AliasChoices("known_estimated_cost", "estimated_cost")
    )
    unknown_cost_item_count: int = Field(default=0, ge=0)

class PlanCandidate(BaseModel):
    # existing fields remain
    assumptions: list[PlanningAssumption] = Field(default_factory=list)
```

Add a serialized `estimated_cost` computed compatibility property to DayPlan and PlanMetrics that returns `known_estimated_cost`. Keep `DayPlan.walking_distance_meters`; set `walking_distance_estimated=true` on each affected PlanItem and add a candidate assumption so Validator can emit one warning per candidate rather than one per leg.

- [ ] **Step 4: Write failing unknown-cost tests**

Construct a candidate with one `PlanItem.estimated_cost=None` and a trip with a budget. Assert `validate_candidate` emits `budget_unverified` WARNING, not `budget_exceeded`, and returns `VALID_WITH_WARNINGS`. Construct a candidate whose known total exceeds the budget and assert `budget_exceeded` ERROR plus `INVALID`.

- [ ] **Step 5: Update Validator**

Accumulate only non-null costs. Add one `budget_unverified` warning when `trip.total_budget` is set and unknown-cost count is positive. Add one warning per unique planning assumption type, including `opening_hours_unverified`, `duration_unverified`, and `walking_distance_estimated`. Return `ValidationResult.from_violations(violations)`.

- [ ] **Step 6: Update existing fixtures and run regression**

Replace direct `ValidationResult(valid=True)` and `ValidationResult(valid=False)` construction in tests/code with `from_violations` or explicit status. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_domain_models.py tests/test_data_quality_validation.py -v
.\.venv\Scripts\python.exe -m pytest
```

Expected: all tests pass and serialized responses still contain `valid` and `estimated_cost` compatibility fields.

- [ ] **Step 7: Commit truthful validation models**

```powershell
git add src/travel_agent/domain/models.py src/travel_agent/planning/validator.py tests/test_domain_models.py tests/test_data_quality_validation.py
git commit -m "feat: distinguish valid plans from unverified data"
```

---

### Task 10: Two-phase route-aware Planner

**Files:**
- Create: `src/travel_agent/planning/drafts.py`
- Create: `tests/test_route_aware_planner.py`
- Modify: `src/travel_agent/planning/planner.py`
- Modify: `tests/test_workflow.py`

**Interfaces:**
- Consumes: `PlanningPOI`, `RouteQuery`, `RouteResult`, and `route_key`.
- Produces: `prepare_candidate_drafts(trip, pois, replan_round) -> list[CandidateDraft]`.
- Produces: `collect_route_queries(trip, drafts) -> list[RouteQuery]`.
- Produces: `materialize_candidates(trip, drafts, pois, routes) -> list[PlanCandidate]`.

- [ ] **Step 1: Write the failing draft and route-query tests**

```python
def test_drafts_use_haversine_only_for_ordering(hangzhou_trip, planning_pois):
    drafts = prepare_candidate_drafts(hangzhou_trip, planning_pois, replan_round=0)
    assert {draft.style for draft in drafts} == set(PlanStyle)
    assert all(len(draft.days) == hangzhou_trip.day_count for draft in drafts)


def test_route_queries_are_directional_and_deduplicated(hangzhou_trip, candidate_drafts):
    queries = collect_route_queries(hangzhou_trip, candidate_drafts)
    keys = [route_key(query) for query in queries]
    assert len(keys) == len(set(keys))
    assert all(query.mode is RouteMode.DRIVING for query in queries)
```

- [ ] **Step 2: Verify RED**

Run `\.venv\Scripts\python.exe -m pytest tests/test_route_aware_planner.py -v`.

Expected: import failure for `planning.drafts`.

- [ ] **Step 3: Implement immutable drafts**

Define `CandidateDraft(id, style, days)` and `DraftDay(date, poi_ids)` as frozen Pydantic models. Move selection, style limits, replan density reduction, round-robin day assignment, and Haversine nearest ordering out of final scheduling into `prepare_candidate_drafts`. Drafts contain POI IDs/order but no claimed travel duration or road distance; materialization builds one ID-to-`PlanningPOI` lookup from its `pois` argument.

`collect_route_queries` must add accommodation/arrival-anchor → first POI and each adjacent POI pair for every draft day, carry POI IDs when available, use strategy 32, and return first-seen unique directional keys.

- [ ] **Step 4: Write the failing materialization test**

```python
def test_materialization_uses_provider_route_values(hangzhou_trip, single_draft, planning_pois):
    query = collect_route_queries(hangzhou_trip, [single_draft])[0]
    routes = {route_key(query): RouteResult(
        distance_meters=4200,
        duration_minutes=18,
        provider="fixture",
        data_confidence=0.9,
        fetched_at=NOW,
    )}
    candidate = materialize_candidates(
        hangzhou_trip, [single_draft], planning_pois, routes
    )[0]
    first = candidate.days[0].items[0]
    assert first.distance_from_previous_meters == 4200
    assert first.travel_from_previous_minutes == 18
    assert first.walking_distance_estimated is True
```

- [ ] **Step 5: Implement route-aware scheduling**

`materialize_candidates` must look up every required segment and raise `MissingRouteResult(route_key)` instead of calling `estimate_route`. Compute derived walking as `min(round(distance_meters * 0.12), 2000)`, mark it estimated, use each date's effective opening window, preserve assumptions, add only known party costs, count unknown costs, and compute score with data confidence and warning risk. Remove route estimation calls from final schedule construction.

- [ ] **Step 6: Update old planner tests and run regression**

Keep `_poi_preference_score` behavior, must-visit priority, the three styles, and bounded density reduction. Adapt direct planner tests to build drafts plus Mock route results. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_route_aware_planner.py tests/test_workflow.py -v
.\.venv\Scripts\python.exe -m pytest
```

Expected: all tests pass; Graph integration still switches in Task 11.

- [ ] **Step 7: Commit the two-phase Planner**

```powershell
git add src/travel_agent/planning/drafts.py src/travel_agent/planning/planner.py tests/test_route_aware_planner.py tests/test_workflow.py
git commit -m "feat: plan with provider route results"
```

---

### Task 11: Async LangGraph Tool Use and trajectory

**Files:**
- Modify: `src/travel_agent/graph/state.py`
- Modify: `src/travel_agent/graph/workflow.py`
- Create: `tests/test_agent_trajectory.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_logging.py`

**Interfaces:**
- Consumes: `ToolGateway`, `POIDefaultPolicy`, Search Plan, drafts, routes, Planner, and Validator.
- Produces: `build_workflow(gateway: ToolGateway, defaults: POIDefaultPolicy)`.
- Produces: `async run_planning(workflow, request, thread_id=None) -> PlanningResponse`.
- Exposes: explicit Tool Use nodes and conditional Replan loop.

- [ ] **Step 1: Write a failing successful Agent trajectory test**

```python
@pytest.mark.asyncio
async def test_agent_trajectory_calls_tools_updates_state_and_selects(hangzhou_trip, caplog):
    gateway = gateway_with_recording_mock_providers()
    workflow = build_workflow(
        gateway,
        POIDefaultPolicy(UnknownFactPolicy.ASSUME_WITH_WARNING),
    )
    response = await run_planning(
        workflow,
        PlanningRequest(trip=hangzhou_trip, max_replan_rounds=2),
        thread_id="trajectory-success",
    )
    assert response.status == "completed"
    assert gateway.poi_calls
    assert gateway.route_calls
    events = event_names(caplog.records, thread_id="trajectory-success")
    assert_in_order(events, [
        "search_plan.created",
        "tool.started",
        "poi_context.loaded",
        "candidate_drafts.prepared",
        "routes.loaded",
        "candidate.generated",
        "candidate.validated",
        "routing.decision",
        "plan.selected",
    ])
```

- [ ] **Step 2: Run the trajectory test and verify RED**

Run `\.venv\Scripts\python.exe -m pytest tests/test_agent_trajectory.py::test_agent_trajectory_calls_tools_updates_state_and_selects -v`.

Expected: `build_workflow` does not accept injected dependencies and `run_planning` is not async.

- [ ] **Step 3: Expand minimal typed State**

Add these fields to `TravelState`:

```python
search_queries: list[POISearchQuery]
poi_facts: list[POIFacts]
planning_pois: list[PlanningPOI]
poi_resolution_issues: list[POIResolutionIssue]
candidate_drafts: list[CandidateDraft]
route_queries: list[RouteQuery]
route_results: dict[str, RouteResult]
tool_summaries: list[ToolExecutionSummary]
```

Keep existing trip, candidate, selection, iteration, status, message, and thread fields. Do not add Provider, Gateway, HTTP client, Key, raw payload, or exception fields.

- [ ] **Step 4: Replace the Graph with explicit Tool Use nodes**

Build these nodes and edges:

```text
START
→ build_search_plan
→ load_pois
→ prepare_candidate_drafts
→ load_routes
→ materialize_candidates
→ validate_candidates
→ select_best | replan | mark_infeasible
replan → prepare_candidate_drafts
```

`load_pois` and `load_routes` are `async def`. They call Gateway with `ToolCallContext(thread_id=state["thread_id"])`. If any required `ToolResult` is failed, raise `ToolUnavailableError.from_result(result, thread_id)` immediately; do not enter Replan. Merge POIs by ID in search-query priority order, cap at 12, and resolve facts through the injected policy.

- [ ] **Step 5: Make planning invocation async and dependency-injected**

```python
async def run_planning(
    workflow: CompiledStateGraph,
    request: PlanningRequest,
    thread_id: str | None = None,
) -> PlanningResponse:
    run_thread_id = thread_id or str(uuid4())
    result = await workflow.ainvoke(
        initial_state(request, run_thread_id),
        config={
            "configurable": {"thread_id": run_thread_id},
            "recursion_limit": 30,
        },
    )
    return response_from_state(result)
```

Raise the recursion limit only to accommodate the added explicit nodes; `max_replan_rounds` remains the business loop bound.

- [ ] **Step 6: Write the failing Replan trajectory test**

```python
@pytest.mark.asyncio
async def test_validation_feedback_drives_one_bounded_replan(low_budget_trip, caplog):
    workflow = mock_workflow()
    response = await run_planning(
        workflow,
        PlanningRequest(trip=low_budget_trip, max_replan_rounds=1),
        thread_id="trajectory-replan",
    )
    assert response.status == "infeasible"
    assert response.iterations == 1
    decisions = routing_decisions(caplog.records, "trajectory-replan")
    assert decisions == ["replan", "mark_infeasible"]
```

- [ ] **Step 7: Preserve bounded validation routing**

Select when at least one candidate has `VALID` or `VALID_WITH_WARNINGS`; sort fully valid ahead of warning-only candidates before score comparison. Replan only when no deliverable candidate exists, POIs exist, and `iterations < max_replan_rounds`. Mark infeasible after the budget is exhausted. Tool failures bypass this conditional edge by exception.

- [ ] **Step 8: Update existing sync tests and logging assertions**

Mark workflow tests async and await `run_planning`. Replace imports of the old global `workflow` with a fixture-created Mock workflow. Update expected node names and retain `thread_id` assertions for every Agent event.

- [ ] **Step 9: Run Graph and trajectory tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_trajectory.py tests/test_workflow.py tests/test_logging.py -v
.\.venv\Scripts\python.exe -m pytest
```

Expected: successful and infeasible trajectories pass; all ordinary tests remain offline.

- [ ] **Step 10: Commit async Agent Tool Use**

```powershell
git add src/travel_agent/graph src/travel_agent/planning tests/test_agent_trajectory.py tests/test_workflow.py tests/test_logging.py
git commit -m "feat: expose async tool use in agent graph"
```

---

### Task 12: FastAPI runtime lifecycle and HTTP 503 semantics

**Files:**
- Create: `src/travel_agent/runtime.py`
- Create: `src/travel_agent/api/dependencies.py`
- Create: `src/travel_agent/api/errors.py`
- Modify: `src/travel_agent/app.py:12-27`
- Modify: `src/travel_agent/api/routes.py:20-22`
- Modify: `tests/test_api.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: `Settings`, Providers, ToolGateway, RetryPolicy, cache, workflow, and `run_planning`.
- Produces: `PlanningRuntime.create(settings)`, `PlanningRuntime.close()`, and `PlanningRuntime.plan(request, thread_id)`.
- Produces: async FastAPI planning endpoint and 503 exception handler.

- [ ] **Step 1: Write failing runtime-mode tests**

```python
@pytest.mark.asyncio
async def test_mock_runtime_contains_only_mock_providers():
    runtime = await PlanningRuntime.create(Settings.from_env({}))
    try:
        assert isinstance(runtime.poi_provider, MockPOIProvider)
        assert isinstance(runtime.route_provider, MockRouteProvider)
        assert not hasattr(runtime, "fallback_provider")
    finally:
        await runtime.close()


def test_amap_app_creation_without_key_fails(monkeypatch):
    monkeypatch.setenv("TRAVEL_PROVIDER", "amap")
    monkeypatch.delenv("AMAP_API_KEY", raising=False)
    with pytest.raises(ValueError, match="AMAP_API_KEY"):
        create_app()
```

- [ ] **Step 2: Run tests and verify RED**

Run `\.venv\Scripts\python.exe -m pytest tests/test_api.py -k "runtime or amap_app" -v`.

Expected: `PlanningRuntime` is missing and `create_app` does not validate Provider settings.

- [ ] **Step 3: Implement runtime assembly**

```python
@dataclass(slots=True)
class PlanningRuntime:
    settings: Settings
    poi_provider: POIProvider
    route_provider: RouteProvider
    gateway: ToolGateway
    workflow: CompiledStateGraph
    client: httpx.AsyncClient | None

    @classmethod
    async def create(cls, settings: Settings) -> "PlanningRuntime":
        if settings.provider is ProviderMode.MOCK:
            poi_provider = MockPOIProvider()
            route_provider = MockRouteProvider()
            client = None
        else:
            client = httpx.AsyncClient(base_url="https://restapi.amap.com")
            amap_client = AMapClient(client, settings.amap_api_key, settings.tool_timeout_seconds)
            poi_provider = AMapPOIProvider(amap_client)
            route_provider = AMapRouteProvider(amap_client)
        gateway = build_gateway(settings, poi_provider, route_provider)
        defaults = POIDefaultPolicy(settings.unknown_fact_policy)
        return cls(
            settings, poi_provider, route_provider, gateway,
            build_workflow(gateway, defaults), client,
        )
```

`close` closes the client only when present. `plan` creates the run ID before calling `run_planning`, so the same ID appears in errors and logs.

- [ ] **Step 4: Use FastAPI lifespan and dependency lookup**

`create_app(settings: Settings | None = None, runtime_factory=PlanningRuntime.create)` must resolve settings synchronously, create runtime in an `asynccontextmanager` lifespan, store it at `app.state.planning_runtime`, and close it in `finally`. `get_runtime(request: Request)` returns that exact object. Change `create_plan` to `async def`, obtain runtime via `Depends`, and await `runtime.plan`.

- [ ] **Step 5: Write the failing 503-vs-infeasible API tests**

```python
def test_tool_failure_returns_503(tool_failure_client):
    response = tool_failure_client.post("/api/v1/plans", json=VALID_REQUEST)
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "tool_unavailable",
        "provider": "amap",
        "operation": "search_pois",
        "category": "timeout",
        "retryable": True,
        "thread_id": "api-tool-failure",
        "message": "地图服务暂时不可用，请稍后重试",
    }


def test_business_infeasible_still_returns_200(client, low_budget_payload):
    response = client.post("/api/v1/plans", json=low_budget_payload)
    assert response.status_code == 200
    assert response.json()["status"] == "infeasible"
```

- [ ] **Step 6: Implement the safe exception handler**

Register an exception handler for `ToolUnavailableError` that returns `UTF8JSONResponse(status_code=503, content={"detail": error.safe_detail()})`. `safe_detail` exposes only the exact fields in the test. Keep stack traces in server ERROR logs, not the response.

- [ ] **Step 7: Run API and full regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api.py -v
.\.venv\Scripts\python.exe -m pytest
```

Expected: health, successful plan, UTF-8 response, infeasible business result, startup validation, and 503 semantics all pass.

- [ ] **Step 8: Commit runtime integration**

```powershell
git add src/travel_agent/runtime.py src/travel_agent/api src/travel_agent/app.py tests/conftest.py tests/test_api.py
git commit -m "feat: add provider runtime and safe tool errors"
```

---

### Task 13: Security, logging, and no-fallback proof

**Files:**
- Modify: `tests/test_logging.py`
- Modify: `tests/test_tool_gateway.py`
- Modify: `tests/test_amap_provider.py`
- Modify: `tests/test_agent_trajectory.py`
- Modify: `src/travel_agent/tools/gateway.py`
- Modify: `src/travel_agent/tools/providers/amap.py`

**Interfaces:**
- Verifies: Agent Tool Use is observable without exposing credentials or raw supplier data.
- Verifies: AMap mode cannot invoke Mock under any failure.

- [ ] **Step 1: Write the failing secret-redaction test**

```python
@pytest.mark.asyncio
async def test_secret_never_appears_in_logs_or_safe_error(caplog):
    secret = "amap-super-secret-test-key"
    provider = amap_provider_returning("server_busy.json", api_key=secret)
    result = (await gateway_for(provider).search_pois([QUERY], CONTEXT))[0]
    combined_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert secret not in combined_logs
    assert secret not in result.model_dump_json()
    assert "restapi.amap.com" not in result.error.safe_message
```

- [ ] **Step 2: Write the failing no-fallback test**

```python
@pytest.mark.asyncio
async def test_amap_failure_does_not_call_mock():
    amap = AlwaysTimeoutPOIProvider()
    mock = ExplodingMockProvider()
    runtime = runtime_for_test(selected_poi_provider=amap, unselected_provider=mock)
    with pytest.raises(ToolUnavailableError):
        await runtime.plan(REQUEST, thread_id="no-fallback")
    assert amap.calls == 3
    assert mock.calls == 0
```

The test helper may retain the unselected Provider solely as a spy; production `PlanningRuntime` must not store it.

- [ ] **Step 3: Add retry and cache log assertions**

Assert one timeout followed by success emits this ordered tool subsequence with the same thread ID:

```text
tool.started attempt=1
tool.retry_scheduled next_attempt=2
tool.completed attempt_count=2 cache_hit=false
```

Call the same query again and assert `tool.cache_hit cache_hit=true` without a second Provider call.

- [ ] **Step 4: Fix any redaction or event-order failures minimally**

Use a logging helper that accepts only safe scalar fields. Do not log parameter dictionaries. Ensure `ToolProviderError.__str__` returns `safe_message`, `AMapClient` never embeds URL/params/body in safe errors, and Gateway logs retry callbacks before sleeping.

- [ ] **Step 5: Run security and trajectory tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_logging.py tests/test_tool_gateway.py tests/test_amap_provider.py tests/test_agent_trajectory.py -v
```

Expected: all tests pass; the test Key is absent from captured output.

- [ ] **Step 6: Commit observability proof**

```powershell
git add src/travel_agent/tools tests/test_logging.py tests/test_tool_gateway.py tests/test_amap_provider.py tests/test_agent_trajectory.py
git commit -m "test: prove safe observable agent tool use"
```

---

### Task 14: v0.2 learning documentation, examples, and final verification

**Files:**
- Create: `docs/v0.2/README.md`
- Create: `docs/v0.2/01-architecture.md`
- Create: `docs/v0.2/02-provider-contracts.md`
- Create: `docs/v0.2/03-tool-gateway-reliability.md`
- Create: `docs/v0.2/04-async-langgraph-flow.md`
- Create: `docs/v0.2/05-running-and-testing.md`
- Create: `docs/v0.2/06-learning-guide.md`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `src/travel_agent/__init__.py`
- Create: `tests/test_amap_live_smoke.py`

**Interfaces:**
- Documents: actual v0.2 code and Agent trajectory, not aspirational future features.
- Provides: Mock startup, AMap configuration, log interpretation, offline tests, and optional live smoke instructions.

- [ ] **Step 1: Update version and environment example**

Set `__version__ = "0.2.0"`. Ensure `.env.example` contains the exact variables from **Global Constraints**, keeps `AMAP_API_KEY=` empty, and defaults to `TRAVEL_PROVIDER=mock`.

- [ ] **Step 2: Write the v0.2 documentation set**

Use these required contents:

```text
README.md
- implemented/not implemented boundary
- recommended reading order
- Agent-first learning objectives

01-architecture.md
- Search Intent → Tool Use → State → Validate → Replan diagram
- module responsibilities and dependency direction

02-provider-contracts.md
- Protocol vs Provider vs Gateway
- Mock/AMap strict isolation
- POIFacts, RouteResult, provenance

03-tool-gateway-reliability.md
- cache, semaphore, retry/error decision table
- why tool failure is 503, not infeasible

04-async-langgraph-flow.md
- every State field, node, edge, loop bound
- successful and Replan trajectories from real log event names

05-running-and-testing.md
- Mock PowerShell commands
- AMap environment variables without a real Key
- offline contract tests and optional smoke command

06-learning-guide.md
- interview explanation
- Tool Use, State, Loop, context, trajectory-test exercises
- explicit next Agent capability, not generic backend expansion
```

- [ ] **Step 3: Update the root README**

Change current progress to v0.2, link `docs/v0.2/README.md`, show both startup modes, explain that AMap mode never falls back, and lead with Agent Tool Use rather than HTTP integration. Keep the v0.1 link as historical documentation.

- [ ] **Step 4: Add an optional live smoke test only if its guard is exact**

```python
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_AMAP_LIVE") != "1" or not os.getenv("AMAP_API_KEY"),
    reason="set RUN_AMAP_LIVE=1 and AMAP_API_KEY to run live AMap smoke tests",
)
```

The smoke test performs one Hangzhou POI query and one route query, asserts only non-empty schema plus positive distance/duration, and never prints the Key. Ordinary `pytest` must report it as skipped without network.

- [ ] **Step 5: Run fresh final verification**

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=travel_agent --cov-report=term-missing
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Expected:

```text
all non-live tests passed
optional live smoke test skipped
TOTAL branch coverage >= 90%
compileall exit code 0
No broken requirements found
git diff --check exit code 0
```

- [ ] **Step 6: Perform a manual Mock Agent trajectory check**

```powershell
$env:TRAVEL_PROVIDER = "mock"
$env:APP_LOG_LEVEL = "INFO"
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

In another PowerShell:

```powershell
.\scripts\invoke-hangzhou-example.ps1
```

Verify the Uvicorn terminal contains, in order, search-plan creation, POI Tool Use, route Tool Use, candidate validation, conditional routing, and final selection. Verify the response contains Provider-derived route metrics and any explicit data assumptions.

- [ ] **Step 7: Commit documentation and release metadata**

```powershell
git add README.md .env.example src/travel_agent/__init__.py docs/v0.2 tests/test_amap_live_smoke.py
git commit -m "docs: publish v0.2 tool use learning guide"
```

---

## Final Agent-Capability Review

Before declaring v0.2 complete, demonstrate these six claims with a test name or log excerpt:

1. **State:** standardized POI and route results are written into typed Graph State; raw Provider payloads are absent.
2. **Tool Use:** POI and route calls occur through explicit Graph nodes and emit correlated tool events.
3. **Loop:** validation feedback selects `select_best`, `replan`, or `mark_infeasible` through conditional edges with a hard iteration bound.
4. **Failure semantics:** Provider retry exhaustion produces HTTP 503 and never invokes Mock or returns business `infeasible`.
5. **Context:** `thread_id` connects API run, Gateway events, Graph State, Checkpoint, and planning logs without storing secrets.
6. **Evaluation:** trajectory tests assert intermediate behavior, while coverage and offline contract tests protect deterministic logic.

If any claim can only be described but not shown in evidence, v0.2 is not complete.
