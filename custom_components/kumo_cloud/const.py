"""Constants for Kumo Cloud."""

from datetime import timedelta

DOMAIN = "kumo_cloud"

# The API allows ~50 requests/minute; each poll costs 1 per site + 1 per unit.
SCAN_INTERVAL = timedelta(seconds=60)
# While the real-time socket is connected, polling is only a backstop.
REALTIME_SCAN_INTERVAL = timedelta(minutes=5)

# Site weather (outdoor temperature) changes slowly; 1 request per site.
WEATHER_REFRESH_SECONDS = 900

# Merge commands sent within this many seconds into one request, so tapping
# the setpoint arrow five times sends one command, not five.
COMMAND_DEBOUNCE = 1.0

CONF_REALTIME = "realtime"
DEFAULT_REALTIME = True

# Wait this long after a command before re-reading state from the cloud.
COMMAND_REFRESH_DELAY = 10

# Re-read the account's site list this often (seconds).
SITE_REFRESH_SECONDS = 3600
