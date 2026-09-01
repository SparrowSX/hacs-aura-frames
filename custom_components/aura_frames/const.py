"""Constants for the Aura Frames integration."""

DOMAIN = "aura_frames"

API_BASE_URL = "https://api.pushd.com/v5"
APP_IDENTIFIER = "com.pushd.Framelord"
USER_AGENT = "Aura/4.11.66 (iPad; iOS 26.5.2; Scale/2.00)"
DEFAULT_LOCALE = "de"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_FRAME_ID = "frame_id"
CONF_DEVICE_ID = "device_id"
# The session the API hands out at login. Stored so that a restart reuses
# it instead of opening a new one.
CONF_USER_ID = "user_id"
CONF_AUTH_TOKEN = "auth_token"

ATTR_FRAME_ID = "frame_id"
ATTR_ASSET_ID = "asset_id"
ATTR_LANDSCAPE_URL = "landscape_url"

POWER_STATE_NORMAL = "normal"
POWER_STATE_FORCED_OFF = "forced_off"

STORAGE_SAVED_SCHEDULE = "saved_schedule"
STORAGE_POWER_STATE = "power_state"

# Aura's API is the mobile app's, and the app only talks to it while
# someone has it open. A frame that is polled around the clock sees far more
# traffic on its account than Aura ever designed for, so poll at a rate that
# still feels live for a photo frame and no faster.
UPDATE_INTERVAL_SECONDS = 300

# How stale the frame's own heartbeat may be before it counts as offline.
# Polling every five minutes means the heartbeat read here is up to that old
# already, so the window has to be a multiple of the interval.
ONLINE_GRACE_SECONDS = UPDATE_INTERVAL_SECONDS * 3

# How long the client waits before opening another session after the one it
# just opened was rejected.
LOGIN_COOLDOWN_SECONDS = 300

SCHEDULE_EPOCH_DATE = "1969-12-31"
