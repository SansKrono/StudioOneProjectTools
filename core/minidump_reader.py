"""
Minimal, read-only Windows minidump (.dmp/.mdmp) parser.

Reads the exception, module list, thread stacks, system info and misc info
streams directly from the file. No debugger, symbols or network access is used.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import mmap
import re
import struct
import sys


THREAD_LIST_STREAM = 3
MODULE_LIST_STREAM = 4
EXCEPTION_STREAM = 6
SYSTEM_INFO_STREAM = 7
MISC_INFO_STREAM = 15

MODULE_ENTRY_SIZE = 108  # MINIDUMP_MODULE
THREAD_ENTRY_SIZE = 48   # MINIDUMP_THREAD

# Max distinct (module, offset) stack hits kept per thread.
MAX_HITS_PER_THREAD = 1000

EXCEPTION_NAMES = {
    0x80000003: "BREAKPOINT",
    0xC0000005: "ACCESS_VIOLATION",
    0xC000001D: "ILLEGAL_INSTRUCTION",
    0xC000008C: "ARRAY_BOUNDS_EXCEEDED",
    0xC000008D: "FLOAT_DENORMAL_OPERAND",
    0xC000008E: "FLOAT_DIVIDE_BY_ZERO",
    0xC000008F: "FLOAT_INEXACT_RESULT",
    0xC0000090: "FLOAT_INVALID_OPERATION",
    0xC0000091: "FLOAT_OVERFLOW",
    0xC0000092: "FLOAT_STACK_CHECK",
    0xC0000093: "FLOAT_UNDERFLOW",
    0xC0000094: "INTEGER_DIVIDE_BY_ZERO",
    0xC0000095: "INTEGER_OVERFLOW",
    0xC00000FD: "STACK_OVERFLOW",
    0xC0000135: "DLL_NOT_FOUND",
    0xC0000139: "ENTRYPOINT_NOT_FOUND",
    0xC0000409: "STACK_BUFFER_OVERRUN / FAST_FAIL",
    0xE06D7363: "CPP_EXCEPTION",
}


@dataclass
class MiniModule:
    base: int
    size: int
    checksum: int = 0
    timestamp: int = 0
    name: str = ""

    @property
    def end(self) -> int:
        return self.base + self.size

    def contains(self, address: int) -> bool:
        return self.base <= address < self.end


@dataclass
class MiniException:
    thread_id: int = 0
    code: int = 0
    flags: int = 0
    record: int = 0
    address: int = 0
    parameters: list[int] = field(default_factory=list)

    @property
    def code_name(self) -> str:
        return EXCEPTION_NAMES.get(self.code, "UNKNOWN_EXCEPTION")


@dataclass
class StackModuleHit:
    thread_id: int
    stack_offset: int
    address: int
    module: MiniModule
    module_offset: int


@dataclass
class MiniDumpReport:
    path: Path
    valid: bool = False
    error: str = ""
    processor_arch: str = ""
    process_id: int = 0
    exception: MiniException | None = None
    modules: list[MiniModule] = field(default_factory=list)
    faulting_module: MiniModule | None = None
    fault_module_offset: int | None = None
    exception_thread_stack_hits: list[StackModuleHit] = field(default_factory=list)
    all_stack_hits: list[StackModuleHit] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Low-level stream parsing
# ---------------------------------------------------------------------------

def _u32(data, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _u64(data, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def _read_utf16_string(data, rva: int) -> str:
    if not rva or rva + 4 > len(data):
        return ""
    byte_len = _u32(data, rva)
    start = rva + 4
    end = min(start + byte_len, len(data))
    return bytes(data[start:end]).decode("utf-16-le", errors="replace").rstrip("\x00")


def _stream_dirs(data) -> dict[int, tuple[int, int]]:
    if len(data) < 32:
        raise ValueError("File is too small for MINIDUMP_HEADER")
    if data[:4] != b"MDMP":
        raise ValueError("Not a minidump: missing MDMP signature")

    stream_count = _u32(data, 8)
    dir_rva = _u32(data, 12)
    if dir_rva <= 0 or dir_rva + stream_count * 12 > len(data):
        raise ValueError("Invalid minidump stream directory")

    streams: dict[int, tuple[int, int]] = {}
    for i in range(stream_count):
        off = dir_rva + i * 12
        stream_type, size, rva = struct.unpack_from("<III", data, off)
        if rva and size and rva + size <= len(data):
            streams[stream_type] = (rva, size)
    return streams


def _parse_system_info(data, streams) -> str:
    loc = streams.get(SYSTEM_INFO_STREAM)
    if not loc or loc[1] < 2:
        return ""
    arch = struct.unpack_from("<H", data, loc[0])[0]
    return {0: "x86", 5: "ARM", 6: "IA64", 9: "x64 / AMD64", 12: "ARM64"}.get(arch, f"unknown ({arch})")


def _parse_misc_info(data, streams) -> int:
    loc = streams.get(MISC_INFO_STREAM)
    if not loc or loc[1] < 12:
        return 0
    rva = loc[0]
    flags = _u32(data, rva + 4)
    return _u32(data, rva + 8) if flags & 0x1 else 0


def _parse_exception(data, streams) -> MiniException | None:
    loc = streams.get(EXCEPTION_STREAM)
    if not loc or loc[1] < 8 + 32:
        return None
    rva = loc[0]
    thread_id = _u32(data, rva)
    ex = rva + 8
    code, flags = _u32(data, ex), _u32(data, ex + 4)
    record, address = _u64(data, ex + 8), _u64(data, ex + 16)
    nparams = min(_u32(data, ex + 24), 15)
    params = [_u64(data, ex + 32 + i * 8) for i in range(nparams) if ex + 40 + i * 8 <= len(data)]
    return MiniException(thread_id, code, flags, record, address, params)


def _parse_modules(data, streams) -> list[MiniModule]:
    loc = streams.get(MODULE_LIST_STREAM)
    if not loc or loc[1] < 4:
        return []

    modules: list[MiniModule] = []
    off = loc[0] + 4
    for _ in range(_u32(data, loc[0])):
        if off + MODULE_ENTRY_SIZE > len(data):
            break
        base, img_size, checksum, timestamp, name_rva = struct.unpack_from("<QIIII", data, off)
        modules.append(MiniModule(base, img_size, checksum, timestamp, _read_utf16_string(data, name_rva)))
        off += MODULE_ENTRY_SIZE

    modules.sort(key=lambda m: m.base)
    return modules


def _iter_thread_stacks(data, streams):
    """Yield (thread_id, stack bytes) for each thread with captured stack memory."""
    loc = streams.get(THREAD_LIST_STREAM)
    if not loc or loc[1] < 4:
        return

    off = loc[0] + 4
    for _ in range(_u32(data, loc[0])):
        if off + THREAD_ENTRY_SIZE > len(data):
            break
        # MINIDUMP_THREAD: ThreadId, SuspendCount, PriorityClass, Priority (4x u32), Teb (u64),
        # then Stack = MINIDUMP_MEMORY_DESCRIPTOR { StartOfMemoryRange u64, DataSize u32, Rva u32 }.
        thread_id = _u32(data, off)
        stack_start, stack_size, stack_rva = struct.unpack_from("<QII", data, off + 24)
        if stack_start and stack_size and stack_rva and stack_rva + stack_size <= len(data):
            yield thread_id, data[stack_rva:stack_rva + stack_size]
        off += THREAD_ENTRY_SIZE


# ---------------------------------------------------------------------------
# Stack scanning
# ---------------------------------------------------------------------------

class _ModuleIndex:
    """Address -> module lookup via binary search over sorted module bases."""

    def __init__(self, modules: list[MiniModule]):
        self.modules = [m for m in modules if m.size]
        self.bases = [m.base for m in self.modules]
        self.lo = self.bases[0] if self.bases else 0
        self.hi = max((m.end for m in self.modules), default=0)

    def find(self, address: int) -> MiniModule | None:
        if not (self.lo <= address < self.hi):
            return None
        i = bisect_right(self.bases, address) - 1
        if i >= 0 and self.modules[i].contains(address):
            return self.modules[i]
        return None


def _scan_stack(thread_id: int, stack: bytes, index: _ModuleIndex) -> list[StackModuleHit]:
    """
    Heuristic stack scanner.

    Every aligned 8-byte value in a saved thread stack that points inside a loaded
    module is treated as a possible return address. This is less precise than a
    symbolic call stack, but it can reveal plugin/DLL involvement when the
    exception address itself is only in kernelbase/ucrtbase.
    Distinct (module, offset) pairs are kept, up to MAX_HITS_PER_THREAD per thread.
    """
    usable = len(stack) - len(stack) % 8
    if usable <= 0:
        return []

    lo, hi = max(index.lo, 0x10000), min(index.hi, 0x00007FFFFFFFFFFF + 1)
    hits: list[StackModuleHit] = []
    seen: set[tuple[str, int]] = set()

    if sys.byteorder == "little":
        values = memoryview(stack)[:usable].cast("Q")
    else:
        values = (v for (v,) in struct.iter_unpack("<Q", stack[:usable]))

    for slot, value in enumerate(values):
        if value < lo or value >= hi:
            continue
        mod = index.find(value)
        if mod is None:
            continue
        key = (mod.name.lower(), value - mod.base)
        if key in seen:
            continue
        seen.add(key)
        hits.append(StackModuleHit(thread_id, slot * 8, value, mod, value - mod.base))
        if len(hits) >= MAX_HITS_PER_THREAD:
            break

    return hits


def _rank_modules_from_stack_hits(hits: list[StackModuleHit]) -> list[tuple[MiniModule, int]]:
    counts: dict[str, list] = {}
    for h in hits:
        entry = counts.setdefault(h.module.name.lower(), [h.module, 0])
        entry[1] += 1
    return sorted(((m, c) for m, c in counts.values()), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# Module classification
# ---------------------------------------------------------------------------

_RUNTIME_EXACT = {
    "kernelbase.dll", "kernel32.dll", "ntdll.dll", "user32.dll", "win32u.dll",
    "gdi32.dll", "gdi32full.dll", "combase.dll", "rpcrt4.dll", "ole32.dll",
    "shell32.dll", "advapi32.dll", "sechost.dll", "bcrypt.dll", "bcryptprimitives.dll",
    "ws2_32.dll", "winmm.dll", "mmdevapi.dll", "cfgmgr32.dll", "setupapi.dll",
}
_RUNTIME_PREFIXES = ("ucrt", "vcruntime", "msvcp", "concrt")
_PLUGIN_PATH_MARKERS = (
    "\\common files\\vst3\\", "\\vstplugins\\", "\\steinberg\\vstplugins\\", "\\common files\\clap\\",
    "\\audio\\plug-ins\\", "\\plugins\\vst\\", "\\plugins\\vst3\\",
    ".vst3\\", ".vst\\", ".clap\\", ".aaxplugin\\", ".component\\",
)
_LICENSING_MARKERS = ("pace", "ilok", "license", "licence", "activation", "codemeter", "wibu")
_GRAPHICS_MARKERS = ("nvoglv", "nvwgf", "nvcuda", "atidxx", "amdxx", "d3d", "dxgi", "vulkan", "opengl", "igc", "igd")


def normalize_module_path(name: str) -> str:
    return (name or "").replace("/", "\\").lower()


@lru_cache(maxsize=4096)
def classify_module(name: str) -> str:
    """
    Generic classification only, no vendor guesses:
    system/runtime, system, host, plugin-path, audio-driver, licensing, graphics, non-system, unknown.
    """
    n = normalize_module_path(name)
    if not n:
        return "unknown"

    base = n.rsplit("\\", 1)[-1]

    if "\\windows\\system32\\" in n or "\\windows\\syswow64\\" in n:
        if base in _RUNTIME_EXACT or base.startswith(_RUNTIME_PREFIXES):
            return "system/runtime"
        return "system"

    if base in {"studio one.exe", "studio pro.exe", "studioone.exe"} or "studio one" in base or "studio pro" in base:
        return "host"
    if any(x in n for x in _PLUGIN_PATH_MARKERS):
        return "plugin-path"
    if "asio" in base or "\\asio\\" in n or "wasapi" in base:
        return "audio-driver"
    if any(x in n for x in _LICENSING_MARKERS):
        return "licensing"
    if any(x in base for x in _GRAPHICS_MARKERS):
        return "graphics"
    return "non-system"


def is_system_or_runtime_module(name: str) -> bool:
    return classify_module(name) in {"system/runtime", "system"}


def is_plugin_path_module(name: str) -> bool:
    return classify_module(name) == "plugin-path"


def _format_module_line(m: MiniModule) -> str:
    return f"[{classify_module(m.name)}] 0x{m.base:016X}-0x{m.end:016X}  {m.name}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def analyze_minidump(path: str | Path) -> MiniDumpReport:
    path = Path(path)
    report = MiniDumpReport(path=path)
    try:
        with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            streams = _stream_dirs(data)
            report.valid = True
            report.processor_arch = _parse_system_info(data, streams)
            report.process_id = _parse_misc_info(data, streams)
            report.exception = _parse_exception(data, streams)
            report.modules = _parse_modules(data, streams)

            index = _ModuleIndex(report.modules)
            if report.exception:
                report.faulting_module = index.find(report.exception.address)
                if report.faulting_module:
                    report.fault_module_offset = report.exception.address - report.faulting_module.base

            for thread_id, stack in _iter_thread_stacks(data, streams):
                report.all_stack_hits.extend(_scan_stack(thread_id, stack, index))
    except (OSError, ValueError, struct.error) as e:
        report.error = str(e)

    if report.exception:
        tid = report.exception.thread_id
        report.exception_thread_stack_hits = [h for h in report.all_stack_hits if h.thread_id == tid]

    return report


# ---------------------------------------------------------------------------
# Matching opened .song FX names against dump modules
# ---------------------------------------------------------------------------

_NOISE_TOKENS = {
    "vst3", "vst", "clap", "aax", "aaxplugin", "dll", "exe", "x64", "x86", "64", "86", "win", "win64", "win32",
    "plugin", "plugins", "common", "files", "program", "data", "steinberg", "contents", "mono", "stereo",
}


def _tokenize_for_match(text: str) -> set[str]:
    text = re.sub(r"^[a-z]:", " ", (text or "").lower())  # drop the drive letter
    return {t for t in re.split(r"[^a-z0-9]+", text) if t and t not in _NOISE_TOKENS}


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _project_fx_match_score(project_name: str, module_name: str) -> tuple[int, list[str]]:
    """
    Score how well a project FX name matches a module path.

    A match needs most (>= 75%) of the FX name's tokens to appear in the module
    path, including at least one distinctive token of 3+ characters. This keeps
    e.g. "Pro-C 2" from matching "Pro-Q 3.vst3" just because both contain "pro".
    """
    p_tokens = _tokenize_for_match(project_name)
    m_tokens = _tokenize_for_match(module_name)
    if not p_tokens or not m_tokens:
        return 0, []

    common = sorted(p_tokens & m_tokens)
    coverage = len(common) / len(p_tokens)

    if coverage < 0.75 or not any(len(t) >= 3 for t in common):
        # Fallback for compact plugin names such as "ProQ3" vs "Pro-Q 3.vst3".
        p = _compact(project_name)
        base = module_name.replace("/", "\\").rsplit("\\", 1)[-1]
        if len(p) >= 4 and p in _compact(base):
            return 100, [project_name]
        return 0, []

    score = len(common) * 20 + (50 if coverage >= 0.75 else 25)
    if is_plugin_path_module(module_name):
        score += 35
    return score, common


def _collect_project_matches(
    project_names: list[str],
    ranked_modules: list[tuple[MiniModule, int]],
    loaded_modules: list[MiniModule] | None = None,
    *,
    include_loaded_only: bool = False,
    min_score: int = 120,
) -> list[dict]:
    """
    Match opened .song FX names against dump evidence.

    Loaded modules alone are NOT evidence of crash involvement (Studio One loads
    every plugin in the project), so by default only stack hits are used.
    """
    if not project_names:
        return []

    evidence: list[tuple[str, MiniModule, int]] = [("stack", m, c) for m, c in ranked_modules if c > 0]
    if include_loaded_only and loaded_modules:
        existing = {m.name.lower() for _, m, _ in evidence}
        evidence += [("loaded", m, 0) for m in loaded_modules if m.name.lower() not in existing]

    matches = []
    for fx in project_names:
        best = None
        for source, mod, hits in evidence:
            score, tokens = _project_fx_match_score(fx, mod.name)
            if score <= 0:
                continue
            # Loaded-only presence is weak and must never dominate.
            score += 100 + min(hits, 75) if source == "stack" else 5
            if score < min_score:
                continue
            if best is None or score > best["score"]:
                best = {"fx": fx, "module": mod, "source": source, "hits": hits, "score": score, "tokens": tokens}
        if best:
            matches.append(best)

    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches


def _format_project_match(item: dict) -> str:
    mod = item["module"]
    tokens = ", ".join(item["tokens"][:8]) if item.get("tokens") else ""
    return (
        f"{item['fx']}  ->  {mod.name}  "
        f"[source={item['source']}, hits={item['hits']}, score={item['score']}, category={classify_module(mod.name)}"
        + (f", tokens={tokens}" if tokens else "")
        + "]"
    )


_CATEGORY_BASE_RANK = {
    "plugin-path": 90, "audio-driver": 85, "licensing": 80, "graphics": 70,
    "host": 55, "non-system": 50, "unknown": 30, "system/runtime": 5, "system": 5,
}


def module_rank(module: MiniModule, hits: int, *, exception_thread: bool = False) -> int:
    """Generic suspicion rank for any module: category + exception-thread bonus + capped hit count."""
    rank = _CATEGORY_BASE_RANK.get(classify_module(module.name), 0)
    if exception_thread:
        rank += 60
    return rank + min(max(hits, 0), 100)


def _ranked_with_suspicion(ranked, *, exception_thread: bool = False) -> list[tuple[MiniModule, int, int]]:
    out = [(m, hits, module_rank(m, hits, exception_thread=exception_thread))
           for m, hits in ranked if not is_system_or_runtime_module(m.name)]
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def format_minidump_report(report: MiniDumpReport, plugin_names: list[str] | None = None) -> str:
    lines = [
        "Studio One Project Tools - Project Crash Match Report",
        "=" * 80,
        f"File: {report.path}",
        f"Valid minidump: {'yes' if report.valid else 'no'}",
    ]
    if report.error:
        lines.append(f"Error: {report.error}")
        return "\n".join(lines)

    lines.append(f"Processor: {report.processor_arch or 'unknown'}")
    if report.process_id:
        lines.append(f"Process ID: {report.process_id}")
    lines += ["", "Exception", "-" * 80]
    if report.exception:
        e = report.exception
        lines += [f"Thread ID: 0x{e.thread_id:X} ({e.thread_id})", f"Code:      0x{e.code:08X} {e.code_name}",
                  f"Address:   0x{e.address:016X}"]
    else:
        lines.append("No ExceptionStream found in this dump.")
    lines += ["", "Fault surface module", "-" * 80]
    if report.faulting_module:
        m = report.faulting_module
        lines.append(_format_module_line(m))
        if report.fault_module_offset is not None:
            lines.append(f"Offset: +0x{report.fault_module_offset:X}")
        if is_system_or_runtime_module(m.name):
            lines.append("Note: system/runtime surface module; the real caller can be lower in the stack.")
    else:
        lines.append("Not resolved by exception address.")
    lines.append("")

    def non_system(hits):
        return [(m, c) for m, c in _rank_modules_from_stack_hits(hits) if not is_system_or_runtime_module(m.name)]

    exception_non_system = non_system(report.exception_thread_stack_hits)
    exception_plugin = [(m, c) for m, c in exception_non_system if is_plugin_path_module(m.name)]
    all_non_system = non_system(report.all_stack_hits)
    all_plugin = [(m, c) for m, c in all_non_system if is_plugin_path_module(m.name)]

    plugin_names = plugin_names or []
    matches_exception = _collect_project_matches(plugin_names, exception_non_system)
    matches_all = _collect_project_matches(plugin_names, all_non_system)
    loaded_only = _collect_project_matches(plugin_names, [], report.modules, include_loaded_only=True, min_score=1)

    def append_matches(title, matches, limit, empty, no_project):
        lines.extend([title, "-" * 80])
        if not plugin_names:
            lines.append(no_project)
        elif matches:
            lines.extend(f"{i:02}. {_format_project_match(item)}" for i, item in enumerate(matches[:limit], 1))
            if len(matches) > limit:
                lines.append(f"... {len(matches) - limit} more hidden")
        else:
            lines.append(empty)
        lines.append("")

    append_matches("Project FX matched in EXCEPTION THREAD stack (strong evidence)", matches_exception, 25,
                   "No opened .song FX matched inside exception-thread stack modules.",
                   "Project context not loaded. Open a .song first to match dump modules against project FX.")
    append_matches("Project FX matched in ALL THREAD stacks (supporting evidence)", matches_all, 35,
                   "No opened .song FX matched in all-thread stack modules.", "Project context not loaded.")

    lines += ["Loaded project FX present in dump", "-" * 80]
    if plugin_names:
        lines.append("Informational only: loaded module presence is NOT crash evidence.")
        if loaded_only:
            lines.append(f"Loaded project FX/module matches: {len(loaded_only)}")
            lines.append("Hidden from the main suspect list to avoid matching the whole project.")
        else:
            lines.append("No loaded project FX/module name matches.")
    else:
        lines.append("Project context not loaded.")
    lines.append("")

    def append_ranked(title, ranked, limit, empty, *, exception_thread=False):
        lines.extend([title, "-" * 80])
        ranked3 = _ranked_with_suspicion(ranked, exception_thread=exception_thread)
        if ranked3:
            lines.extend(f"{i:02}. rank={rank:3} hits={count:3}  {_format_module_line(m)}"
                         for i, (m, count, rank) in enumerate(ranked3[:limit], 1))
            if len(ranked3) > limit:
                lines.append(f"... {len(ranked3) - limit} more hidden")
        else:
            lines.append(empty)
        lines.append("")

    append_ranked("Top Exception Thread Non-System Modules", exception_non_system, 20,
                  "No non-system modules found in exception thread stack.", exception_thread=True)
    append_ranked("Top Exception Thread Plugin-Path Modules", exception_plugin, 15,
                  "No plugin-path modules found in exception thread stack.", exception_thread=True)
    append_ranked("Top All-Thread Non-System Modules", all_non_system, 25,
                  "No non-system modules found across saved thread stacks.")
    append_ranked("Top All-Thread Plugin-Path Modules", all_plugin, 20,
                  "No plugin-path modules found across saved thread stacks.")

    lines += ["Loaded Module Counts by Category", "-" * 80]
    counts: dict[str, int] = {}
    for m in report.modules:
        cat = classify_module(m.name)
        counts[cat] = counts.get(cat, 0) + 1
    for cat in ("plugin-path", "host", "audio-driver", "licensing", "graphics", "non-system", "system/runtime", "system", "unknown"):
        if counts.get(cat):
            lines.append(f"{cat:16} {counts[cat]}")
    lines.append("")

    return "\n".join(lines)
