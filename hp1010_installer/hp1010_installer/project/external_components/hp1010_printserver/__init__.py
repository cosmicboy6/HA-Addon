import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import usb_host
from esphome.const import CONF_ID, CONF_PORT

DEPENDENCIES = ["usb_host"]

CONF_VID = "vid"
CONF_PID = "pid"
CONF_PAGE_COUNT = "page_count"
CONF_STATUS_POLLING = "status_polling"

hp1010_printserver_ns = cg.esphome_ns.namespace("hp1010_printserver")
HP1010PrintServer = hp1010_printserver_ns.class_(
    "HP1010PrintServer", usb_host.USBClient, cg.Component
)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(HP1010PrintServer),
        cv.Optional(CONF_PORT, default=9100): cv.port,
        cv.Optional(CONF_VID, default=0x03F0): cv.hex_uint16_t,
        cv.Optional(CONF_PID, default=0x0C17): cv.hex_uint16_t,
        # HP LaserJet 1010 speaks PJL; most other printers (e.g. the DYMO
        # LabelWriter 450) do not, and sending the query would inject control
        # bytes into the print stream.
        cv.Optional(CONF_PAGE_COUNT, default=True): cv.boolean,
        # USB Printer Class GET_PORT_STATUS polling (IEEE-1284 status byte).
        cv.Optional(CONF_STATUS_POLLING, default=True): cv.boolean,
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID], config[CONF_VID], config[CONF_PID])
    await cg.register_component(var, config)
    cg.add(var.set_port(config[CONF_PORT]))
    cg.add(var.set_page_count_enabled(config[CONF_PAGE_COUNT]))
    cg.add(var.set_status_polling_enabled(config[CONF_STATUS_POLLING]))
