from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import struct
import re


THREAD_LIST_STREAM = 3
MODULE_LIST_STREAM = 4
EXCEPTION_STREAM = 6
SYSTEM_INFO_STREAM = 7
MISC_INFO_STREAM = 15

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
class MiniThreadStack:
    thread_id: int
    start: int
    data: bytes


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
    suspicious_modules: list[MiniModule] = field(default_factory=list)
    thread_stacks: list[MiniThreadStack] = field(default_factory=list)
    exception_thread_stack_hits: list[StackModuleHit] = field(default_factory=list)
    all_stack_hits: list[StackModuleHit] = field(default_factory=list)


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def _read_utf16_string(data: bytes, rva: int) -> str:
    if not rva or rva + 4 > len(data):
        return ""
    try:
        byte_len = _u32(data, rva)
        start = rva + 4
        end = min(start + byte_len, len(data))
        return data[start:end].decode("utf-16-le", errors="replace").rstrip("\x00")
    except Exception:
        return ""


def _stream_dirs(data: bytes) -> dict[int, tuple[int, int]]:
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
        stream_type = _u32(data, off)
        size = _u32(data, off + 4)
        rva = _u32(data, off + 8)
        if rva and size and rva + size <= len(data):
            streams[stream_type] = (rva, size)
    return streams


def _parse_system_info(data: bytes, streams: dict[int, tuple[int, int]]) -> str:
    loc = streams.get(SYSTEM_INFO_STREAM)
    if not loc:
        return ""
    rva, size = loc
    if size < 2:
        return ""
    arch = struct.unpack_from("<H", data, rva)[0]
    return {
        0: "x86",
        5: "ARM",
        6: "IA64",
        9: "x64 / AMD64",
        12: "ARM64",
    }.get(arch, f"unknown ({arch})")


def _parse_misc_info(data: bytes, streams: dict[int, tuple[int, int]]) -> int:
    loc = streams.get(MISC_INFO_STREAM)
    if not loc:
        return 0
    rva, size = loc
    if size >= 12:
        try:
            flags = _u32(data, rva + 4)
            if flags & 0x00000001:
                return _u32(data, rva + 8)
        except Exception:
            pass
    return 0


def _parse_exception(data: bytes, streams: dict[int, tuple[int, int]]) -> MiniException | None:
    loc = streams.get(EXCEPTION_STREAM)
    if not loc:
        return None
    rva, size = loc
    if size < 8 + 32:
        return None
    try:
        thread_id = _u32(data, rva)
        ex = rva + 8
        code = _u32(data, ex)
        flags = _u32(data, ex + 4)
        record = _u64(data, ex + 8)
        address = _u64(data, ex + 16)
        nparams = min(max(_u32(data, ex + 24), 0), 15)
        params = []
        param_base = ex + 32
        for i in range(nparams):
            poff = param_base + i * 8
            if poff + 8 <= len(data):
                params.append(_u64(data, poff))
        return MiniException(thread_id, code, flags, record, address, params)
    except Exception:
        return None


def _parse_modules(data: bytes, streams: dict[int, tuple[int, int]]) -> list[MiniModule]:
    loc = streams.get(MODULE_LIST_STREAM)
    if not loc:
        return []
    rva, size = loc
    if size < 4:
        return []

    try:
        count = _u32(data, rva)
    except Exception:
        return []

    modules: list[MiniModule] = []
    off = rva + 4
    entry_size = 108

    for _ in range(count):
        if off + entry_size > len(data):
            break
        try:
            base = _u64(data, off + 0)
            img_size = _u32(data, off + 8)
            checksum = _u32(data, off + 12)
            timestamp = _u32(data, off + 16)
            name_rva = _u32(data, off + 20)
            name = _read_utf16_string(data, name_rva)
            modules.append(MiniModule(base=base, size=img_size, checksum=checksum, timestamp=timestamp, name=name))
        except Exception:
            pass
        off += entry_size

    modules.sort(key=lambda m: m.base)
    return modules


def _classify_suspicious(modules: list[MiniModule]) -> list[MiniModule]:
    # Generic relevance only. No vendor list.
    categories = {"plugin-path", "audio-driver", "licensing", "graphics", "host", "non-system"}
    return [m for m in modules if classify_module(m.name) in categories]



def _parse_thread_stacks(data: bytes, streams: dict[int, tuple[int, int]]) -> list[MiniThreadStack]:
    loc = streams.get(THREAD_LIST_STREAM)
    if not loc:
        return []
    rva, size = loc
    if size < 4:
        return []

    try:
        count = _u32(data, rva)
    except Exception:
        return []

    stacks: list[MiniThreadStack] = []
    off = rva + 4
    entry_size = 48  # MINIDUMP_THREAD

    for _ in range(count):
        if off + entry_size > len(data):
            break
        try:
            thread_id = _u32(data, off + 0)

            # MINIDUMP_THREAD:
            # ULONG32 ThreadId;
            # ULONG32 SuspendCount;
            # ULONG32 PriorityClass;
            # ULONG32 Priority;
            # ULONG64 Teb;
            # MINIDUMP_MEMORY_DESCRIPTOR Stack;
            # MINIDUMP_LOCATION_DESCRIPTOR ThreadContext;
            # Stack starts at offset +24:
            #   StartOfMemoryRange ULONG64
            #   Memory.DataSize ULONG32
            #   Memory.Rva ULONG32
            stack_start = _u64(data, off + 24)
            stack_size = _u32(data, off + 32)
            stack_rva = _u32(data, off + 36)

            if stack_start and stack_size and stack_rva and stack_rva + stack_size <= len(data):
                stacks.append(MiniThreadStack(thread_id=thread_id, start=stack_start, data=data[stack_rva:stack_rva + stack_size]))
        except Exception:
            pass
        off += entry_size

    return stacks


def _module_for_address(modules: list[MiniModule], address: int) -> MiniModule | None:
    # Linear scan is fine for typical minidumps. Module list is usually small.
    for m in modules:
        if m.contains(address):
            return m
    return None


def _scan_stack_for_module_addresses(stacks: list[MiniThreadStack], modules: list[MiniModule]) -> list[StackModuleHit]:
    """
    Heuristic stack scanner.

    It scans every aligned 8-byte value in saved thread stacks.
    If a value points inside a loaded module range, it is treated as a possible
    return address / saved pointer to code. This is not as precise as a symbolic
    call stack, but it can reveal plugin/DLL involvement when the exception
    address itself is only kernelbase/ucrtbase.
    """
    hits: list[StackModuleHit] = []

    for st in stacks:
        data = st.data
        max_len = len(data) - 8
        if max_len < 0:
            continue

        # Step 8 for x64. Studio One / Fender Studio Pro is x64 in practice.
        for pos in range(0, max_len + 1, 8):
            try:
                value = struct.unpack_from("<Q", data, pos)[0]
            except Exception:
                continue

            # User-mode canonical-ish range filter to reduce noise.
            if value < 0x10000 or value > 0x00007FFFFFFFFFFF:
                continue

            mod = _module_for_address(modules, value)
            if mod:
                hits.append(StackModuleHit(
                    thread_id=st.thread_id,
                    stack_offset=pos,
                    address=value,
                    module=mod,
                    module_offset=value - mod.base,
                ))

    return hits


def _dedupe_stack_hits(hits: list[StackModuleHit], limit: int = 300) -> list[StackModuleHit]:
    seen = set()
    out = []
    for h in hits:
        key = (h.thread_id, h.module.name.lower(), h.module_offset)
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= limit:
            break
    return out


def _rank_modules_from_stack_hits(hits: list[StackModuleHit]) -> list[tuple[MiniModule, int]]:
    counts: dict[str, tuple[MiniModule, int]] = {}
    for h in hits:
        key = h.module.name.lower()
        if key not in counts:
            counts[key] = (h.module, 0)
        counts[key] = (counts[key][0], counts[key][1] + 1)
    ranked = sorted(counts.values(), key=lambda x: x[1], reverse=True)
    return ranked



def normalize_module_path(name: str) -> str:
    return (name or "").replace("/", "\\").lower()


def classify_module(name: str) -> str:
    """
    Generic classification only. No vendor/brand guesses.

    Categories:
    - system/runtime
    - host
    - plugin-path
    - audio-driver
    - licensing
    - graphics
    - non-system
    """
    n = normalize_module_path(name)

    if not n:
        return "unknown"

    if "\\windows\\system32\\" in n or "\\windows\\syswow64\\" in n:
        base = n.rsplit("\\", 1)[-1]
        runtime_prefixes = ("ucrt", "vcruntime", "msvcp", "concrt")
        runtime_exact = {
            "kernelbase.dll", "kernel32.dll", "ntdll.dll", "user32.dll", "win32u.dll",
            "gdi32.dll", "gdi32full.dll", "combase.dll", "rpcrt4.dll", "ole32.dll",
            "shell32.dll", "advapi32.dll", "sechost.dll", "bcrypt.dll", "bcryptprimitives.dll",
            "ws2_32.dll", "winmm.dll", "mmdevapi.dll", "cfgmgr32.dll", "setupapi.dll",
        }
        if base in runtime_exact or base.startswith(runtime_prefixes):
            return "system/runtime"
        return "system"

    base = n.rsplit("\\", 1)[-1]

    if base in {"studio one.exe", "studio pro.exe", "studioone.exe"} or "studio one" in base or "studio pro" in base:
        return "host"

    plugin_path_markers = (
        "\\common files\\vst3\\",
        "\\vstplugins\\",
        "\\steinberg\\vstplugins\\",
        "\\common files\\clap\\",
        "\\audio\\plug-ins\\",
        "\\plugins\\vst\\",
        "\\plugins\\vst3\\",
        ".vst3\\",
        ".vst\\",
        ".clap\\",
        ".aaxplugin\\",
        ".component\\",
    )
    if any(x in n for x in plugin_path_markers):
        return "plugin-path"

    if "asio" in base or "\\asio\\" in n or "wasapi" in base:
        return "audio-driver"

    licensing_markers = ("pace", "ilok", "license", "licence", "activation", "codemeter", "wibu")
    if any(x in n for x in licensing_markers):
        return "licensing"

    graphics_markers = ("nvoglv", "nvwgf", "nvcuda", "atidxx", "amdxx", "d3d", "dxgi", "vulkan", "opengl", "igc", "igd")
    if any(x in base for x in graphics_markers):
        return "graphics"

    return "non-system"


def is_system_or_runtime_module(name: str) -> bool:
    return classify_module(name) in {"system/runtime", "system"}


def is_plugin_path_module(name: str) -> bool:
    return classify_module(name) == "plugin-path"


def _is_system_noise_module(name: str) -> bool:
    return classify_module(name) in {"system/runtime", "system"}


def _rank_modules_from_stack_hits(hits: list[StackModuleHit]) -> list[tuple[MiniModule, int]]:
    counts: dict[str, tuple[MiniModule, int]] = {}
    for h in hits:
        key = h.module.name.lower()
        if key not in counts:
            counts[key] = (h.module, 0)
        counts[key] = (counts[key][0], counts[key][1] + 1)
    ranked = sorted(counts.values(), key=lambda x: x[1], reverse=True)
    return ranked


def _modules_by_category(modules: list[MiniModule], category: str) -> list[MiniModule]:
    return [m for m in modules if classify_module(m.name) == category]


def _interesting_stack_modules(hits: list[StackModuleHit]) -> list[tuple[MiniModule, int]]:
    ranked = _rank_modules_from_stack_hits(hits)
    return [(m, c) for m, c in ranked if not _is_system_noise_module(m.name)]


def _format_module_line(m: MiniModule) -> str:
    return f"[{classify_module(m.name)}] 0x{m.base:016X}-0x{m.end:016X}  {m.name}"



def analyze_minidump(path: str | Path) -> MiniDumpReport:
    path = Path(path)
    report = MiniDumpReport(path=path)
    try:
        data = path.read_bytes()
        streams = _stream_dirs(data)
        report.valid = True
        report.processor_arch = _parse_system_info(data, streams)
        report.process_id = _parse_misc_info(data, streams)
        report.exception = _parse_exception(data, streams)
        report.modules = _parse_modules(data, streams)
        report.thread_stacks = _parse_thread_stacks(data, streams)

        if report.exception:
            addr = report.exception.address
            for m in report.modules:
                if m.contains(addr):
                    report.faulting_module = m
                    report.fault_module_offset = addr - m.base
                    break

        report.all_stack_hits = _dedupe_stack_hits(_scan_stack_for_module_addresses(report.thread_stacks, report.modules), limit=1000)

        if report.exception:
            report.exception_thread_stack_hits = [
                h for h in report.all_stack_hits
                if h.thread_id == report.exception.thread_id
            ]

        report.suspicious_modules = _classify_suspicious(report.modules)
    except Exception as e:
        report.error = str(e)
    return report



def _tokenize_for_match(text: str) -> set[str]:
    """
    Generic tokenization for matching project FX names against dump module paths.
    No vendor hardcoding.
    """
    text = (text or "").lower()
    text = text.replace("\\", " ").replace("/", " ")
    text = re.sub(r"[\._\-\+\(\)\[\]\{\},;:]+", " ", text)
    text = re.sub(r"\b(vst3|vst|clap|aax|dll|exe|x64|x86|win64|win32|plugin|plugins|common|files|program|data|steinberg|contents)\b", " ", text)
    tokens = {t for t in text.split() if len(t) >= 3}
    return tokens


def _project_fx_match_score(project_name: str, module_name: str) -> tuple[int, list[str]]:
    p_tokens = _tokenize_for_match(project_name)
    m_tokens = _tokenize_for_match(module_name)

    if not p_tokens or not m_tokens:
        return 0, []

    common = sorted(p_tokens & m_tokens)
    if not common:
        # substring fallback for compact plugin names
        p = re.sub(r"[^a-z0-9]+", "", project_name.lower())
        m = re.sub(r"[^a-z0-9]+", "", module_name.lower())
        if len(p) >= 4 and p in m:
            return 100, [project_name]
        return 0, []

    # Strong enough for practical FX-to-DLL matching.
    score = len(common) * 20

    # Boost when project name is mostly covered by module/path.
    coverage = len(common) / max(len(p_tokens), 1)
    if coverage >= 0.75:
        score += 50
    elif coverage >= 0.5:
        score += 25

    # Boost plugin-path modules because those are more likely actual FX binaries.
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

    Important rule:
    loaded modules alone are NOT evidence of crash involvement.
    Studio One can load many plugins from the project; showing all of them as
    matches makes the report useless.

    Evidence used by default:
    - stack module hits only.

    Loaded modules can be included only for a separate informational count,
    not for the main culprit list.
    """
    if not project_names:
        return []

    evidence: list[tuple[str, MiniModule, int]] = []
    for m, count in ranked_modules:
        if count <= 0:
            continue
        evidence.append(("stack", m, count))

    if include_loaded_only and loaded_modules:
        existing = {m.name.lower() for _, m, _ in evidence}
        for m in loaded_modules:
            if m.name.lower() not in existing:
                evidence.append(("loaded", m, 0))

    matches = []
    for fx in project_names:
        best = None
        for source, mod, hits in evidence:
            score, tokens = _project_fx_match_score(fx, mod.name)
            if score <= 0:
                continue

            if source == "stack":
                score += 100 + min(hits, 75)
            else:
                # Loaded-only is weak and must never dominate.
                score += 5

            if score < min_score:
                continue

            item = {
                "fx": fx,
                "module": mod,
                "source": source,
                "hits": hits,
                "score": score,
                "tokens": tokens,
            }
            if best is None or item["score"] > best["score"]:
                best = item
        if best:
            matches.append(best)

    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches


def _loaded_project_fx_matches(project_names: list[str], loaded_modules: list[MiniModule]) -> list[dict]:
    """
    Informational only. Shows what project FX are simply loaded in the dump.
    This is NOT used as crash evidence.
    """
    return _collect_project_matches(
        project_names,
        ranked_modules=[],
        loaded_modules=loaded_modules,
        include_loaded_only=True,
        min_score=1,
    )



def _format_project_match(item: dict) -> str:
    mod = item["module"]
    tokens = ", ".join(item["tokens"][:8]) if item.get("tokens") else ""
    return (
        f"{item['fx']}  ->  {mod.name}  "
        f"[source={item['source']}, hits={item['hits']}, score={item['score']}, category={classify_module(mod.name)}"
        + (f", tokens={tokens}" if tokens else "")
        + "]"
    )



def module_rank(module: MiniModule, hits: int, *, exception_thread: bool = False) -> int:
    """
    Generic suspicion rank for any non-system module.

    No vendor hardcoding.
    Higher = more relevant.

    Inputs:
    - stack hit count;
    - whether the module was in exception thread stack;
    - generic category.
    """
    category = classify_module(module.name)

    base = 0
    if category == "plugin-path":
        base = 90
    elif category == "audio-driver":
        base = 85
    elif category == "licensing":
        base = 80
    elif category == "graphics":
        base = 70
    elif category == "host":
        base = 55
    elif category == "non-system":
        base = 50
    elif category == "unknown":
        base = 30
    elif category in {"system/runtime", "system"}:
        base = 5

    if exception_thread:
        base += 60

    # Hits matter, but cap them so a noisy stack does not dominate everything.
    base += min(max(hits, 0), 100)

    return base


def _ranked_with_suspicion(
    ranked: list[tuple[MiniModule, int]],
    *,
    exception_thread: bool = False,
    include_system: bool = False,
) -> list[tuple[MiniModule, int, int]]:
    out = []
    for m, hits in ranked:
        if not include_system and is_system_or_runtime_module(m.name):
            continue
        rank = module_rank(m, hits, exception_thread=exception_thread)
        out.append((m, hits, rank))
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def format_minidump_report(report: MiniDumpReport, plugin_names: list[str] | None = None) -> str:
    lines = []
    lines.append("Studio One Project Tools - Project Crash Match Report")
    lines.append("=" * 80)
    lines.append(f"File: {report.path}")
    lines.append(f"Valid minidump: {'yes' if report.valid else 'no'}")
    if report.error:
        lines.append(f"Error: {report.error}")
        return "\n".join(lines)

    lines.append(f"Processor: {report.processor_arch or 'unknown'}")
    if report.process_id:
        lines.append(f"Process ID: {report.process_id}")
    lines.append("")

    lines.append("Exception")
    lines.append("-" * 80)
    if report.exception:
        e = report.exception
        lines.append(f"Thread ID: 0x{e.thread_id:X} ({e.thread_id})")
        lines.append(f"Code:      0x{e.code:08X} {e.code_name}")
        lines.append(f"Address:   0x{e.address:016X}")
    else:
        lines.append("No ExceptionStream found in this dump.")
    lines.append("")

    lines.append("Fault surface module")
    lines.append("-" * 80)
    if report.faulting_module:
        m = report.faulting_module
        lines.append(_format_module_line(m))
        if report.fault_module_offset is not None:
            lines.append(f"Offset: +0x{report.fault_module_offset:X}")
        if classify_module(m.name) in {"system/runtime", "system"}:
            lines.append("Note: system/runtime surface module; real caller can be lower in stack.")
    else:
        lines.append("Not resolved by exception address.")
    lines.append("")

    exception_hits = report.exception_thread_stack_hits or []
    all_hits = report.all_stack_hits or []

    exception_ranked = _rank_modules_from_stack_hits(exception_hits)
    exception_non_system = [(m, c) for m, c in exception_ranked if not is_system_or_runtime_module(m.name)]
    exception_plugin = [(m, c) for m, c in exception_non_system if is_plugin_path_module(m.name)]

    all_ranked = _rank_modules_from_stack_hits(all_hits)
    all_non_system = [(m, c) for m, c in all_ranked if not is_system_or_runtime_module(m.name)]
    all_plugin = [(m, c) for m, c in all_non_system if is_plugin_path_module(m.name)]

    plugin_names = plugin_names or []
    project_matches_exception = _collect_project_matches(
        plugin_names,
        exception_non_system,
        [],
        include_loaded_only=False,
        min_score=120,
    )
    project_matches_all = _collect_project_matches(
        plugin_names,
        all_non_system,
        [],
        include_loaded_only=False,
        min_score=120,
    )
    loaded_only_project_matches = _loaded_project_fx_matches(plugin_names, report.modules)

    lines.append("Project FX matched in EXCEPTION THREAD stack (strong evidence)")
    lines.append("-" * 80)
    if plugin_names:
        if project_matches_exception:
            for idx, item in enumerate(project_matches_exception[:25], 1):
                lines.append(f"{idx:02}. {_format_project_match(item)}")
            if len(project_matches_exception) > 25:
                lines.append(f"... {len(project_matches_exception) - 25} more hidden")
        else:
            lines.append("No opened .song FX matched inside exception-thread stack modules.")
    else:
        lines.append("Project context not loaded. Open a .song first to match dump modules against project FX.")
    lines.append("")

    lines.append("Project FX matched in ALL THREAD stacks (supporting evidence)")
    lines.append("-" * 80)
    if plugin_names:
        if project_matches_all:
            for idx, item in enumerate(project_matches_all[:35], 1):
                lines.append(f"{idx:02}. {_format_project_match(item)}")
            if len(project_matches_all) > 35:
                lines.append(f"... {len(project_matches_all) - 35} more hidden")
        else:
            lines.append("No opened .song FX matched in all-thread stack modules.")
    else:
        lines.append("Project context not loaded.")
    lines.append("")

    def append_ranked(title: str, ranked: list[tuple[MiniModule, int]], limit: int, empty: str, *, exception_thread: bool = False):
        lines.append(title)
        lines.append("-" * 80)
        if ranked:
            ranked3 = _ranked_with_suspicion(ranked, exception_thread=exception_thread, include_system=False)
            for idx, (m, count, rank) in enumerate(ranked3[:limit], 1):
                lines.append(f"{idx:02}. rank={rank:3} hits={count:3}  {_format_module_line(m)}")
            if len(ranked3) > limit:
                lines.append(f"... {len(ranked3) - limit} more hidden")
        else:
            lines.append(empty)
        lines.append("")

    lines.append("Loaded project FX present in dump")
    lines.append("-" * 80)
    if plugin_names:
        lines.append("Informational only: loaded module presence is NOT crash evidence.")
        if loaded_only_project_matches:
            lines.append(f"Loaded project FX/module matches: {len(loaded_only_project_matches)}")
            lines.append("Hidden from main suspect list to avoid matching the whole project.")
        else:
            lines.append("No loaded project FX/module name matches.")
    else:
        lines.append("Project context not loaded.")
    lines.append("")

    append_ranked(
        "Top Exception Thread Non-System Modules",
        exception_non_system,
        20,
        "No non-system modules found in exception thread stack.",
        exception_thread=True,
    )

    append_ranked(
        "Top Exception Thread Plugin-Path Modules",
        exception_plugin,
        15,
        "No plugin-path modules found in exception thread stack.",
        exception_thread=True,
    )

    append_ranked(
        "Top All-Thread Non-System Modules",
        all_non_system,
        25,
        "No non-system modules found across saved thread stacks.",
    )

    append_ranked(
        "Top All-Thread Plugin-Path Modules",
        all_plugin,
        20,
        "No plugin-path modules found across saved thread stacks.",
    )

    lines.append("Loaded Module Counts by Category")
    lines.append("-" * 80)
    categories = ["plugin-path", "host", "audio-driver", "licensing", "graphics", "non-system", "system/runtime", "system", "unknown"]
    for cat in categories:
        count = sum(1 for m in report.modules if classify_module(m.name) == cat)
        if count:
            lines.append(f"{cat:16} {count}")
    lines.append("")

    lines.append("Full evidence files")
    lines.append("-" * 80)
    lines.append("This compact report intentionally hides long raw lists and loaded-only project FX matches.")
    lines.append("If needed, use *_pure_dump_report.txt / saved report file for the full evidence.")
    lines.append("")

    return "\n".join(lines)
