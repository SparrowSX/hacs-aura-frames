"""The Aura Frames integration."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuraApiError, AuraAuthError, AuraClient
from .const import (
    CONF_AUTH_TOKEN,
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_USER_ID,
)
from .coordinator import AuraFramesConfigEntry, AuraFramesCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BUTTON,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(
    hass: HomeAssistant, entry: AuraFramesConfigEntry
) -> bool:
    """Set up Aura Frames from a config entry."""
    session = async_get_clientsession(hass)

    @callback
    def store_credentials(credentials: dict[str, str | None]) -> None:
        """Keep a newly opened session on the entry."""
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, **credentials}
        )

    client = AuraClient(
        session,
        entry.data[CONF_DEVICE_ID],
        user_id=entry.data.get(CONF_USER_ID),
        auth_token=entry.data.get(CONF_AUTH_TOKEN),
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        on_credentials_refreshed=store_credentials,
    )

    # Resume the stored session rather than logging in again. Every login
    # opens a new session on the Aura account, and an account whose sessions
    # churn takes the frame with it: it drops its own session, restarts and
    # shows its pairing code before it comes back. Only an entry from before
    # the session was stored still has to log in here, once; from then on the
    # client renews it by itself, and only when the API rejects it.
    if not client.is_authenticated:
        try:
            await client.login(
                entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD]
            )
        except AuraAuthError as err:
            raise ConfigEntryAuthFailed("Aura credentials are invalid") from err
        except AuraApiError as err:
            raise ConfigEntryNotReady(f"Unable to connect to Aura: {err}") from err

    coordinator = AuraFramesCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AuraFramesConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
