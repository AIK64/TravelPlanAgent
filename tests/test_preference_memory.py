from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from travel_agent.domain.models import (
    Coordinate,
    LocationAnchor,
    Pace,
    TransportAnchor,
    TripSpec,
)
from travel_agent.identity.models import Principal
from travel_agent.memory.context import PreferenceContextComposer
from travel_agent.memory.errors import MemoryConflictError, MemoryNotFoundError
from travel_agent.memory.models import (
    AgentRole,
    ConfirmationStatus,
    MemoryCategory,
    MemoryProposalRequest,
    MemorySource,
    PersonalizationUpdateRequest,
    PreferenceCreateRequest,
    PreferenceScope,
    PreferenceUpdateRequest,
)
from travel_agent.memory.repository import (
    InMemoryPreferenceRepository,
    SQLitePreferenceRepository,
)
from travel_agent.memory.service import PreferenceMemoryService
from travel_agent.memory.errors import MemoryPolicyError
from travel_agent.memory.policy import (
    normalize_preference_value,
    preference_content_hash,
    validate_source_for_direct_write,
)
from travel_agent.requirements.models import RequirementDraft


def principal(user_id: str = "user-a", tenant_id: str = "tenant-a") -> Principal:
    return Principal(
        tenant_id=tenant_id,
        user_id=user_id,
        scopes=frozenset({"preferences:read", "preferences:write"}),
    )


def trip() -> TripSpec:
    return TripSpec(
        destination="杭州",
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
        travelers=2,
        arrival=TransportAnchor(
            name="杭州东站",
            at=datetime(2026, 10, 2, 10, tzinfo=timezone.utc),
            coordinate=Coordinate(longitude=120.21, latitude=30.29),
        ),
        departure=TransportAnchor(
            name="杭州东站",
            at=datetime(2026, 10, 4, 10, tzinfo=timezone.utc),
            coordinate=Coordinate(longitude=120.21, latitude=30.29),
        ),
        accommodation=LocationAnchor(
            name="西湖",
            coordinate=Coordinate(longitude=120.15, latitude=30.25),
        ),
    )


@pytest.mark.asyncio
async def test_explicit_memory_is_deduplicated_and_isolated() -> None:
    repository = InMemoryPreferenceRepository()
    service = PreferenceMemoryService(repository)
    request = PreferenceCreateRequest(
        category=MemoryCategory.PACE,
        value=" relaxed ",
    )

    first = await service.create_explicit(principal(), request)
    repeated = await service.create_explicit(principal(), request)

    assert first.memory_id == repeated.memory_id
    assert first.value == "relaxed"
    assert len((await service.list(principal())).items) == 1
    assert (await service.list(principal("user-b"))).items == ()
    with pytest.raises(MemoryNotFoundError):
        await service.get(principal("user-b"), first.memory_id)


@pytest.mark.asyncio
async def test_proposal_requires_confirmation_and_is_idempotent() -> None:
    service = PreferenceMemoryService(InMemoryPreferenceRepository())
    proposal = await service.propose(
        principal(),
        MemoryProposalRequest(
            category=MemoryCategory.PREFERRED_CATEGORIES,
            value=["自然", "自然", "美食"],
            source=MemorySource.MODEL_INFERENCE,
            confidence=0.7,
            reason="用户连续选择自然景点",
        ),
    )
    assert (await service.list(principal())).items == ()

    request_id = uuid4()
    memory = await service.confirm_proposal(
        principal(), proposal.proposal_id, request_id=request_id
    )
    replay = await service.confirm_proposal(
        principal(), proposal.proposal_id, request_id=request_id
    )

    assert memory.memory_id == replay.memory_id
    assert memory.confirmation_status is ConfirmationStatus.CONFIRMED
    assert memory.source is MemorySource.EXPLICIT_USER
    assert memory.value == ["自然", "美食"]


@pytest.mark.asyncio
async def test_revoke_delete_and_personalization() -> None:
    service = PreferenceMemoryService(InMemoryPreferenceRepository())
    memory = await service.create_explicit(
        principal(),
        PreferenceCreateRequest(
            category=MemoryCategory.WALKING_TOLERANCE,
            value=6_000,
        ),
    )
    revoked = await service.revoke(
        principal(), memory.memory_id, expected_revision=memory.revision
    )
    assert revoked.revoked_at is not None
    assert (await service.list(principal())).items == ()
    assert len((await service.list(principal(), include_inactive=True)).items) == 1

    settings = await service.update_personalization(
        principal(),
        PersonalizationUpdateRequest(enabled=False, expected_revision=1),
    )
    assert not settings.enabled
    with pytest.raises(MemoryConflictError):
        await service.update_personalization(
            principal(),
            PersonalizationUpdateRequest(enabled=True, expected_revision=1),
        )

    await service.delete(principal(), memory.memory_id)
    assert (await service.list(principal(), include_inactive=True)).items == ()


@pytest.mark.asyncio
async def test_update_uses_optimistic_revision() -> None:
    service = PreferenceMemoryService(InMemoryPreferenceRepository())
    memory = await service.create_explicit(
        principal(),
        PreferenceCreateRequest(category=MemoryCategory.PACE, value="relaxed"),
    )
    updated = await service.update(
        principal(),
        memory.memory_id,
        PreferenceUpdateRequest(value="balanced", expected_revision=1),
    )
    assert updated.revision == 2
    assert updated.value == "balanced"
    with pytest.raises(MemoryConflictError):
        await service.update(
            principal(),
            memory.memory_id,
            PreferenceUpdateRequest(value="intensive", expected_revision=1),
        )


@pytest.mark.asyncio
async def test_context_is_bounded_role_specific_and_current_request_wins() -> None:
    service = PreferenceMemoryService(
        InMemoryPreferenceRepository(),
        context_max_tokens=200,
        context_max_characters=800,
    )
    pace = await service.create_explicit(
        principal(),
        PreferenceCreateRequest(category=MemoryCategory.PACE, value="relaxed"),
    )
    await service.create_explicit(
        principal(),
        PreferenceCreateRequest(
            category=MemoryCategory.PREFERRED_CATEGORIES,
            value=["自然", "美食"],
        ),
    )
    await service.create_explicit(
        principal(),
        PreferenceCreateRequest(
            category=MemoryCategory.FOOD_PREFERENCES,
            value=["川菜"],
        ),
    )
    explicit_draft = RequirementDraft(pace=Pace.INTENSIVE)
    context = await service.context_for_trip(
        principal(),
        trip=trip(),
        draft=explicit_draft,
        agent_role=AgentRole.PLANNER,
    )

    assert pace.memory_id in context.manifest.overridden_memory_ids
    assert MemoryCategory.PACE not in context.manifest.selected_categories
    assert context.manifest.estimated_tokens <= context.manifest.max_tokens
    personalized = service.apply_to_trip(
        trip(), draft=explicit_draft, context=context
    )
    assert personalized.pace is Pace.BALANCED
    assert personalized.interests == ["自然", "美食"]

    replanner = await service.context_for_trip(
        principal(),
        trip=trip(),
        draft=None,
        agent_role=AgentRole.REPLANNER,
    )
    assert MemoryCategory.FOOD_PREFERENCES not in replanner.manifest.selected_categories


@pytest.mark.asyncio
async def test_expired_and_destination_scoped_memory_is_excluded() -> None:
    repository = InMemoryPreferenceRepository()
    service = PreferenceMemoryService(repository)
    now = datetime.now(timezone.utc)
    await service.create_explicit(
        principal(),
        PreferenceCreateRequest(
            category=MemoryCategory.PACE,
            value="relaxed",
            scope=PreferenceScope.DESTINATION,
            scope_key="成都",
        ),
    )
    await service.create_explicit(
        principal(),
        PreferenceCreateRequest(
            category=MemoryCategory.BUDGET_STYLE,
            value="economy",
            expires_at=now + timedelta(seconds=1),
        ),
    )
    items = await repository.list_memories("tenant-a", "user-a")
    context = PreferenceContextComposer().compose(
        items,
        trip=trip(),
        draft=None,
        agent_role=AgentRole.PLANNER,
        now=now + timedelta(seconds=2),
    )
    assert context.summaries == ()
    assert context.manifest.exclusion_reasons["scope_mismatch"] == 1
    assert context.manifest.exclusion_reasons["inactive"] == 1


@pytest.mark.asyncio
async def test_sqlite_repository_persists_and_enforces_owner(tmp_path) -> None:
    path = tmp_path / "memory.sqlite3"
    first = SQLitePreferenceRepository(str(path))
    service = PreferenceMemoryService(first)
    memory = await service.create_explicit(
        principal(),
        PreferenceCreateRequest(category=MemoryCategory.PACE, value="relaxed"),
    )
    await first.close()

    second = SQLitePreferenceRepository(str(path))
    restored = await second.get_memory("tenant-a", "user-a", memory.memory_id)
    assert restored.value == "relaxed"
    with pytest.raises(MemoryNotFoundError):
        await second.get_memory("tenant-a", "user-b", memory.memory_id)
    await second.close()
@pytest.mark.asyncio
async def test_sensitive_payment_data_is_rejected():
    service = PreferenceMemoryService(InMemoryPreferenceRepository())
    with pytest.raises(MemoryPolicyError) as captured:
        await service.create_explicit(
            Principal(tenant_id="tenant-a", user_id="user-a"),
            PreferenceCreateRequest(
                category=MemoryCategory.FOOD_PREFERENCES,
                value=["信用卡 6222020202020202"],
            ),
        )
    assert captured.value.code == "sensitive_memory_rejected"


@pytest.mark.asyncio
async def test_sqlite_preference_repository_full_contract(tmp_path) -> None:
    repository = SQLitePreferenceRepository(str(tmp_path / "contract.sqlite3"))
    service = PreferenceMemoryService(repository)
    try:
        first = await service.create_explicit(
            principal(),
            PreferenceCreateRequest(category=MemoryCategory.PACE, value="relaxed"),
        )
        second = await service.create_explicit(
            principal(),
            PreferenceCreateRequest(
                category=MemoryCategory.PREFERRED_CATEGORIES,
                value=["自然"],
            ),
        )
        assert len(await repository.list_memories("tenant-a", "user-a")) == 2
        assert (
            await repository.find_content_hash(
                "tenant-a", "user-a", first.content_hash
            )
        ).memory_id == first.memory_id

        with pytest.raises(MemoryConflictError):
            await repository.create_memory(first)
        with pytest.raises(MemoryNotFoundError):
            await repository.save_memory(
                first.model_copy(update={"memory_id": "missing", "revision": 2}),
                expected_revision=1,
            )
        with pytest.raises(MemoryConflictError):
            await repository.save_memory(
                first.model_copy(update={"revision": 2}), expected_revision=99
            )

        proposal = await service.propose(
            principal(),
            MemoryProposalRequest(
                category=MemoryCategory.FOOD_PREFERENCES,
                value=["本帮菜"],
                reason="explicit contract case",
            ),
        )
        assert (
            await repository.get_proposal(
                "tenant-a", "user-a", proposal.proposal_id
            )
        ).proposal_id == proposal.proposal_id
        await service.reject_proposal(
            principal(), proposal.proposal_id, request_id=uuid4()
        )
        with pytest.raises(MemoryNotFoundError):
            await repository.get_proposal("tenant-a", "user-a", "missing")
        with pytest.raises(MemoryNotFoundError):
            await repository.save_proposal(
                proposal.model_copy(update={"proposal_id": "missing"})
            )

        settings = await repository.get_personalization("tenant-a", "user-a")
        await repository.save_personalization(
            settings.model_copy(update={"enabled": False, "revision": 2}),
            expected_revision=1,
        )
        with pytest.raises(MemoryConflictError):
            await repository.save_personalization(
                settings.model_copy(update={"revision": 3}), expected_revision=1
            )

        await repository.delete_memory("tenant-a", "user-a", first.memory_id)
        with pytest.raises(MemoryNotFoundError):
            await repository.delete_memory("tenant-a", "user-a", "missing")
        assert await repository.clear_memories("tenant-a", "user-a") == 1
        assert second.memory_id not in {
            item.memory_id
            for item in await repository.list_memories("tenant-a", "user-a")
        }
    finally:
        await repository.close()


@pytest.mark.parametrize(
    ("category", "value"),
    [
        (MemoryCategory.PREFERRED_CATEGORIES, [1]),
        (MemoryCategory.PREFERRED_CATEGORIES, [" "]),
        (MemoryCategory.PACE, "fast"),
        (MemoryCategory.WALKING_TOLERANCE, True),
        (MemoryCategory.WALKING_TOLERANCE, 50_001),
        (MemoryCategory.BUDGET_STYLE, "luxury"),
        (MemoryCategory.SCHEDULE_PREFERENCES, "early"),
        (MemoryCategory.SCHEDULE_PREFERENCES, {"unsupported": True}),
    ],
)
def test_memory_policy_rejects_invalid_values(category, value) -> None:
    with pytest.raises(MemoryPolicyError):
        normalize_preference_value(category, value)


def test_memory_policy_normalizes_hashes_and_write_sources() -> None:
    assert normalize_preference_value(
        MemoryCategory.PREFERRED_CATEGORIES, [" 自然 ", "自然", "美食"]
    ) == ["自然", "美食"]
    assert normalize_preference_value(MemoryCategory.PACE, " RELAXED ") == "relaxed"
    assert normalize_preference_value(
        MemoryCategory.WALKING_TOLERANCE, 5000.9
    ) == 5000
    assert normalize_preference_value(
        MemoryCategory.BUDGET_STYLE, " Comfort "
    ) == "comfort"
    schedule = {"earliest_start": "09:00", "avoid_early_start": True}
    assert normalize_preference_value(
        MemoryCategory.SCHEDULE_PREFERENCES, schedule
    ) == schedule

    validate_source_for_direct_write(MemorySource.EXPLICIT_USER)
    validate_source_for_direct_write(MemorySource.IMPORT)
    with pytest.raises(MemoryPolicyError) as rejected_source:
        validate_source_for_direct_write(MemorySource.MODEL_INFERENCE)
    assert rejected_source.value.code == "confirmation_required"

    first = preference_content_hash(
        category=MemoryCategory.PACE,
        value="relaxed",
        scope=PreferenceScope.GLOBAL,
        scope_key=None,
    )
    second = preference_content_hash(
        category=MemoryCategory.PACE,
        value="relaxed",
        scope=PreferenceScope.GLOBAL,
        scope_key=None,
    )
    assert first == second
    assert len(first) == 64
