"""Tests for the Aura Frames API client."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.aura_frames.api import AuraAuthError, AuraClient
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


def build_client(hass: HomeAssistant, **kwargs) -> AuraClient:
    """Return a client that already holds a session, bound to the test session."""
    return AuraClient(
        async_get_clientsession(hass),
        "0BE1A0DB-0000-0000-0000-000000000000",
        user_id="1",
        auth_token="token",
        **kwargs,
    )


def mock_frame_responses(
    aioclient_mock: AiohttpClientMocker, *statuses: int
) -> list[int]:
    """Answer the single-frame endpoint with ``statuses``, in order.

    Returns the list the calls are recorded in, so a test can count them.
    """
    calls: list[int] = []

    async def respond(method, url, data):
        status = statuses[min(len(calls), len(statuses) - 1)]
        calls.append(status)
        return AiohttpClientMockResponse(
            method, url, status=status, json={"frame": FRAME}
        )

    aioclient_mock.get(
        f"{API_BASE_URL}/frames/{FRAME_ID}.json", side_effect=respond
    )
    return calls


def mock_login(aioclient_mock: AiohttpClientMocker) -> None:
    """Answer the login endpoint with a fresh session."""
    aioclient_mock.post(
        f"{API_BASE_URL}/login.json",
        json={"result": {"current_user": {"id": "1", "auth_token": "neu"}}},
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


async def test_a_rejected_session_is_renewed_once_and_the_request_repeated(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A stale session costs one login, not a failed poll.

    Sessions are what the frame reacts to: it drops its own and shows its
    pairing code when the account's sessions churn. So one is opened only
    when the API turns the stored one down, and the caller never notices.
    """
    calls = mock_frame_responses(aioclient_mock, 401, 200)
    mock_login(aioclient_mock)
    stored: list[dict] = []

    client = build_client(
        hass,
        email="someone@example.com",
        password="hunter2",
        on_credentials_refreshed=stored.append,
    )
    frame = await client.get_frame(FRAME_ID)

    assert frame["num_assets"] == 7
    assert calls == [401, 200]
    assert client.auth_token == "neu"
    # Reported so the config entry survives a restart without logging in.
    assert stored == [{"user_id": "1", "auth_token": "neu"}]


async def test_a_session_rejected_twice_is_an_auth_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Renewing once is a repair; renewing in a loop would be the bug."""
    calls = mock_frame_responses(aioclient_mock, 401)
    mock_login(aioclient_mock)

    client = build_client(
        hass, email="someone@example.com", password="hunter2"
    )

    with pytest.raises(AuraAuthError):
        await client.get_frame(FRAME_ID)

    assert calls == [401, 401]


async def test_a_client_without_credentials_cannot_renew(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Without email and password there is nothing to log in with."""
    calls = mock_frame_responses(aioclient_mock, 401)

    with pytest.raises(AuraAuthError):
        await build_client(hass).get_frame(FRAME_ID)

    assert calls == [401]


async def test_login_reports_the_session_it_opened(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The config flow stores what login returns, so it has to be reported."""
    mock_login(aioclient_mock)
    stored: list[dict] = []

    client = AuraClient(
        async_get_clientsession(hass),
        "0BE1A0DB-0000-0000-0000-000000000000",
        on_credentials_refreshed=stored.append,
    )
    await client.login("someone@example.com", "hunter2")

    assert client.credentials == {"user_id": "1", "auth_token": "neu"}
    assert stored == [{"user_id": "1", "auth_token": "neu"}]


async def test_a_session_rejected_again_later_is_not_renewed_again(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Poll after poll must not turn into login after login."""
    calls = mock_frame_responses(aioclient_mock, 401, 200, 401)
    mock_login(aioclient_mock)

    client = build_client(
        hass, email="someone@example.com", password="hunter2"
    )
    await client.get_frame(FRAME_ID)

    with pytest.raises(AuraAuthError):
        await client.get_frame(FRAME_ID)

    assert calls == [401, 200, 401]
    logins = [
        call for call in aioclient_mock.mock_calls if "login" in str(call[1])
    ]
    assert len(logins) == 1
