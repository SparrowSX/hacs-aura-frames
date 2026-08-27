"""Tests for the Aura Frames API client."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.aura_frames.api import AuraClient
from custom_components.aura_frames.const import API_BASE_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import FRAME, FRAME_ID

# What a sparse single-frame response looks like: everything the entities
# read, but no schedule. This is the shape the frame-list fallback exists for.
SPARSE_FRAME = {"id": FRAME_ID, "num_assets": 7, "wifi_network": "WLAN"}


@pytest.fixture(autouse=True)
def mock_response_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the Home Assistant response mock a content_length.

    _request reads it to decide whether a body is worth parsing. Real aiohttp
    responses have it; AiohttpClientMockResponse does not, so fill it in from
    the body the mock was configured with.
    """
    monkeypatch.setattr(
        AiohttpClientMockResponse,
        "content_length",
        property(lambda self: len(self.response)),
        raising=False,
    )


def build_client(hass: HomeAssistant) -> AuraClient:
    """Return an authenticated client bound to the test session."""
    return AuraClient(
        async_get_clientsession(hass),
        "0BE1A0DB-0000-0000-0000-000000000000",
        user_id="1",
        auth_token="token",
    )


async def test_get_frame_merges_the_list_when_the_schedule_is_missing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The fallback fills in schedule fields the single frame omits."""
    aioclient_mock.get(
        f"{API_BASE_URL}/frames/{FRAME_ID}.json", json={"frame": SPARSE_FRAME}
    )
    aioclient_mock.get(f"{API_BASE_URL}/frames.json", json={"frames": [FRAME]})

    frame = await build_client(hass).get_frame(FRAME_ID)

    assert len(aioclient_mock.mock_calls) == 2
    assert frame["scheduled_display_on_at"] == "1969-12-31T07:30:00.000Z"
    assert frame["num_assets"] == 7


async def test_get_frame_skips_the_list_when_the_schedule_is_not_needed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """ensure_schedule=False costs one request instead of two."""
    aioclient_mock.get(
        f"{API_BASE_URL}/frames/{FRAME_ID}.json", json={"frame": SPARSE_FRAME}
    )
    aioclient_mock.get(f"{API_BASE_URL}/frames.json", json={"frames": [FRAME]})

    frame = await build_client(hass).get_frame(FRAME_ID, ensure_schedule=False)

    assert len(aioclient_mock.mock_calls) == 1
    assert frame["num_assets"] == 7
    assert "scheduled_display_on_at" not in frame


async def test_get_frame_makes_one_request_when_the_schedule_is_present(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A complete single-frame response never triggers the fallback."""
    aioclient_mock.get(
        f"{API_BASE_URL}/frames/{FRAME_ID}.json", json={"frame": FRAME}
    )

    frame = await build_client(hass).get_frame(FRAME_ID)

    assert len(aioclient_mock.mock_calls) == 1
    assert frame["scheduled_display_on_at"] == "1969-12-31T07:30:00.000Z"
