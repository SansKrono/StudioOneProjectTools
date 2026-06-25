from dataclasses import dataclass


@dataclass
class PluginItem:
    index: int
    track_name: str
    slot_name: str
    plugin_name: str
    bypassed: bool
    xml_file: str = "Devices/audiomixer.xml"
