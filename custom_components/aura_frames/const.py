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

UPDATE_INTERVAL_SECONDS = 60

# How long the client waits before opening another session after the one it
# just opened was rejected.
LOGIN_COOLDOWN_SECONDS = 300

SCHEDULE_EPOCH_DATE = "1969-12-31"
