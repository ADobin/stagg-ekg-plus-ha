DOMAIN = "fellow_stagg"

# Pushed state resets the coordinator timer, so a refresh only runs when the
# kettle has been silent this long: it verifies the link or reconnects.
UPDATE_INTERVAL = 15  # seconds
# Silence on an open connection treated as a dead link (the kettle streams ~1 frame/s)
FRAME_TIMEOUT = 20  # seconds
