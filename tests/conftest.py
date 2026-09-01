"""Fixtures for the Aura Frames tests."""

from __future__ import annotations

import copy
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aura_frames.const import (
    CONF_AUTH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)
from homeassistant.core import HomeAssistant

from .const import ENTRY_DATA, FRAME, FRAME_ID


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load the integration from custom_components."""
    return


@pytest.fixture
def frame() -> dict:
    """Return a mutable copy of the frame payload for one test."""
    return copy.deepcopy(FRAME)


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry for the frame under test."""
    return MockConfigEntry(
        domain=DOMAIN,
        data=dict(ENTRY_DATA),
        unique_id=FRAME_ID,
        title="Flur",
    )


SESSION = {CONF_USER_ID: "1", CONF_AUTH_TOKEN: "token"}


@pytest.fixture
def mock_client_class(frame: dict) -> Generator[MagicMock]:
    """Patch AuraClient so no test reaches the network.

    get_frame returns the frame fixture by reference, so a test can mutate
    ``frame`` and have the next poll observe the change. login behaves like
    the real one: it leaves the client holding a session and reports it to
    whoever asked to be told.
    """
    with patch(
        "custom_components.aura_frames.AuraClient", autospec=True
    ) as client_class:
        client = client_class.return_value
        client.is_authenticated = False
        client.credentials = dict(SESSION)

        async def login(email: str, password: str) -> dict:
            client.is_authenticated = True
            report = client_class.call_args.kwargs["on_credentials_refreshed"]
            report(client.credentials)
            return {"id": "1", "auth_token": "token"}

        client.login.side_effect = login
        client.get_frame.side_effect = lambda *args, **kwargs: frame
        client.get_frames.return_value = [frame]
        client.update_frame.return_value = {"frame": frame}
        yield client_class


@pytest.fixture
def mock_client(mock_client_class: MagicMock) -> AsyncMock:
    """Return the client instance the integration will get."""
    return mock_client_class.return_value


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> MockConfigEntry:
    """Set up the integration and return its config entry."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
