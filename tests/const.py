"""Shared fixtures data for the Aura Frames tests."""

from custom_components.aura_frames.const import (
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_FRAME_ID,
    CONF_PASSWORD,
    POWER_STATE_NORMAL,
    STORAGE_POWER_STATE,
    STORAGE_SAVED_SCHEDULE,
)

FRAME_ID = "42"

ENTRY_DATA = {
    CONF_EMAIL: "someone@example.com",
    CONF_PASSWORD: "hunter2",
    CONF_FRAME_ID: FRAME_ID,
    CONF_DEVICE_ID: "0BE1A0DB-0000-0000-0000-000000000000",
    STORAGE_POWER_STATE: POWER_STATE_NORMAL,
    STORAGE_SAVED_SCHEDULE: None,
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
