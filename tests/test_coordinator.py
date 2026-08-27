"""Tests for the Aura Frames coordinator."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.aura_frames.api import AuraApiError
from custom_components.aura_frames.const import (
    DOMAIN,
    POWER_STATE_FORCED_OFF,
    STORAGE_POWER_STATE,
    STORAGE_SAVED_SCHEDULE,
    UPDATE_INTERVAL_SECONDS,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .const import FRAME_ID


def frame_device(hass: HomeAssistant, entry: MockConfigEntry):
    """Return the device registry entry for the frame."""
    registry = dr.async_get(hass)
    return next(
        device
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
        if (DOMAIN, FRAME_ID) in device.identifiers
    )


def switch_entity_id(hass: HomeAssistant) -> str:
    """Return the entity id of the frame's power switch."""
    entity_ids = hass.states.async_entity_ids("switch")
    assert len(entity_ids) == 1
    return entity_ids[0]


def force_off(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    saved_schedule: dict,
) -> None:
    """Put the entry into the forced-off state with a given saved schedule."""
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            STORAGE_POWER_STATE: POWER_STATE_FORCED_OFF,
            STORAGE_SAVED_SCHEDULE: saved_schedule,
        },
    )


async def test_turn_off_saves_the_schedule_and_turn_on_restores_it(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """The round trip stores the frame's times and writes them back."""
    entity_id = switch_entity_id(hass)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )

    saved = init_integration.data[STORAGE_SAVED_SCHEDULE]
    assert saved["scheduled_display_on_at"] == "1969-12-31T07:30:00.000Z"
    assert saved["scheduled_display_off_at"] == "1969-12-31T22:00:00.000Z"
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_FORCED_OFF

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_on_at"] == "1969-12-31T07:30:00.000Z"
    assert written["scheduled_display_off_at"] == "1969-12-31T22:00:00.000Z"
    assert init_integration.data[STORAGE_SAVED_SCHEDULE] is None


async def test_turn_on_refuses_an_unreadable_start_time(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """An unreadable start time must not be written back as null.

    Sending null would clear the time the display comes on, and clearing the
    saved schedule afterwards would leave nothing to retry with.
    """
    saved = {
        "scheduled_display_sleep": True,
        "scheduled_display_on_at": "so kaputt",
        "scheduled_display_off_at": "1969-12-31T22:00:00.000Z",
    }
    force_off(hass, init_integration, saved)
    mock_client.update_frame.reset_mock()

    with pytest.raises(AuraApiError, match="start time is unreadable"):
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": switch_entity_id(hass)},
            blocking=True,
        )

    mock_client.update_frame.assert_not_called()
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_FORCED_OFF
    assert init_integration.data[STORAGE_SAVED_SCHEDULE] == saved


async def test_turn_on_still_restores_when_the_off_time_is_unreadable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Refusing here would strand the frame in the dark, so it only warns."""
    force_off(
        hass,
        init_integration,
        {
            "scheduled_display_sleep": True,
            "scheduled_display_on_at": "1969-12-31T07:30:00.000Z",
            "scheduled_display_off_at": "auch kaputt",
        },
    )
    mock_client.update_frame.reset_mock()

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": switch_entity_id(hass)},
        blocking=True,
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_on_at"] == "1969-12-31T07:30:00.000Z"
    assert written["scheduled_display_off_at"] is None
    assert init_integration.data[STORAGE_SAVED_SCHEDULE] is None
    assert "unreadable" in caplog.text


async def test_a_missing_saved_off_time_is_restored_as_missing(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """None means the frame had no off time; writing None back is faithful."""
    force_off(
        hass,
        init_integration,
        {
            "scheduled_display_sleep": True,
            "scheduled_display_on_at": "1969-12-31T07:30:00.000Z",
            "scheduled_display_off_at": None,
        },
    )
    mock_client.update_frame.reset_mock()

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": switch_entity_id(hass)},
        blocking=True,
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_off_at"] is None
    assert init_integration.data[STORAGE_SAVED_SCHEDULE] is None


async def test_polling_stops_asking_for_the_schedule(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Only the first fetch merges the frame list; later polls do not."""
    first = mock_client.get_frame.call_args_list[0]
    assert first.kwargs["ensure_schedule"] is True

    mock_client.get_frame.reset_mock()
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=UPDATE_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    assert mock_client.get_frame.call_count >= 1
    for call in mock_client.get_frame.call_args_list:
        assert call.kwargs["ensure_schedule"] is False


async def test_the_switch_reports_its_new_state_without_waiting_for_a_poll(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """The refresh after a toggle returns identical data.

    With always_update=False that notifies no listeners, so the switch only
    reaches the frontend if the coordinator pushes the change itself.
    """
    entity_id = switch_entity_id(hass)
    assert hass.states.get(entity_id).state == "on"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )

    assert hass.states.get(entity_id).state == "off"


async def test_a_renamed_frame_reaches_the_device_registry(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    frame: dict,
) -> None:
    """Entities build their DeviceInfo once, so the poll has to carry this."""
    device = frame_device(hass, init_integration)
    assert device.name == "Flur"
    assert device.sw_version == "9.1"

    frame["name"] = "Wohnzimmer"
    frame["software_version"] = "9.2"
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=UPDATE_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    device = frame_device(hass, init_integration)
    assert device.name == "Wohnzimmer"
    assert device.sw_version == "9.2"


async def test_a_sparse_poll_does_not_clear_the_device_details(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    frame: dict,
) -> None:
    """Absent fields mean the response was sparse, not that they were reset."""
    frame.pop("name")
    frame.pop("software_version")
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=UPDATE_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    device = frame_device(hass, init_integration)
    assert device.name == "Flur"
    assert device.sw_version == "9.1"


async def test_a_real_model_field_wins_over_the_aspect_ratio(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    frame: dict,
) -> None:
    """The aspect ratio only stands in while the API reports no model."""
    device = frame_device(hass, init_integration)
    assert device.model == "16:10"

    frame["model"] = "Carver Mat"
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=UPDATE_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    device = frame_device(hass, init_integration)
    assert device.model == "Carver Mat"


async def test_the_entry_unloads_and_loads_again(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """runtime_data replaced hass.data, so the round trip must still work.

    Home Assistant keeps unloaded entities in the state machine and marks
    them unavailable rather than dropping them, so that is what to look for.
    """
    entity_id = switch_entity_id(hass)
    assert hass.states.get(entity_id).state == "on"

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(entity_id).state == "unavailable"

    assert await hass.config_entries.async_setup(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.LOADED
    assert hass.states.get(entity_id).state == "on"
