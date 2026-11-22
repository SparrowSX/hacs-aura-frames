"""Tests for the Aura Frames coordinator."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.aura_frames.api import AuraApiError, AuraAuthError
from custom_components.aura_frames.const import (
    CONF_AUTH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
    MAX_BRIDGED_POLL_FAILURES,
    POWER_STATE_FORCED_OFF,
    POWER_STATE_FORCED_ON,
    POWER_STATE_NORMAL,
    STORAGE_POWER_STATE,
    STORAGE_SAVED_SCHEDULE,
    UPDATE_INTERVAL_SECONDS,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .const import FRAME_ID, SAVED_SCHEDULE


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


async def test_turn_off_moves_the_off_time_and_remembers_the_schedule(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Off is still the off time moved to now, with the start time kept."""
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": switch_entity_id(hass)},
        blocking=True,
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_sleep"] is True
    assert written["scheduled_display_on_at"] == "1969-12-31T07:30:00.000Z"
    assert written["scheduled_display_off_at"] != "1969-12-31T22:00:00.000Z"

    saved = init_integration.data[STORAGE_SAVED_SCHEDULE]
    assert saved["scheduled_display_on_at"] == "1969-12-31T07:30:00.000Z"
    assert saved["scheduled_display_off_at"] == "1969-12-31T22:00:00.000Z"
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_FORCED_OFF


async def test_turn_on_switches_the_scheduled_sleep_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Restoring the window cannot light a frame outside that window.

    The frame shows photos between its on and off time, so writing the old
    schedule back after the off time has passed leaves it dark. Only
    switching the scheduled sleep off turns the display on whatever the
    time is.
    """
    entity_id = switch_entity_id(hass)
    force_off(hass, init_integration, SAVED_SCHEDULE)
    mock_client.update_frame.reset_mock()

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_sleep"] is False
    assert written["scheduled_display_on_at"] == "1969-12-31T07:30:00.000Z"
    assert written["scheduled_display_off_at"] == "1969-12-31T22:00:00.000Z"
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_FORCED_ON
    assert hass.states.get(entity_id).state == "on"


async def test_turn_on_forces_a_frame_the_switch_never_touched(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """A dark frame has to come on even while the switch already reads on.

    Nothing in Home Assistant knows whether the frame's own schedule has it
    lit right now, and switch.turn_on reaches the entity whatever state it
    is in. Treating that as a no-op is what left the frame dark.
    """
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_NORMAL
    mock_client.update_frame.reset_mock()

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": switch_entity_id(hass)},
        blocking=True,
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_sleep"] is False
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_FORCED_ON
    assert init_integration.data[STORAGE_SAVED_SCHEDULE] == SAVED_SCHEDULE


async def test_a_second_turn_on_writes_nothing(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Once the display is forced on there is nothing left to send."""
    entity_id = switch_entity_id(hass)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    mock_client.update_frame.reset_mock()

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )

    mock_client.update_frame.assert_not_called()


async def test_the_off_time_the_switch_moved_is_repaired(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    frame: dict,
) -> None:
    """The remembered schedule outlives the cycle it was captured in.

    Turning off overwrites the frame's off time, so the record read on the
    way back carries that forced value. Writing the record back as found is
    what walked the off time forward until the frame stopped coming on.
    """
    entity_id = switch_entity_id(hass)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )

    # What the frame looks like once Aura has applied that write.
    frame["scheduled_display_off_at"] = "1969-12-31T18:54:00.000Z"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_off_at"] == "1969-12-31T22:00:00.000Z"
    assert (
        init_integration.data[STORAGE_SAVED_SCHEDULE]["scheduled_display_off_at"]
        == "1969-12-31T22:00:00.000Z"
    )


async def test_an_unreadable_remembered_time_is_left_out(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sending null would clear the user's schedule; omitting keeps it.

    The update is a partial one, so a field left out of the payload keeps
    whatever the frame has. That is strictly better than refusing to switch
    the frame on, which used to strand it in the dark.
    """
    force_off(
        hass,
        init_integration,
        {
            "scheduled_display_sleep": True,
            "scheduled_display_on_at": "so kaputt",
            "scheduled_display_off_at": "1969-12-31T22:00:00.000Z",
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
    assert "scheduled_display_on_at" not in written
    assert written["scheduled_display_off_at"] == "1969-12-31T22:00:00.000Z"
    assert written["scheduled_display_sleep"] is False
    assert "unreadable" in caplog.text
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_FORCED_ON


async def test_a_missing_remembered_off_time_is_left_out(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """None means the frame had no off time, so there is none to write."""
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
    assert "scheduled_display_off_at" not in written
    assert written["scheduled_display_on_at"] == "1969-12-31T07:30:00.000Z"


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


async def test_setup_stores_the_session_and_the_next_one_resumes_it(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client_class: MagicMock,
    mock_client: AsyncMock,
) -> None:
    """Logging in happens once, not on every setup.

    Every login opens a new session on the Aura account, and the frame reacts
    to that churn by dropping its own session, restarting and showing its
    pairing code. So the session the first setup opens is kept on the entry,
    and every setup after it resumes that one.
    """
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_client.login.call_count == 1
    assert config_entry.data[CONF_USER_ID] == "1"
    assert config_entry.data[CONF_AUTH_TOKEN] == "token"

    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_client.login.call_count == 1
    resumed = mock_client_class.call_args.kwargs
    assert resumed["user_id"] == "1"
    assert resumed["auth_token"] == "token"


async def test_a_session_the_api_rejects_reaches_reauth(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """The client renews a stale session itself; what is left is a real one.

    An AuraAuthError out of the client means the renewal failed too, so the
    credentials are the problem and only the user can fix them.
    """
    mock_client.get_frame.side_effect = AuraAuthError("abgelaufen")

    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


def wifi_entity_id(hass: HomeAssistant) -> str:
    """Return the entity id of the frame's Wi-Fi sensor.

    Its reading is a plain string, so a test can tell "the last reading is
    still being served" from "the entity is gone" at a glance.
    """
    entity_ids = [
        entity_id
        for entity_id in hass.states.async_entity_ids("sensor")
        if entity_id.endswith("wi_fi_network")
    ]
    assert len(entity_ids) == 1
    return entity_ids[0]


async def poll(hass: HomeAssistant, times: int = 1) -> None:
    """Let the coordinator run its update the given number of times."""
    for attempt in range(1, times + 1):
        async_fire_time_changed(
            hass,
            dt_util.utcnow()
            + timedelta(seconds=UPDATE_INTERVAL_SECONDS * attempt + 1),
        )
        await hass.async_block_till_done()


async def test_a_failed_poll_keeps_the_last_reading(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """One failed request must not take every entity down with it.

    The host's DNS fails every so often. Dropping to unavailable for a whole
    interval also makes Home Assistant discard service calls aimed at the
    frame, so a scheduled switch-on silently never happens -- much worse
    than serving a reading that is one interval old.
    """
    entity_id = wifi_entity_id(hass)
    assert hass.states.get(entity_id).state == "WLAN"

    mock_client.get_frame.side_effect = AuraApiError(
        "Connection error: Cannot connect to host api.pushd.com:443"
    )
    await poll(hass)

    assert hass.states.get(entity_id).state == "WLAN"
    assert hass.states.get(switch_entity_id(hass)).state == "on"


async def test_entities_go_unavailable_once_the_outage_outlasts_the_bridge(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Bridging is for a blip; a real outage still has to be visible."""
    entity_id = wifi_entity_id(hass)
    mock_client.get_frame.side_effect = AuraApiError("weg")
    mock_client.get_frame.reset_mock()

    await poll(hass, MAX_BRIDGED_POLL_FAILURES)
    # Every one of those polls has to have been tried, or the bridge would
    # look longer than it is.
    assert mock_client.get_frame.call_count == MAX_BRIDGED_POLL_FAILURES
    assert hass.states.get(entity_id).state == "WLAN"

    await poll(hass, MAX_BRIDGED_POLL_FAILURES + 1)
    assert hass.states.get(entity_id).state == "unavailable"


async def test_a_good_poll_clears_the_bridge(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    frame: dict,
) -> None:
    """Otherwise scattered failures would add up to an outage over days."""
    entity_id = wifi_entity_id(hass)

    for _ in range(MAX_BRIDGED_POLL_FAILURES * 2):
        mock_client.get_frame.side_effect = AuraApiError("weg")
        await poll(hass)
        mock_client.get_frame.side_effect = lambda *args, **kwargs: frame
        await poll(hass)

    assert hass.states.get(entity_id).state == "WLAN"


async def test_a_connection_error_at_setup_still_fails_the_setup(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """With no reading to fall back on there is nothing to bridge with."""
    mock_client.get_frame.side_effect = AuraApiError("Connection error")

    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
