DOMAIN = "fellow_stagg"

# Seconds after a connection loss before entities become unavailable
DISCONNECT_GRACE = 15
# Link check cadence and the silence (the kettle streams ~1 frame/s) treated as a dead link
LINK_CHECK_INTERVAL = 5
FRAME_TIMEOUT = 20
# Delays between reconnect attempts; the last value repeats
RECONNECT_BACKOFF = (0, 2, 5, 15, 30, 60)
