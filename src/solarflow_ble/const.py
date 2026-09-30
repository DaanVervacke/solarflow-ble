"""SolarFlow BLE constants."""

WRITE_CHARACTERISTIC_UUID = "0000c304-0000-1000-8000-00805f9b34fb"
NOTIFY_CHARACTERISTIC_UUID = "0000c305-0000-1000-8000-00805f9b34fb"
MANUFACTURER_ID = 0x4F48
DEFAULT_KEEPALIVE_SECONDS = 30.0
DEFAULT_RESPONSE_TIMEOUT = 10.0
DEFAULT_BLE_SPP_DELAY = 0.3
BLESPP_OK_MESSAGE_ID = 1009
# BLE notifications fit in a few hundred bytes, but ESPHome proxy
# transports can deliver much larger frames; 64 KiB is far above any
# legitimate report while still bounding hostile payloads.
MAX_JSON_PAYLOAD_BYTES = 64 * 1024
