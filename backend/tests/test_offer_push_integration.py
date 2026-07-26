"""Integration tests: offer creation triggers push notification dispatch.

These tests verify that create_provider_offers calls
dispatch_offer_push.apply_async() with the correct arguments.
The actual Celery task is not executed — we spy on apply_async.

We patch at ``app.tasks.worker.dispatch_offer_push`` because
``offer_service.py`` imports it locally via
``from app.tasks.worker import dispatch_offer_push`` inside
``create_provider_offers``.

We also patch ``matching.nearest_mechanics`` / ``nearest_garages``
to avoid the PostgreSQL-specific ``&&`` operator used in the fallback
matching path (not available on SQLite).
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_token import DeviceToken
from app.models.garage import Garage
from app.models.mechanic import Mechanic
from app.models.provider_offer import ProviderOffer
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _make_ranked_mechanic(m: Mechanic) -> dict:
    """Build a RankedMechanic-like dict from a Mechanic fixture."""
    from app.services.matching import RankedMechanic
    from app.services.matching import haversine_m, _score

    dist = haversine_m(28.6139, 77.2090, float(m.lat), float(m.lon))
    return RankedMechanic(
        id=m.id,
        full_name=m.full_name,
        lat=float(m.lat),
        lon=float(m.lon),
        rating=float(m.rating or 0),
        distance_m=dist,
        score=_score(dist, float(m.rating or 0), 0.5),
        expertise=list(m.expertise or []),
    )


@pytest_asyncio.fixture
async def job_with_tag(
    db_session: AsyncSession,
    test_user: "User",
) -> "Job":
    """Create a Job with a valid issue_tag (NOT NULL in DB)."""
    from app.models.job import Job

    job = Job(
        id=uuid.uuid4(),
        user_id=test_user.id,
        issue_tag="flat_tire",
        description="Test job for push integration",
        request_type="auto",
        status="searching",
        customer_lat=28.6139,
        customer_lon=77.2090,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


@patch("app.services.offer_service.matching.nearest_garages")
@patch("app.services.offer_service.matching.nearest_mechanics")
@patch("app.tasks.worker.dispatch_offer_push")
async def test_offer_creation_triggers_push(
    mock_dispatch: MagicMock,
    mock_nearest_mechs: AsyncMock,
    mock_nearest_gars: AsyncMock,
    db_session: AsyncSession,
    job_with_tag: "Job",
    test_mechanic: Mechanic,
):
    """OPI1: Creating offers calls dispatch_offer_push.apply_async.

    When offers are created for a job, the Celery task should be
    dispatched with the job_id and offer count.
    """
    from app.services.offer_service import create_provider_offers

    # Mock matching to return the test mechanic
    mock_nearest_mechs.return_value = [_make_ranked_mechanic(test_mechanic)]
    mock_nearest_gars.return_value = []

    count = await create_provider_offers(db_session, job_with_tag)
    assert count > 0, "Expected at least one offer to be created"

    mock_dispatch.apply_async.assert_called_once()
    _pos, call_kwargs = mock_dispatch.apply_async.call_args
    assert call_kwargs["args"][0] == str(job_with_tag.id)  # job_id
    assert call_kwargs["args"][1] == count  # expected_count


@patch("app.services.offer_service.matching.nearest_garages")
@patch("app.services.offer_service.matching.nearest_mechanics")
@patch("app.tasks.worker.dispatch_offer_push")
async def test_no_offers_no_push(
    mock_dispatch: MagicMock,
    mock_nearest_mechs: AsyncMock,
    mock_nearest_gars: AsyncMock,
    db_session: AsyncSession,
    job_with_tag: "Job",
):
    """OPI2: No offers created → no push dispatch.

    When the matching functions return empty lists, the task should not fire.
    """
    from app.services.offer_service import create_provider_offers

    # Mock matching to return no providers
    mock_nearest_mechs.return_value = []
    mock_nearest_gars.return_value = []

    count = await create_provider_offers(db_session, job_with_tag)
    assert count == 0

    mock_dispatch.apply_async.assert_not_called()


@patch("app.services.offer_service.matching.nearest_garages")
@patch("app.services.offer_service.matching.nearest_mechanics")
@patch("app.tasks.worker.dispatch_offer_push")
async def test_job_already_assigned_no_push(
    mock_dispatch: MagicMock,
    mock_nearest_mechs: AsyncMock,
    mock_nearest_gars: AsyncMock,
    db_session: AsyncSession,
    job_with_tag: "Job",
):
    """OPI3: Job already assigned/cancelled → no push.

    When the job is not in 'searching' state, no offers are created
    and no push is dispatched.
    """
    from app.services.offer_service import create_provider_offers

    job_with_tag.status = "assigned"
    db_session.add(job_with_tag)
    await db_session.flush()

    count = await create_provider_offers(db_session, job_with_tag)
    assert count == 0

    mock_dispatch.apply_async.assert_not_called()
