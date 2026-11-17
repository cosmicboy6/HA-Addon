import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import usb_host
from esphome.const import CONF_ID, CONF_PORT

DEPENDENCIES = ["usb_host"]

CONF_VID = "vid"
CONF_PID = "pid"

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
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID], config[CONF_VID], config[CONF_PID])
    await cg.register_component(var, config)
    cg.add(var.set_port(config[CONF_PORT]))
