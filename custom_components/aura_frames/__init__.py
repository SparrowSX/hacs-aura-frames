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
    STORAGE_SAVED_SCHEDULE,
    STORAGE_WANTED_OFF,
    STORAGE_WANTED_ON,
)
from .coordinator import (
    SCHEDULE_FIELD_OFF,
    SCHEDULE_FIELD_ON,
    AuraFramesConfigEntry,
    AuraFramesCoordinator,
    wall_clock,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BUTTON,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]


@callback
def _async_take_up_the_remembered_schedule(
    hass: HomeAssistant, entry: AuraFramesConfigEntry
) -> None:
    """Turn a schedule remembered by an earlier version into a wanted one.

    Up to 0.6.0 the switch noted down the edge it had just overwritten, so
    that the opposite press could give it back. That note was the only thing
    standing in for the user's intent, and it was never visible or editable.
    The wanted schedule took its place, so the note is taken up as the first
    wanted time and the key is dropped.

    A note from 0.5.0 or earlier carries the sleep flag as well, and its
    times are not a schedule anyone chose -- both were written with the same
    epoch date, which asks the frame for a sleep window running backwards.
    Those are discarded rather than taken up.
    """
    if STORAGE_SAVED_SCHEDULE not in entry.data:
        return

    saved = entry.data[STORAGE_SAVED_SCHEDULE]
    data = {
        key: value
        for key, value in entry.data.items()
        if key != STORAGE_SAVED_SCHEDULE
    }
    if isinstance(saved, dict) and "scheduled_display_sleep" not in saved:
        for field, storage_key in (
            (SCHEDULE_FIELD_ON, STORAGE_WANTED_ON),
            (SCHEDULE_FIELD_OFF, STORAGE_WANTED_OFF),
        ):
            if data.get(storage_key) is None and (
                clock := wall_clock(saved.get(field))
            ):
                data[storage_key] = clock
                _LOGGER.info(
                    "Taking the remembered %s (%s) up as the wanted schedule",
                    field,
                    clock,
                )

    hass.config_entries.async_update_entry(entry, data=data)


async def async_setup_entry(
    hass: HomeAssistant, entry: AuraFramesConfigEntry
) -> bool:
    """Set up Aura Frames from a config entry."""
    _async_take_up_the_remembered_schedule(hass, entry)
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
