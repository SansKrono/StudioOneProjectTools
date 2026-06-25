from pathlib import Path
import re
from .song_archive import read_text_from_archive, write_modified_song_copy
from models.plugin import PluginItem


AUDIO_MIXER_XML = "Devices/audiomixer.xml"


def extract_attr(tag: str, attr: str) -> str:
    m = re.search(rf'{re.escape(attr)}="([^"]*)"', tag)
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
            if tag_end > next_open and text[tag_end - 1] == "/":
                depth -= 1
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


def find_parent_channel_name(text: str, fx_start: int) -> str:
    """
    Best-effort parent channel detection for Studio One audiomixer.xml.
    FX blocks are usually inside tags like:
      <AudioTrackChannel ... name="Channel20" label="Guitar DI">
      <AudioOutputChannel ... label="Main">
      <BusChannel ... label="Bus 1">
    """
    prefix = text[:fx_start]

    channel_tags = [
        "AudioTrackChannel",
        "AudioOutputChannel",
        "AudioInputChannel",
        "BusChannel",
        "FolderChannel",
        "VCAChannel",
        "FXChannel",
        "InstrumentChannel",
    ]

    tag_pattern = re.compile(
        r'<(/?)(' + "|".join(channel_tags) + r')\b([^>]*)>',
        flags=re.IGNORECASE,
    )

    stack = []

    for match in tag_pattern.finditer(prefix):
        is_closing = match.group(1) == "/"
        tag_name = match.group(2)
        attrs = match.group(3) or ""
        full_tag = match.group(0)
        self_closing = full_tag.rstrip().endswith("/>")

        if is_closing:
            if stack:
                stack.pop()
            continue

        label = extract_attr(attrs, "label")
        name = extract_attr(attrs, "name")
        display = label or name or tag_name

        if not self_closing:
            stack.append(display)

    return stack[-1] if stack else "(no track name)"


def parse_fx_blocks(audiomixer_xml: str) -> list[dict]:
    fx = []
    pos = 0

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
        slot = extract_attr(start_tag, "name")
        bypass = extract_attr(start_tag, "bypass") == "1"
        plugin_name = get_plugin_name_from_block(block)
        channel_name = find_parent_channel_name(audiomixer_xml, start)
        path = f"{channel_name} / {slot}" if channel_name else slot

        fx.append(
            {
                "slot": slot,
                "channel": channel_name,
                "path": path,
                "plugin": plugin_name,
                "bypass": bypass,
                "start": start,
                "end": end,
                "block": block,
            }
        )

        pos = end

    return fx


def parse_plugins(song_path: Path) -> list[PluginItem]:
    xml = read_text_from_archive(song_path, AUDIO_MIXER_XML)
    fx_blocks = parse_fx_blocks(xml)

    return [
        PluginItem(
            index=i,
            track_name=fx.get("channel") or "(no track name)",
            slot_name=fx.get("slot") or "FX",
            plugin_name=fx.get("plugin") or "UNKNOWN",
            bypassed=bool(fx.get("bypass")),
            xml_file=AUDIO_MIXER_XML,
        )
        for i, fx in enumerate(fx_blocks)
    ]


def count_bypassed_fx(song_path: Path) -> int:
    return sum(1 for p in parse_plugins(song_path) if p.bypassed)


def modify_fx_xml_by_indices(audiomixer_xml: str, selected_indices: set[int], action: str) -> tuple[str, list[str]]:
    """
    action:
      - remove: remove selected FX blocks
      - bypass: set bypass="1"
      - unbypass: set bypass="0"
    """
    fx_blocks = parse_fx_blocks(audiomixer_xml)
    if not fx_blocks:
        return audiomixer_xml, []

    changed = []
    out = []
    pos = 0

    for index, fx in enumerate(fx_blocks):
        out.append(audiomixer_xml[pos:fx["start"]])

        if index in selected_indices:
            changed.append(fx["plugin"])

            if action == "remove":
                # Drop whole FX block.
                pass

            elif action in {"bypass", "unbypass"}:
                new_block = fx["block"]
                tag_end = new_block.find(">")

                if tag_end == -1:
                    out.append(new_block)
                else:
                    tag = new_block[:tag_end + 1]
                    rest = new_block[tag_end + 1:]
                    new_value = "1" if action == "bypass" else "0"

                    if re.search(r'\bbypass="[^"]*"', tag):
                        tag = re.sub(r'\bbypass="[^"]*"', f'bypass="{new_value}"', tag, count=1)
                    else:
                        tag = tag[:-1] + f' bypass="{new_value}">'

                    out.append(tag + rest)
            else:
                out.append(fx["block"])
        else:
            out.append(fx["block"])

        pos = fx["end"]

    out.append(audiomixer_xml[pos:])
    return "".join(out), changed


def remove_bypassed_fx(song_path: Path, output_path: Path) -> Path:
    xml = read_text_from_archive(song_path, AUDIO_MIXER_XML)
    fx_blocks = parse_fx_blocks(xml)
    selected = {i for i, fx in enumerate(fx_blocks) if fx.get("bypass")}
    new_xml, _ = modify_fx_xml_by_indices(xml, selected, "remove")

    return write_modified_song_copy(
        song_path=song_path,
        output_path=output_path,
        replacements={AUDIO_MIXER_XML: new_xml},
    )


def modify_selected_fx(song_path: Path, output_path: Path, selected_indices: set[int], action: str) -> tuple[Path, list[str]]:
    xml = read_text_from_archive(song_path, AUDIO_MIXER_XML)
    new_xml, changed = modify_fx_xml_by_indices(xml, selected_indices, action)

    result = write_modified_song_copy(
        song_path=song_path,
        output_path=output_path,
        replacements={AUDIO_MIXER_XML: new_xml},
    )

    return result, changed
