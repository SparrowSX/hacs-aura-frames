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
POWER_STATE_FORCED_ON = "forced_on"

STORAGE_SAVED_SCHEDULE = "saved_schedule"
STORAGE_POWER_STATE = "power_state"

# The schedule the user wants the frame to run: two wall-clock times, held
# in the config entry so that every entity reads the same one and it
# survives a restart. The frame's own record is not that schedule -- the
# switch overwrites one of its times whenever it forces the display, and the
# Aura app writes it too -- so what it reports can only be read as the
# current state, never as the intent.
STORAGE_WANTED_ON = "wanted_on_at"
STORAGE_WANTED_OFF = "wanted_off_at"

# Aura's API is the mobile app's, and the app only talks to it while
# someone has it open. A frame that is polled around the clock sees far more
# traffic on its account than Aura ever designed for, so poll at a rate that
# still feels live for a photo frame and no faster.
UPDATE_INTERVAL_SECONDS = 300

# How many polls in a row may fail before the entities go unavailable.
# A failed poll used to drop every entity straight to unavailable for a whole
# interval, and a service call that lands in such a window is discarded by
# Home Assistant with "entity not currently available" -- a scheduled
# switch-on simply never happens. The frame's state hardly moves between
# polls, so serving the last reading across a brief outage is both closer to
# the truth and far less disruptive than going dark. Three polls bridges a
# quarter of an hour, after which something is wrong enough to show.
MAX_BRIDGED_POLL_FAILURES = 3

# How stale the frame's own heartbeat may be before it counts as offline.
# Polling every five minutes means the heartbeat read here is up to that old
# already, so the window has to be a multiple of the interval.
ONLINE_GRACE_SECONDS = UPDATE_INTERVAL_SECONDS * 3

# How long the client waits before opening another session after the one it
# just opened was rejected.
LOGIN_COOLDOWN_SECONDS = 300

# Aura stores the display schedule as two wall-clock times carrying an epoch
# date and a ``Z`` it does not mean. The pair delimits the frame's sleep
# window -- the display is dark from the off time until the on time -- and the
# date is what tells the frame whether that window crosses midnight: the Aura
# app writes the on time on the day after the off time when it does. A pair
# written on the same date describes a window that ends on the same day, so
# dating both 1969-12-31 for an evening-to-morning schedule asks the frame for
# a window that runs backwards.
SCHEDULE_EPOCH_DATE = "1969-12-31"
SCHEDULE_NEXT_DAY_DATE = "1970-01-01"
