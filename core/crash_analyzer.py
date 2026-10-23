"""
Crash analysis for Studio One / Fender Studio Pro.

Finds crash dumps and logs in the usual locations and analyzes them with the
built-in minidump reader. Nothing is executed and nothing is sent anywhere;
reports are written to a local folder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re

from core.common import app_dir, format_bytes
from core.minidump_reader import analyze_minidump, format_minidump_report


TEXT_EXTENSIONS = {".txt", ".log"}
DUMP_EXTENSIONS = {".dmp", ".mdmp"}
REPORTS_FOLDER_NAME = "StudioOneProjectTools_CrashReports"

_DUMP_DIR_TOKENS = ("dump", "crash")
_VERSIONED_RE = re.compile(r"studio\s*(?:one|pro)?\s*(\d+)")


@dataclass
class CrashFile:
    path: Path
    kind: str
    size: int
    mtime: float


@dataclass
class CrashAnalysisResult:
    source_path: Path
    process_name: str = ""
    faulting_module: str = ""
    image_name: str = ""
    module_name: str = ""
    exception_code: str = ""
    exception_description: str = ""
    exception_address: str = ""
    stack_modules: list[str] = field(default_factory=list)
    loaded_modules: list[str] = field(default_factory=list)
    plugin_matches: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    report_path: Path | None = None
    report_text: str = ""
    errors: list[str] = field(default_factory=list)


def default_report_dir() -> Path:
    """Where crash reports are written: %LOCALAPPDATA%\\StudioOneProjectTools\\CrashReports."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "StudioOneProjectTools" / "CrashReports"
    return app_dir() / "CrashReports"


def _studio_one_version_key(path: Path) -> tuple[int, str]:
    m = _VERSIONED_RE.search(path.name.lower())
    return (int(m.group(1)) if m else -1, str(path).lower())


def crash_search_roots(custom_root: str | None = None) -> list[Path]:
    """
    Root folders to scan for dumps/logs.

    Manual root: only that folder.
    Auto, in priority order:
    1. Windows user-mode dumps: %LOCALAPPDATA%\\CrashDumps
    2. Fender Studio Pro: %APPDATA%\\Fender, %LOCALAPPDATA%\\Fender
    3. PreSonus / Studio One application data, newest version first.
    The Documents folder (song library) is deliberately not scanned.
    """
    roots: list[Path] = []

    def add(p: Path):
        try:
            p = p.resolve()
            if p.is_dir() and p not in roots:
                roots.append(p)
        except OSError:
            pass

    if custom_root:
        add(Path(custom_root))
        return roots

    appdata = os.environ.get("APPDATA")
    localappdata = os.environ.get("LOCALAPPDATA")
    programdata = os.environ.get("PROGRAMDATA")

    if localappdata:
        add(Path(localappdata) / "CrashDumps")
    for base in (appdata, localappdata):
        if base:
            add(Path(base) / "Fender")

    versioned: list[Path] = []
    fallback: list[Path] = []
    for base in (appdata, localappdata, programdata):
        if not base:
            continue
        presonus = Path(base) / "PreSonus"
        if not presonus.is_dir():
            continue
        try:
            versioned += [p for p in presonus.iterdir() if p.is_dir() and p.name.lower().startswith(("studio one", "studio pro"))]
        except OSError:
            pass
        fallback.append(presonus)

    for p in sorted(set(versioned), key=_studio_one_version_key, reverse=True):
        add(p)
    for p in fallback:
        add(p)

    return roots


def is_own_crash_report_file(path: Path) -> bool:
    name = path.name.lower()
    if REPORTS_FOLDER_NAME.lower() in (p.lower() for p in path.parts):
        return True
    return name.endswith(("_crash_report.txt", "_raw_debug_output.txt", "_pure_dump_report.txt", "_raw_log_output.txt"))


def _walk_roots(custom_root: str | None):
    """Yield (dir_path, [file names]) for every folder under the search roots, skipping own report folders."""
    seen: set[str] = set()
    for root in crash_search_roots(custom_root):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() != REPORTS_FOLDER_NAME.lower()]
            key = dirpath.lower()
            if key in seen:
                continue
            seen.add(key)
            yield Path(dirpath), filenames


def discover_crash_files(custom_root: str | None = None, limit: int = 200) -> tuple[list[CrashFile], list[Path], list[Path]]:
    """
    Single pass over all search roots.

    Returns (newest crash files first, dump folders, log folders).
    """
    files: list[CrashFile] = []
    dump_dirs: list[Path] = []
    log_dirs: list[Path] = []

    for d, names in _walk_roots(custom_root):
        has_dump = has_log = False
        for name in names:
            ext = os.path.splitext(name)[1].lower()
            if ext in DUMP_EXTENSIONS:
                kind = "dump"
            elif ext in TEXT_EXTENSIONS:
                kind = "log"
            else:
                continue
            p = d / name
            if is_own_crash_report_file(p):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            files.append(CrashFile(path=p, kind=kind, size=st.st_size, mtime=st.st_mtime))
            has_dump |= kind == "dump"
            has_log |= kind == "log"

        if has_dump or any(tok in d.name.lower() for tok in _DUMP_DIR_TOKENS):
            dump_dirs.append(d)
        if has_log:
            log_dirs.append(d)

    files.sort(key=lambda x: x.mtime, reverse=True)
    return files[:limit], dump_dirs, log_dirs


def crash_roots_diagnostics(custom_root: str | None = None) -> str:
    roots = crash_search_roots(custom_root)
    files, dump_dirs, log_dirs = discover_crash_files(custom_root, limit=10000)

    lines = [
        "Crash/log roots diagnostics",
        "=" * 80,
        f"Mode: {'manual root only' if custom_root else 'auto'}",
        f"Roots: {len(roots)}",
        f"Dump dirs: {len(dump_dirs)}",
        f"Log dirs: {len(log_dirs)}",
        f"Files: {len(files)}",
        f"Reports are written to: {default_report_dir()}",
        "",
        "Roots",
        "-" * 80,
    ]
    lines += [str(r) for r in roots]
    lines += ["", "Newest files", "-" * 80]
    lines += [f"{f.kind:4} | {format_bytes(f.size):>10} | {f.path}" for f in files[:50]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Text log analysis
# ---------------------------------------------------------------------------

_SYSTEM_MODULE_MARKERS = (
    "ntdll.dll", "kernelbase.dll", "kernel32.dll", "ucrtbase.dll",
    "vcruntime", "msvcp", "user32.dll", "gdi32.dll", "win32u.dll",
    "combase.dll", "ole32.dll", "shell32.dll", "advapi32.dll",
    "sechost.dll", "rpcrt4.dll", "studio one.exe", "studioone.exe",
)


def _first_regex(text: str, patterns: list[str]) -> str:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return (m.group(1) if m.groups() else m.group(0)).strip()
    return ""


def _normalize_mod_name(s: str) -> str:
    return s.strip().strip("`'\"").replace("\\", "/").split("/")[-1].lower()


def is_likely_system_module(mod: str) -> bool:
    m = _normalize_mod_name(mod)
    return any(x in m for x in _SYSTEM_MODULE_MARKERS)


def extract_modules(text: str) -> list[str]:
    mods = []
    seen = set()
    patterns = (
        r"\b([A-Za-z0-9_.\- ()]+\.(?:dll|vst3|vst|aaxplugin|sys|exe))\b",
        r"\b([A-Za-z0-9_.\- ()]+)![A-Za-z0-9_?$@]+",
    )
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            name = _normalize_mod_name(m.group(1))
            if name and name not in seen:
                seen.add(name)
                mods.append(name)
                if len(mods) >= 1000:
                    return mods
    return mods


def parse_crash_text(source_path: Path, text: str) -> CrashAnalysisResult:
    result = CrashAnalysisResult(source_path=source_path)

    result.image_name = _first_regex(text, [
        r"^\s*IMAGE_NAME:\s*(.+)$",
        r"Faulting module name:\s*([^,\r\n]+)",
        r"faulting module:\s*([^,\r\n]+)",
    ])
    result.module_name = _first_regex(text, [r"^\s*MODULE_NAME:\s*(.+)$", r"Module Name:\s*([^,\r\n]+)"])
    result.faulting_module = result.image_name or result.module_name
    result.exception_code = _first_regex(text, [
        r"^\s*EXCEPTION_CODE:\s*\((?:NTSTATUS)?\)\s*(0x[0-9a-fA-F]+|[0-9a-fA-F]{8,})",
        r"^\s*ExceptionCode:\s*(0x[0-9a-fA-F]+|[0-9a-fA-F]{8,})",
        r"Exception code:\s*(0x[0-9a-fA-F]+|[0-9a-fA-F]{8,})",
    ])
    result.exception_description = _first_regex(text, [r"^\s*EXCEPTION_CODE:.*-\s*(.+)$", r"^\s*ExceptionCode:.*\((.+)\)"])
    result.exception_address = _first_regex(text, [r"^\s*EXCEPTION_ADDRESS:\s*(.+)$", r"^\s*ExceptionAddress:\s*(.+)$"])
    result.process_name = _first_regex(text, [r"^\s*PROCESS_NAME:\s*(.+)$", r"Faulting application name:\s*([^,\r\n]+)"])

    modules = extract_modules(text)
    result.stack_modules = modules[:120]
    result.loaded_modules = modules

    if not result.faulting_module:
        result.faulting_module = next((m for m in result.stack_modules if not is_likely_system_module(m)), "")

    result.hints = build_hints(result, text)
    return result


def build_hints(result: CrashAnalysisResult, text: str) -> list[str]:
    hay = (text + "\n" + " ".join(result.stack_modules + result.loaded_modules)).lower()
    hints = []

    if "pace" in hay or "ilok" in hay or "eden" in hay:
        hints.append("PACE/iLok appears in the context. Check iLok License Manager, the PACE drivers and iLok-protected plugins.")
    if "neural" in hay or "ndsp" in hay:
        hints.append("Neural DSP traces found. Test with Neural DSP plugins bypassed/removed first.")
    if ".vst3" in hay or ".vst" in hay:
        hints.append("A VST/VST3 module appears in the dump/log. If it is the faulting module or near the top of the stack, a plugin crash is likely.")
    if ".sys" in hay:
        hints.append("A .sys module appears. If it is the faulting module, suspect a driver, audio interface or authorization layer.")

    code = (result.exception_code or "").lower()
    if "c0000005" in code:
        hints.append("c0000005 = access violation. Typical of a native plugin crash, a bad driver hook or corrupted plugin state.")
    elif "80000003" in code:
        hints.append("80000003 = breakpoint/assert. May be a deliberate break inside a plugin or the host.")
    elif "c0000409" in code:
        hints.append("c0000409 = fail-fast / stack buffer overrun. Often points to faulty native module/plugin code.")

    if not hints:
        hints.append("No strong signature found. Look at the faulting module and the first non-system modules in the stack.")

    return hints


def match_plugins_to_crash(result: CrashAnalysisResult, plugins) -> list[str]:
    """Name-based matching for text logs (dump analysis uses stack evidence instead)."""
    if not plugins:
        return []

    crash_names = set()
    for val in [result.faulting_module, result.image_name, result.module_name] + result.stack_modules[:100] + result.loaded_modules[:300]:
        if val:
            norm = _normalize_mod_name(val)
            for v in (norm, Path(norm).stem.lower()):
                c = re.sub(r"[^a-z0-9]+", "", v)
                if len(c) >= 4:
                    crash_names.add(c)

    matches = []
    for p in plugins:
        pname = getattr(p, "plugin_name", "") or ""
        clean = re.sub(r"[^a-z0-9]+", "", pname.lower())
        if len(clean) < 3:
            continue

        hit = any(clean in cn or cn in clean for cn in crash_names)
        if not hit:
            # Token fallback for names like "FabFilter Pro-Q 3" vs "Pro-Q 3.vst3".
            tokens = [t for t in re.split(r"[^a-z0-9]+", pname.lower()) if len(t) >= 3]
            hit = bool(tokens) and any(all(t in cn for t in tokens[:3]) for cn in crash_names)

        if hit:
            matches.append(f"{getattr(p, 'track_name', '')} / {getattr(p, 'slot_name', '')} / {pname} / bypassed={getattr(p, 'bypassed', False)}")

    return matches[:100]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _plugin_names(plugins) -> list[str]:
    names = []
    seen = set()
    for p in plugins or []:
        value = getattr(p, "plugin_name", "") or ""
        if value and value.lower() not in seen:
            seen.add(value.lower())
            names.append(value)
    return names


def analyze_crash_file(path: Path, plugins=None, output_dir: Path | None = None) -> CrashAnalysisResult:
    """
    Analyze a .dmp/.mdmp minidump or a text log and write <name>_crash_report.txt.

    Dumps are parsed directly (exception, module list, thread stacks); project FX
    are matched only against stack evidence, because every plugin in a project is
    loaded and "loaded" alone is not evidence.
    """
    path = Path(path)
    output_dir = output_dir or default_report_dir()
    result = CrashAnalysisResult(source_path=path)

    try:
        if path.suffix.lower() in DUMP_EXTENSIONS:
            mini = analyze_minidump(path)

            if mini.exception:
                result.exception_code = f"0x{mini.exception.code:08X}"
                result.exception_description = mini.exception.code_name
                result.exception_address = f"0x{mini.exception.address:016X}"
            if mini.faulting_module:
                result.faulting_module = mini.faulting_module.name
                result.image_name = Path(mini.faulting_module.name).name
                result.module_name = Path(mini.faulting_module.name).stem
            if mini.error:
                result.errors.append(mini.error)

            result.loaded_modules = [m.name for m in mini.modules if m.name]
            result.stack_modules = [h.module.name for h in mini.all_stack_hits if h.module.name]

            if mini.faulting_module:
                result.hints.append("Fault surface module was resolved by mapping the exception address to a loaded module range.")
            elif mini.exception:
                result.hints.append("An exception was found, but its address is not inside any loaded module.")
            else:
                result.hints.append("No ExceptionStream found in this minidump.")

            result.report_text = format_minidump_report(mini, _plugin_names(plugins))
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            result = parse_crash_text(path, text)
            result.plugin_matches = match_plugins_to_crash(result, plugins or [])
            result.report_text = make_report(result)
    except OSError as e:
        result.errors.append(str(e))
        result.report_text = make_report(result)

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / f"{path.stem}_crash_report.txt"
        report_path.write_text(result.report_text, encoding="utf-8", errors="replace")
        result.report_path = report_path
    except OSError as e:
        result.errors.append(f"Could not write report: {e}")

    return result


def make_report(r: CrashAnalysisResult) -> str:
    lines = [
        "Studio One Project Tools - Crash Analysis Report",
        "=" * 80,
        f"Source:          {r.source_path}",
        "Analyzer:        built-in log parser",
        "",
        "Crash summary",
        "-" * 80,
        f"Process:         {r.process_name}",
        f"Faulting module: {r.faulting_module}",
        f"IMAGE_NAME:      {r.image_name}",
        f"MODULE_NAME:     {r.module_name}",
        f"Exception code:  {r.exception_code}",
        f"Description:     {r.exception_description}",
        f"Address:         {r.exception_address}",
        "",
        "Plugin matches in opened .song",
        "-" * 80,
    ]
    lines += r.plugin_matches or ["No plugin name matches found against the currently opened .song."]
    lines += ["", "Hints", "-" * 80]
    lines += r.hints or ["No hints."]
    lines += ["", "Top modules", "-" * 80]
    lines += r.stack_modules[:80]
    lines.append("")
    if r.errors:
        lines += ["Errors", "-" * 80] + r.errors + [""]
    return "\n".join(lines)


def result_to_display_text(r: CrashAnalysisResult) -> str:
    text = r.report_text or make_report(r)
    if r.report_path:
        text += f"\nReport saved to: {r.report_path}\n"
    if r.errors:
        text += "\nErrors:\n" + "\n".join(f"  - {e}" for e in r.errors) + "\n"
    return text
