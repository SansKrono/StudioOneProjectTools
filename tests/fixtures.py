"""Synthetic Studio One projects and minidumps for the tests. No real user data is needed."""

from pathlib import Path
import random
import struct
import zipfile


AUDIOMIXER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<AudioMixer xmlns:x="x">
 <AudioTrackChannel name="Channel1" label="Guitar DI">
  <Attributes name="Inserts">
   <Attributes name="FX0" bypass="1"><Attributes x:id="deviceData" name="Pro-Q 3"/></Attributes>
   <Attributes name="FX1"/>
   <Attributes name="FX2"><Attributes x:id="deviceData" name="Archetype Gojira"/></Attributes>
  </Attributes>
 </AudioTrackChannel>
 <BusChannel name="Bus1" label="Drums Bus">
  <Attributes name="Inserts">
   <Attributes name="FX0" bypass="0"><Attributes x:id="deviceData" name="Pro-C 2"/></Attributes>
  </Attributes>
 </BusChannel>
 <AudioOutputChannel label="Main">
  <Attributes name="Inserts">
   <Attributes name="FX0" bypass="1"><Attributes name="Limiter"/></Attributes>
  </Attributes>
 </AudioOutputChannel>
</AudioMixer>
"""


def _wav(p: Path, payload: bytes):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"RIFF" + payload)


def make_project(root: Path) -> dict:
    """
    Build a project folder with referenced, external, missing, unused and duplicate audio.
    Returns a dict of useful paths.
    """
    proj = root / "MySong"
    ext = root / "ExternalDrive"
    found = root / "Found"

    _wav(proj / "Media" / "kick.wav", b"K" * 5000)
    _wav(proj / "Media" / "snare.wav", b"S" * 6000)
    _wav(proj / "Media" / "pad.aiff", b"P" * 4500)
    _wav(proj / "Bounces" / "mix.wav", b"M" * 7000)
    _wav(proj / "Media" / "unused take.wav", b"U" * 3000)
    _wav(proj / "Media" / "kick_copy.wav", b"K" * 5000)      # unreferenced, duplicate of kick.wav
    _wav(proj / "Media" / "Sub" / "snare.wav", b"S" * 6000)  # same-name side file, duplicate content
    _wav(ext / "vox.wav", b"V" * 8000)                        # referenced from outside the project
    _wav(found / "lost guitar.wav", b"G" * 4000)              # the "missing" file, found elsewhere

    mediapool = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<MediaPool>\n'
        ' <AudioClip file="Media\\kick.wav"/>\n'
        ' <AudioClip file="Media\\snare.wav"/>\n'
        ' <AudioClip file="Media\\pad.aiff"/>\n'
        ' <AudioClip file="Bounces\\mix.wav"/>\n'
        f' <AudioClip file="{ext}\\vox.wav"/>\n'
        ' <AudioClip file="Old Takes\\lost guitar.wav"/>\n'
        '</MediaPool>\n'
    )

    song = proj / "MySong.song"
    with zipfile.ZipFile(song, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Devices/audiomixer.xml", AUDIOMIXER_XML)
        z.writestr("Song/mediapool.xml", mediapool)
        z.writestr("metainfo.xml", "<MetaInfo/>")
        z.writestr("binary.bin", b"\x00\x01\x02")

    return {"project": proj, "song": song, "external": ext, "found": found / "lost guitar.wav"}


VST3 = r"C:\Program Files\Common Files\VST3"
PRO_Q_BASE = 0x180000000
GOJIRA_BASE = 0x190000000
KERNELBASE = 0x7FF800000000


def make_minidump(path: Path, n_threads: int = 12, stack_kb: int = 64, filler_modules: int = 50) -> int:
    """
    Write a minimal x64 minidump: an access violation in KERNELBASE, with the
    exception thread's stack pointing into "Archetype Gojira" and other threads
    pointing into "FabFilter Pro-Q 3". The exception thread is listed LAST so
    that any global truncation of stack hits would hide it.
    Returns the exception thread id.
    """
    mods = [
        (KERNELBASE, 0x100000, r"C:\Windows\System32\KERNELBASE.dll"),
        (0x7FF700000000, 0x2000000, r"C:\Program Files\PreSonus\Studio One 6\Studio One.exe"),
        (PRO_Q_BASE, 0x400000, VST3 + r"\FabFilter Pro-Q 3.vst3\Contents\x86_64-win\FabFilter Pro-Q 3.vst3"),
        (GOJIRA_BASE, 0x400000, VST3 + r"\Archetype Gojira.vst3\Contents\x86_64-win\Archetype Gojira.vst3"),
    ]
    mods += [(0x200000000 + i * 0x1000000, 0x800000, rf"C:\Windows\System32\filler{i}.dll") for i in range(filler_modules)]

    header_size, dir_slots = 32, 4
    blob = bytearray()
    base_off = header_size + 12 * dir_slots
    streams = []

    def add(b: bytes) -> int:
        off = base_off + len(blob)
        blob.extend(b)
        return off

    name_rvas = []
    for _, _, name in mods:
        enc = name.encode("utf-16-le")
        name_rvas.append(add(struct.pack("<I", len(enc)) + enc + b"\0\0"))
    ml = bytearray(struct.pack("<I", len(mods)))
    for (b, s, _), nr in zip(mods, name_rvas):
        ml += struct.pack("<QIIII", b, s, 0, 0, nr) + b"\0" * (108 - 24)
    streams.append((4, len(ml), add(bytes(ml))))

    rnd = random.Random(1)
    exc_tid = 1000 + n_threads - 1
    stacks = []
    for t in range(n_threads):
        words = []
        for k in range(stack_kb * 1024 // 8):
            r = rnd.random()
            if t == n_threads - 1 and r < 0.01:
                words.append(GOJIRA_BASE + 0x1234 + (k % 50) * 16)
            elif t != n_threads - 1 and r < 0.02:
                words.append(PRO_Q_BASE + 0x500 + (k % 300) * 16)
            elif r < 0.03:
                words.append(KERNELBASE + 0x100)
            else:
                words.append(rnd.getrandbits(32))
        data = struct.pack(f"<{len(words)}Q", *words)
        stacks.append((0x10000000 + t * 0x100000, len(data), add(data)))
    tl = bytearray(struct.pack("<I", n_threads))
    for t, (start, size, rva) in enumerate(stacks):
        tl += struct.pack("<IIIIQ", 1000 + t, 0, 0, 0, 0) + struct.pack("<QII", start, size, rva) + struct.pack("<II", 0, 0)
    streams.append((3, len(tl), add(bytes(tl))))

    ex = struct.pack("<II", exc_tid, 0) + struct.pack("<IIQQII", 0xC0000005, 0, 0, KERNELBASE + 0x2000, 0, 0)
    ex += b"\0" * (15 * 8) + struct.pack("<II", 0, 0)
    streams.append((6, len(ex), add(ex)))
    si = struct.pack("<H", 9) + b"\0" * 46
    streams.append((7, len(si), add(si)))

    hdr = struct.pack("<4sIIIIIQ", b"MDMP", 0xA793, len(streams), header_size, 0, 0, 0)
    dirs = b"".join(struct.pack("<III", t, s, r) for t, s, r in streams).ljust(12 * dir_slots, b"\0")
    path.write_bytes(hdr + dirs + bytes(blob))
    return exc_tid


class FakePlugin:
    def __init__(self, name, track="T", slot="FX0", bypassed=False):
        self.plugin_name, self.track_name, self.slot_name, self.bypassed = name, track, slot, bypassed
