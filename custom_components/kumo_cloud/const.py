"""Constants for Kumo Cloud."""

from datetime import timedelta

DOMAIN = "kumo_cloud"

# The API allows ~50 requests/minute; each poll costs 1 per site + 1 per unit.
SCAN_INTERVAL = timedelta(seconds=60)

# Wait this long after a command before re-reading state from the cloud.
COMMAND_REFRESH_DELAY = 10
