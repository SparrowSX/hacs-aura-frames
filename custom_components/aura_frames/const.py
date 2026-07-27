"""Constants for Aura Frames."""

from datetime import timedelta

DOMAIN = "aura_frames"

API_URL = "https://api.pushd.com/v5"

DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)

CONF_USER_ID = "user_id"
CONF_TOKEN = "token"
CONF_CLIENT_ID = "client_id"
CONF_FRAME_ID = "frame_id"
