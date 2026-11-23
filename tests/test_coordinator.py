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
from custom_components.aura_frames.coordinator import AuraFramesCoordinator
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
    STORAGE_WANTED_OFF,
    STORAGE_WANTED_ON,
    UPDATE_INTERVAL_SECONDS,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .const import (
    FRAME_ID,
    HELD_OFF_EDGE,
    PRE_0_6_SAVED_SCHEDULE,
    WANTED_OFF,
    WANTED_ON,
)


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


def select_entity_id(hass: HomeAssistant) -> str:
    """Return the entity id of the frame's display mode select."""
    entity_ids = hass.states.async_entity_ids("select")
    assert len(entity_ids) == 1
    return entity_ids[0]


def time_entity_ids(hass: HomeAssistant) -> dict[str, str]:
    """Return the wanted wake and sleep time entities, keyed on/off."""
    entity_ids = hass.states.async_entity_ids("time")
    assert len(entity_ids) == 2
    return {
        ("on" if "wake" in entity_id else "off"): entity_id
        for entity_id in entity_ids
    }


def force_off(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Put the entry into the forced-off state."""
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, STORAGE_POWER_STATE: POWER_STATE_FORCED_OFF},
    )


def at_half_past_six(freezer) -> None:
    """Stop the clock at 18:30 in the frame's own time zone."""
    freezer.move_to("2026-11-22T17:30:00+00:00")


async def test_the_wanted_schedule_is_taken_from_the_frame_once(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    frame: dict,
) -> None:
    """A frame the switch has not touched is running what the user wants.

    That is the only guess available for an entry that has never held a
    wanted schedule, and the right one. From then on the wanted times are
    changed in Home Assistant, not read back.
    """
    assert init_integration.data[STORAGE_WANTED_ON] == WANTED_ON
    assert init_integration.data[STORAGE_WANTED_OFF] == WANTED_OFF

    states = time_entity_ids(hass)
    assert hass.states.get(states["on"]).state == WANTED_ON
    assert hass.states.get(states["off"]).state == WANTED_OFF


async def test_a_forced_time_is_never_taken_for_the_wanted_one(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    frame: dict,
    freezer,
) -> None:
    """Reading the frame back is what walked the schedule out of shape.

    Once a wanted time is held, no poll touches it again -- and the value
    the frame reports after a switch-off is a forced one, not a schedule
    anyone chose.
    """
    at_half_past_six(freezer)
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": switch_entity_id(hass)},
        blocking=True,
    )
    frame["scheduled_display_off_at"] = "1969-12-31T18:30:00.000Z"

    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=UPDATE_INTERVAL_SECONDS + 1)
    )
    await hass.async_block_till_done()

    assert init_integration.data[STORAGE_WANTED_OFF] == WANTED_OFF


async def test_turn_off_moves_the_time_the_frame_falls_asleep(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    freezer,
) -> None:
    """Off moves one edge of the sleep window and writes the wanted other."""
    at_half_past_six(freezer)

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": switch_entity_id(hass)},
        blocking=True,
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_sleep"] is True
    assert written["scheduled_display_off_at"] == "1969-12-31T18:30:00.000Z"
    # The frame now sleeps from this evening until the wanted wake time, so
    # that time is the day after. Dating both alike, as versions up to 0.5.0
    # did, describes a window running backwards.
    assert written["scheduled_display_on_at"] == "1970-01-01T07:30:00.000Z"
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_FORCED_OFF


async def test_turn_on_moves_the_time_the_frame_wakes(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    freezer,
) -> None:
    """On is the mirror image of off, not a switch into automatic mode.

    Switching the frame's scheduled sleep off lights an evening frame too,
    which is why it looked right -- but that field is the automatic mode the
    Aura app offers, where the frame's own light sensor decides. A frame
    handed to it goes dark again at dusk by itself, with Home Assistant
    still reporting the switch as on.
    """
    entity_id = switch_entity_id(hass)
    at_half_past_six(freezer)
    force_off(hass, init_integration)
    mock_client.update_frame.reset_mock()

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_sleep"] is True
    assert written["scheduled_display_on_at"] == "1970-01-01T18:30:00.000Z"
    # The wanted bedtime stands: the frame is lit from now until 22:00
    # rather than around the clock.
    assert written["scheduled_display_off_at"] == "1969-12-31T22:00:00.000Z"
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_FORCED_ON
    assert hass.states.get(entity_id).state == "on"


async def test_a_window_that_ends_on_the_same_day_keeps_one_date(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    freezer,
) -> None:
    """Switched on past its bedtime, the frame sleeps tomorrow evening.

    The wake time is then later in the day than the sleep time, so both
    belong to the same day and the window no longer crosses midnight.
    """
    freezer.move_to("2026-11-22T22:30:00+00:00")
    mock_client.update_frame.reset_mock()

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": switch_entity_id(hass)},
        blocking=True,
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_off_at"] == "1969-12-31T22:00:00.000Z"
    assert written["scheduled_display_on_at"] == "1969-12-31T23:30:00.000Z"


async def test_turn_on_forces_a_frame_the_switch_never_touched(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    freezer,
) -> None:
    """A dark frame has to come on even while the switch already reads on.

    Nothing in Home Assistant knows whether the frame's own schedule has it
    lit right now, and switch.turn_on reaches the entity whatever state it
    is in. Treating that as a no-op is what left the frame dark.
    """
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_NORMAL
    at_half_past_six(freezer)
    mock_client.update_frame.reset_mock()

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": switch_entity_id(hass)},
        blocking=True,
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_on_at"] == "1970-01-01T18:30:00.000Z"
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_FORCED_ON


async def test_a_second_press_writes_again_and_repairs_the_first(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    frame: dict,
    freezer,
) -> None:
    """A press insists, and never carries a forced time forward.

    The display can be put back to sleep behind our back, so a press has to
    write even when Home Assistant already has the frame in the state being
    asked for. And because every press writes the wanted schedule for the
    edge it is not moving, the forced time from the press before is
    overwritten rather than taken for the user's own -- which is how the
    sleep time used to walk forward until the frame stopped coming on.
    """
    entity_id = switch_entity_id(hass)
    at_half_past_six(freezer)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    # What the frame looks like once Aura has applied that write.
    frame["scheduled_display_on_at"] = "1970-01-01T18:30:00.000Z"
    mock_client.update_frame.reset_mock()
    freezer.move_to("2026-11-22T18:00:00+00:00")

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_sleep"] is True
    assert written["scheduled_display_off_at"] == "1969-12-31T19:00:00.000Z"
    assert written["scheduled_display_on_at"] == "1970-01-01T07:30:00.000Z"


async def test_the_switch_shows_the_schedule_and_what_is_wanted(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """The values that decide whether the display is lit are visible.

    Next to them is what the user asked for, so the two can be compared at
    a glance -- which is the only comparison available, since the frame
    cannot say whether a difference is drift or a change made in the app.
    """
    state = hass.states.get(switch_entity_id(hass))

    assert state.attributes["scheduled_sleep"] is True
    assert state.attributes["scheduled_on_at"] == "07:30:00"
    assert state.attributes["scheduled_off_at"] == "22:00:00"
    assert state.attributes["wanted_on_at"] == WANTED_ON
    assert state.attributes["wanted_off_at"] == WANTED_OFF
    assert state.attributes["frame_time_zone"] == "Europe/Berlin"
    assert state.attributes["power_state"] == POWER_STATE_NORMAL


async def test_a_changed_wanted_time_is_sent_once(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Changing the wanted schedule writes it to the frame there and then.

    Nothing holds the frame to it afterwards: the Aura app writes that
    record too, and the switch moves one of its times on purpose, so a
    difference cannot be told apart from intent.
    """
    force_off(hass, init_integration)
    mock_client.update_frame.reset_mock()

    await hass.services.async_call(
        "time",
        "set_value",
        {"entity_id": time_entity_ids(hass)["off"], "time": "23:00:00"},
        blocking=True,
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_off_at"] == "1969-12-31T23:00:00.000Z"
    assert written["scheduled_display_on_at"] == "1970-01-01T07:30:00.000Z"
    # The mode is the select's business, so a time change leaves it alone.
    assert "scheduled_display_sleep" not in written
    assert init_integration.data[STORAGE_WANTED_OFF] == "23:00:00"
    # Both edges are now the wanted ones, so nothing is forced any more.
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_NORMAL


async def test_the_button_sends_the_wanted_schedule_again(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Drift the integration cannot detect can still be undone by hand."""
    force_off(hass, init_integration)
    mock_client.update_frame.reset_mock()

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.flur_send_schedule"},
        blocking=True,
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_off_at"] == "1969-12-31T22:00:00.000Z"
    assert written["scheduled_display_on_at"] == "1970-01-01T07:30:00.000Z"
    assert "scheduled_display_sleep" not in written
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_NORMAL


async def test_a_time_the_frame_reports_unreadably_is_left_out(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    frame: dict,
    freezer,
) -> None:
    """Sending null would clear the user's schedule; omitting keeps it.

    The update is a partial one, so a field left out of the payload keeps
    whatever the frame has. That is strictly better than refusing to switch
    the frame on, which used to strand it in the dark.
    """
    frame["scheduled_display_off_at"] = "so kaputt"
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    at_half_past_six(freezer)
    mock_client.update_frame.reset_mock()

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": switch_entity_id(hass)},
        blocking=True,
    )

    written = mock_client.update_frame.call_args.args[1]
    assert "scheduled_display_off_at" not in written
    # With no sleep time going out there is nothing for the wake time to
    # fall after, so it keeps the plain epoch date.
    assert written["scheduled_display_on_at"] == "1969-12-31T18:30:00.000Z"
    # Nor is an unreadable value taken up as something the user wants.
    assert STORAGE_WANTED_OFF not in config_entry.data


async def test_the_mode_select_shows_how_the_frame_decides(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    frame: dict,
) -> None:
    """The field behind the automatic mode is readable in Home Assistant."""
    entity_id = select_entity_id(hass)
    assert hass.states.get(entity_id).state == "schedule"

    mock_client.get_frame.side_effect = lambda *args, **kwargs: {
        **frame,
        "scheduled_display_sleep": False,
    }
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=UPDATE_INTERVAL_SECONDS + 1)
    )
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "automatic"


@pytest.mark.parametrize(
    ("option", "sleep"), [("automatic", False), ("schedule", True)]
)
async def test_a_mode_change_carries_the_wanted_schedule(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    option: str,
    sleep: bool,
) -> None:
    """Either mode leaves the frame with the schedule the user asked for.

    In automatic mode the schedule is ignored, so an edge the switch forced
    would otherwise sit there waiting to come back the moment someone
    switches the schedule on again in the app.
    """
    force_off(hass, init_integration)
    mock_client.update_frame.reset_mock()

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select_entity_id(hass), "option": option},
        blocking=True,
    )

    written = mock_client.update_frame.call_args.args[1]
    assert written["scheduled_display_sleep"] is sleep
    assert written["scheduled_display_off_at"] == "1969-12-31T22:00:00.000Z"
    assert written["scheduled_display_on_at"] == "1970-01-01T07:30:00.000Z"
    assert init_integration.data[STORAGE_POWER_STATE] == POWER_STATE_NORMAL


async def test_a_remembered_edge_becomes_a_wanted_time(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    frame: dict,
) -> None:
    """The note an earlier version kept was the user's own time; keep it.

    Letting it go would mean adopting whatever the frame reports instead --
    and while the switch is holding the frame off, that is the forced time.
    """
    frame["scheduled_display_off_at"] = "1969-12-31T18:30:00.000Z"
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        data={
            **config_entry.data,
            STORAGE_SAVED_SCHEDULE: HELD_OFF_EDGE,
            STORAGE_POWER_STATE: POWER_STATE_FORCED_OFF,
        },
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert STORAGE_SAVED_SCHEDULE not in config_entry.data
    assert config_entry.data[STORAGE_WANTED_OFF] == WANTED_OFF
    assert config_entry.data[STORAGE_WANTED_ON] == WANTED_ON
    assert config_entry.data[STORAGE_POWER_STATE] == POWER_STATE_FORCED_OFF


async def test_a_schedule_remembered_by_an_even_earlier_version_is_dropped(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Those times are not a schedule anyone chose, so they are discarded.

    Up to 0.5.0 the note carried all three schedule fields and both times
    were written with the same date, which asks for a window running
    backwards. Taking them up as wanted times would make that permanent.
    """
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        data={
            **config_entry.data,
            STORAGE_SAVED_SCHEDULE: PRE_0_6_SAVED_SCHEDULE,
            STORAGE_POWER_STATE: POWER_STATE_FORCED_ON,
        },
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert STORAGE_SAVED_SCHEDULE not in config_entry.data
    assert config_entry.data[STORAGE_WANTED_ON] == WANTED_ON
    assert config_entry.data[STORAGE_WANTED_OFF] == WANTED_OFF


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


def test_the_debug_settings_line_leaves_out_what_a_log_should_not_carry() -> None:
    """Only plain settings go into the log: no nesting, nothing sensitive."""
    settings = AuraFramesCoordinator._settings_of(
        {
            "scheduled_display_sleep": True,
            "auto_brightness": False,
            "name": "Flur",
            "auth_token": "hunter2",
            "user_email": "someone@example.com",
            "preview_url": "https://example.com/a.jpg",
            "frame_environment": {"last_online_at": "2026-11-22T12:00:00Z"},
            "last_impression": {"asset": {}},
        }
    )

    assert settings == {
        "auto_brightness": False,
        "name": "Flur",
        "scheduled_display_sleep": True,
    }
