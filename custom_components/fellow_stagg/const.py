DOMAIN = "fellow_stagg"

CONF_POLLING_INTERVAL = "polling_interval"
DEFAULT_POLLING_INTERVAL = 5   # seconds
MIN_POLLING_INTERVAL = 5
MAX_POLLING_INTERVAL = 60

# Target temperature range accepted by the kettle
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

# BLE UUIDs for the Fellow Stagg kettle’s “Serial Port Service”
SERVICE_UUID = "00001820-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "00002A80-0000-1000-8000-00805f9b34fb"

# The magic init sequence (in hex) used to authenticate with the kettle:
# ef dd 0b 30 31 32 33 34 35 36 37 38 39 30 31 32 33 34 9a 6d
INIT_SEQUENCE = bytes.fromhex("efdd0b3031323334353637383930313233349a6d")

# Notification window per poll; extended up to the timeout while required frames are missing
NOTIFY_WINDOW = 2.0   # seconds
NOTIFY_TIMEOUT = 5.0  # seconds
