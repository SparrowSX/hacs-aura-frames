"""Shared fixtures data for the Aura Frames tests."""

from custom_components.aura_frames.const import (
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_FRAME_ID,
    CONF_PASSWORD,
    POWER_STATE_NORMAL,
    STORAGE_POWER_STATE,
    STORAGE_WANTED_OFF,
    STORAGE_WANTED_ON,
)

FRAME_ID = "42"

ENTRY_DATA = {
    CONF_EMAIL: "someone@example.com",
    CONF_PASSWORD: "hunter2",
    CONF_FRAME_ID: FRAME_ID,
    CONF_DEVICE_ID: "0BE1A0DB-0000-0000-0000-000000000000",
    STORAGE_POWER_STATE: POWER_STATE_NORMAL,
}

# A frame as the API returns it, with a schedule that runs 07:30 to 22:00.
FRAME = {
    "id": FRAME_ID,
    "name": "Flur",
    "num_assets": 7,
    "wifi_network": "WLAN",
    "software_version": "9.1",
    "display_aspect_ratio": "16:10",
    "time_zone": "Europe/Berlin",
    "scheduled_display_sleep": True,
    "scheduled_display_on_at": "1969-12-31T07:30:00.000Z",
    "scheduled_display_off_at": "1969-12-31T22:00:00.000Z",
    "last_impression": {"asset": {"original_file_name": "foto.jpg"}},
}

# One edge of that window as versions up to 0.6.0 noted it down: the edge
# the switch had just overwritten, kept so the opposite press could give it
# back. 0.7.0 takes such a note up as a wanted time.
HELD_OFF_EDGE = {"scheduled_display_off_at": FRAME["scheduled_display_off_at"]}
HELD_ON_EDGE = {"scheduled_display_on_at": FRAME["scheduled_display_on_at"]}

# The same window as the wanted schedule, in the shape the config entry
# holds it from 0.7.0 on.
WANTED_ON = "07:30:00"
WANTED_OFF = "22:00:00"
WANTED = {STORAGE_WANTED_ON: WANTED_ON, STORAGE_WANTED_OFF: WANTED_OFF}

# A record left behind by a version that remembered all three fields and
# wrote both times with the same date.
PRE_0_6_SAVED_SCHEDULE = {
    "scheduled_display_sleep": False,
    "scheduled_display_on_at": "1969-12-31T10:01:18.000Z",
    "scheduled_display_off_at": "1969-12-31T18:54:14.000Z",
}
