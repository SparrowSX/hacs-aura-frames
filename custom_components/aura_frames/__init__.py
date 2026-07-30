"""The Aura Frames integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuraApiError, AuraAuthError, AuraClient
from .const import (
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_FRAME_ID,
    CONF_PASSWORD,
    DATA_COORDINATOR,
    DOMAIN,
)
from .coordinator import AuraFramesCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BUTTON,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Aura Frames from a config entry."""
    session = async_get_clientsession(hass)
    client = AuraClient(
        session,
        entry.data[CONF_DEVICE_ID],
    )

    try:
        await client.login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    except AuraAuthError as err:
        raise ConfigEntryAuthFailed("Aura credentials are invalid") from err
    except AuraApiError as err:
        raise ConfigEntryNotReady(f"Unable to connect to Aura: {err}") from err

    coordinator = AuraFramesCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    ):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
