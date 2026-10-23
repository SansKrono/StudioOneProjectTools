from pathlib import Path
import re

from core.song_archive import read_text_from_archive, replace_member_text
from models.plugin import PluginItem


AUDIO_MIXER_XML = "Devices/audiomixer.xml"

NO_TRACK_NAME = "(no track name)"

# Studio One mixer channel elements that can contain FX inserts, e.g.
#   <AudioTrackChannel ... name="Channel20" label="Guitar DI">
CHANNEL_TAGS = (
    "AudioTrackChannel",
    "AudioOutputChannel",
    "AudioInputChannel",
    "BusChannel",
    "FolderChannel",
    "VCAChannel",
    "FXChannel",
    "InstrumentChannel",
)

_CHANNEL_TAG_RE = re.compile(r"<(/?)(" + "|".join(CHANNEL_TAGS) + r")\b([^>]*)>", re.IGNORECASE)
_BYPASS_ATTR_RE = re.compile(r'\bbypass="[^"]*"')


def extract_attr(tag: str, attr: str) -> str:
    m = re.search(rf'\b{re.escape(attr)}="([^"]*)"', tag)
    return m.group(1) if m else ""


def find_matching_attributes_end(text: str, start: int) -> int:
    pos = start
    depth = 0

    while True:
        next_open = text.find("<Attributes", pos)
        next_close = text.find("</Attributes>", pos)

        if next_close == -1:
            raise ValueError("Could not find matching </Attributes>")

        if next_open != -1 and next_open < next_close:
            depth += 1
            tag_end = text.find(">", next_open)
            if tag_end == -1:
                raise ValueError("Broken <Attributes> tag")
            if text[tag_end - 1] == "/":
                depth -= 1
                if depth == 0:
                    return tag_end + 1
            pos = tag_end + 1
        else:
            depth -= 1
            pos = next_close + len("</Attributes>")
            if depth == 0:
                return pos


def get_plugin_name_from_block(block: str) -> str:
    # Most reliable Studio One plugin display name pattern.
    m = re.search(r'<Attributes\b[^>]*\bx:id="deviceData"[^>]*\bname="([^"]+)"', block)
    if m:
        return m.group(1)

    # Fallback: first non-FX Attributes name.
    for m in re.finditer(r'<Attributes\b[^>]*\bname="([^"]+)"', block):
        name = m.group(1)
        if not name.startswith("FX"):
            return name

    return "UNKNOWN"


class _ChannelTracker:
    """
    Tracks which mixer channel encloses a given offset.

    Offsets must be queried in increasing order; each channel tag is visited once,
    so the whole document is scanned in a single pass.
    """

    def __init__(self, text: str):
        self._tags = _CHANNEL_TAG_RE.finditer(text)
        self._pending = next(self._tags, None)
        self._stack: list[str] = []

    def channel_at(self, offset: int) -> str:
        while self._pending and self._pending.end() <= offset:
            m = self._pending
            if m.group(1) == "/":
                if self._stack:
                    self._stack.pop()
            elif not m.group(0).rstrip().endswith("/>"):
                attrs = m.group(3) or ""
                self._stack.append(extract_attr(attrs, "label") or extract_attr(attrs, "name") or m.group(2))
            self._pending = next(self._tags, None)
        return self._stack[-1] if self._stack else NO_TRACK_NAME


def parse_fx_blocks(audiomixer_xml: str) -> list[dict]:
    fx = []
    pos = 0
    channels = _ChannelTracker(audiomixer_xml)

    while True:
        start = audiomixer_xml.find('<Attributes name="FX', pos)
        if start == -1:
            break

        tag_end = audiomixer_xml.find(">", start)
        if tag_end == -1:
            break

        start_tag = audiomixer_xml[start:tag_end + 1]

        try:
            end = find_matching_attributes_end(audiomixer_xml, start)
        except ValueError:
            break

        block = audiomixer_xml[start:end]
        if block.endswith("/>") and block.count("<") == 1:
            # Empty self-closing slot such as <Attributes name="FX3"/>: no plugin inside.
            pos = end
            continue

        slot = extract_attr(start_tag, "name")
        channel_name = channels.channel_at(start)

        fx.append(
            {
                "slot": slot,
                "channel": channel_name,
                "path": f"{channel_name} / {slot}",
                "plugin": get_plugin_name_from_block(block),
                "bypass": extract_attr(start_tag, "bypass") == "1",
                "start": start,
                "end": end,
                "block": block,
            }
        )

        pos = end

    return fx


def parse_plugins(song_path: Path) -> list[PluginItem]:
    xml = read_text_from_archive(song_path, AUDIO_MIXER_XML)

    return [
        PluginItem(
            index=i,
            track_name=fx["channel"] or NO_TRACK_NAME,
            slot_name=fx["slot"] or "FX",
            plugin_name=fx["plugin"] or "UNKNOWN",
            bypassed=fx["bypass"],
            xml_file=AUDIO_MIXER_XML,
        )
        for i, fx in enumerate(parse_fx_blocks(xml))
    ]


def _set_bypass(block: str, bypassed: bool) -> str:
    tag_end = block.find(">")
    if tag_end == -1:
        return block

    tag, rest = block[:tag_end + 1], block[tag_end + 1:]
    value = "1" if bypassed else "0"

    if _BYPASS_ATTR_RE.search(tag):
        tag = _BYPASS_ATTR_RE.sub(f'bypass="{value}"', tag, count=1)
    elif tag.endswith("/>"):
        tag = tag[:-2].rstrip() + f' bypass="{value}"/>'
    else:
        tag = tag[:-1] + f' bypass="{value}">'

    return tag + rest


def modify_fx_xml_by_indices(audiomixer_xml: str, selected_indices: set[int], action: str) -> tuple[str, list[str]]:
    """
    action:
      - remove: remove selected FX blocks
      - bypass: set bypass="1"
      - unbypass: set bypass="0"
    """
    if action not in {"remove", "bypass", "unbypass"}:
        raise ValueError(f"Unknown FX action: {action}")

    fx_blocks = parse_fx_blocks(audiomixer_xml)
    changed = []
    out = []
    pos = 0

    for index, fx in enumerate(fx_blocks):
        out.append(audiomixer_xml[pos:fx["start"]])

        if index in selected_indices:
            changed.append(fx["plugin"])
            if action != "remove":
                out.append(_set_bypass(fx["block"], action == "bypass"))
        else:
            out.append(fx["block"])

        pos = fx["end"]

    out.append(audiomixer_xml[pos:])
    return "".join(out), changed


def remove_bypassed_fx(song_path: Path, output_path: Path) -> Path:
    xml = read_text_from_archive(song_path, AUDIO_MIXER_XML)
    selected = {i for i, fx in enumerate(parse_fx_blocks(xml)) if fx["bypass"]}
    new_xml, _ = modify_fx_xml_by_indices(xml, selected, "remove")
    return replace_member_text(song_path, output_path, {AUDIO_MIXER_XML: new_xml})


def modify_selected_fx(song_path: Path, output_path: Path, selected_indices: set[int], action: str) -> tuple[Path, list[str]]:
    xml = read_text_from_archive(song_path, AUDIO_MIXER_XML)
    new_xml, changed = modify_fx_xml_by_indices(xml, selected_indices, action)
    return replace_member_text(song_path, output_path, {AUDIO_MIXER_XML: new_xml}), changed
