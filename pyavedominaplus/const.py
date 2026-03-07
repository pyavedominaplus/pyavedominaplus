"""Constants for AVE DominaPlus protocol."""

# Protocol control characters
STX = 0x02  # Start of text
ETX = 0x03  # End of text
EOT = 0x04  # End of transmission
GS = 0x1D  # Group separator (field separator)
RS = 0x1E  # Record separator

# Default WebSocket port
DEFAULT_WS_PORT = 14001

# Device family types
DEVICE_TYPE_LIGHT = 1
DEVICE_TYPE_DIMMER = 2
DEVICE_TYPE_SHUTTER = 3
DEVICE_TYPE_THERMOSTAT = 4
DEVICE_TYPE_ECONOMIZER = 5
DEVICE_TYPE_SCENARIO = 6
DEVICE_TYPE_ENERGY = 9
DEVICE_TYPE_P3000_AREA = 12
DEVICE_TYPE_P3000_SENSOR = 13
DEVICE_TYPE_AUDIO = 14
DEVICE_TYPE_SHUTTER_16 = 16
DEVICE_TYPE_ABANO = 17
DEVICE_TYPE_SHUTTER_19 = 19
DEVICE_TYPE_LIGHT_22 = 22

# Normalized application types
APP_TYPE_LIGHT = 1
APP_TYPE_SHUTTER = 3
APP_TYPE_THERMOSTAT = 4
APP_TYPE_SCENARIO = 6
APP_TYPE_ENERGY = 9

# Device type to app type mapping
DEVICE_TYPE_TO_APP_TYPE = {
    DEVICE_TYPE_LIGHT: APP_TYPE_LIGHT,
    DEVICE_TYPE_DIMMER: APP_TYPE_LIGHT,
    DEVICE_TYPE_LIGHT_22: APP_TYPE_LIGHT,
    DEVICE_TYPE_SHUTTER: APP_TYPE_SHUTTER,
    DEVICE_TYPE_SHUTTER_16: APP_TYPE_SHUTTER,
    DEVICE_TYPE_SHUTTER_19: APP_TYPE_SHUTTER,
    DEVICE_TYPE_THERMOSTAT: APP_TYPE_THERMOSTAT,
    DEVICE_TYPE_SCENARIO: APP_TYPE_SCENARIO,
    DEVICE_TYPE_ENERGY: APP_TYPE_ENERGY,
}

# Commands (client -> server)
CMD_LIST_MAPS = "LM"
CMD_LIST_DEVICES = "LDI"
CMD_LIST_DEVICE_ADDRESSES = "LI2"
CMD_LIST_MAP_COMMANDS = "LMC"
CMD_LIST_MAP_LABELS = "LML"
CMD_GET_THERMOSTAT_STATUS = "WTS"
CMD_SET_THERMOSTAT_STATUS = "STS"
CMD_GET_DEVICE_STATUS_FAMILY = "WSF"
CMD_SUBSCRIBE_UPDATES_2 = "SU2"
CMD_SUBSCRIBE_UPDATES_3 = "SU3"
CMD_GET_THERMOSTAT_MODE = "GTM"
CMD_GET_MARCIA_ARRESTO = "GMA"
CMD_GET_NO_ACTION = "GNA"
CMD_GET_GLOBAL_SECURITY = "GGS"
CMD_GET_SENSOR_FAMILY = "GSF"
CMD_PONG = "PONG"
CMD_PING = "PING"
CMD_SET_DIMMER_LEVEL = "SIL"

# Device control commands (client -> server)
CMD_LIGHT_COMMAND = "EBI"  # Light/energy on/off/toggle
CMD_SHUTTER_COMMAND = "EAI"  # Shutter open/close
CMD_EXECUTE_SCENARIO = "ES"  # Execute scenario (map command ID)
CMD_EXECUTE_MAP_COMMAND = "EBC"  # Execute generic map command
CMD_THERMOSTAT_SET_OFF = "TOO"  # Set thermostat local off (standard)
CMD_THERMOSTAT_SET_OFF_TS01 = "TUU"  # Set thermostat local off (TS01 type)
CMD_THERMOSTAT_KEYBOARD_LOCK = "TTK"  # Toggle thermostat keyboard lock

# Light sub-command values (used with CMD_LIGHT_COMMAND / EBI)
LIGHT_CMD_TOGGLE = "10"  # Toggle light on/off (step)
LIGHT_CMD_ON = "11"  # Turn light on
LIGHT_CMD_OFF = "12"  # Turn light off

# Dimmer sub-command values (used with CMD_LIGHT_COMMAND / EBI)
# Dimmers use different sub-commands than regular lights
DIMMER_CMD_STEP = "2"  # Dimmer step (toggle)
DIMMER_CMD_ON = "3"  # Turn dimmer on
DIMMER_CMD_OFF = "4"  # Turn dimmer off

# Shutter sub-command values (used with CMD_SHUTTER_COMMAND)
SHUTTER_CMD_OPEN = "8"  # Open/raise shutter
SHUTTER_CMD_CLOSE = "9"  # Close/lower shutter

# UPD event subtypes (server -> client)
UPD_DEVICE_STATUS = "WS"
UPD_THERMOSTAT = "WT"
UPD_THERMOSTAT_SETPOINT = "TP"
UPD_THERMOSTAT_MODE = "TM"
UPD_THERMOSTAT_KEYBOARD_LOCK = "TK"
UPD_THERMOSTAT_WINDOW = "TW"
UPD_THERMOSTAT_LOCAL_OFF_MAP = "TLO"  # Thermostat local off (from map)
UPD_THERMOSTAT_SEASON_MAP = "TS"  # Thermostat season (from map)
UPD_THERMOSTAT_TEMP_MAP = "TT"  # Thermostat temperature (from map)
UPD_THERMOSTAT_OFFSET_MAP = "TO"  # Thermostat offset (from map)
UPD_THERMOSTAT_FANLEVEL_MAP = "TL"  # Thermostat fan level (from map)
UPD_HUMIDITY = "UMI"
UPD_RGB = "RGB"
UPD_TUTONDO = "S"
UPD_VIVALDI = "VI"
UPD_DEVICE_ICON = "D"
UPD_ALARM = "A"
UPD_ANTITHEFT_AREA = "X"
UPD_GROUP_DIMMER = "GRP"
UPD_THERMOSTAT_FUNCTION = "TF"  # Thermostat function/scheduling info
UPD_THERMOSTAT_REQUEST = "TR"  # Thermostat request
UPD_ECONOMIZER = "epv"
UPD_HOTEL = "htl"

# Shutter status values
SHUTTER_STATUS_OPEN = 1
SHUTTER_STATUS_OPENING = 2
SHUTTER_STATUS_CLOSED = 3
SHUTTER_STATUS_CLOSING = 4
SHUTTER_STATUS_STOPPED = 5  # Stopped mid-movement (partially open/closed)

# Thermostat seasons
SEASON_SUMMER = 0
SEASON_WINTER = 1
SEASON_ALL = 2

# Thermostat modes
THERMOSTAT_MODE_AUTO = 0  # Automatic (follows built-in schedule)
THERMOSTAT_MODE_MANUAL = 1  # Manual (user-set temperature)
THERMOSTAT_MODE_ANTIFREEZE = 0x1F  # Antifreeze protection (set by system)

# Connection statuses
CONN_STATUS_OPEN = "OPEN"
CONN_STATUS_CLOSE = "CLOSE"
CONN_STATUS_ERROR = "ERROR"
