import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import usb_host
from esphome.const import CONF_ID, CONF_PORT

DEPENDENCIES = ["usb_host"]

CONF_VID = "vid"
CONF_PID = "pid"
CONF_STATUS_POLLING = "status_polling"

dymo_printserver_ns = cg.esphome_ns.namespace("dymo_printserver")
DymoPrintServer = dymo_printserver_ns.class_(
    "DymoPrintServer", usb_host.USBClient, cg.Component
)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(DymoPrintServer),
        cv.Optional(CONF_PORT, default=9100): cv.port,
        # DYMO LabelWriter 450.
        cv.Optional(CONF_VID, default=0x0922): cv.hex_uint16_t,
        cv.Optional(CONF_PID, default=0x0020): cv.hex_uint16_t,
        # USB Printer Class GET_PORT_STATUS polling (IEEE-1284 status byte).
        cv.Optional(CONF_STATUS_POLLING, default=True): cv.boolean,
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID], config[CONF_VID], config[CONF_PID])
    await cg.register_component(var, config)
    cg.add(var.set_port(config[CONF_PORT]))
    cg.add(var.set_status_polling_enabled(config[CONF_STATUS_POLLING]))
