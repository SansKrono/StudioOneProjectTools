from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import shutil
import subprocess

from core.minidump_reader import analyze_minidump, format_minidump_report


TEXT_EXTENSIONS = {".txt", ".log"}
DUMP_EXTENSIONS = {".dmp", ".mdmp"}

CDB_CANDIDATES = [
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe",
    r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x86\cdb.exe",
    r"C:\Program Files\Windows Kits\10\Debuggers\x64\cdb.exe",
    r"C:\Program Files\Windows Kits\10\Debuggers\x86\cdb.exe",
    r"C:\Program Files (x86)\Windows Kits\11\Debuggers\x64\cdb.exe",
    r"C:\Program Files\Windows Kits\11\Debuggers\x64\cdb.exe",
]


@dataclass
class CrashFile:
    path: Path
    kind: str
    size: int
    mtime: float


@dataclass
class CrashAnalysisResult:
    source_path: Path
    used_cdb: bool = False
    cdb_path: Path | None = None
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
    raw_output_path: Path | None = None
    report_path: Path | None = None
    errors: list[str] = field(default_factory=list)


def format_bytes(num: int | float | None) -> str:
    if num is None:
        return "0 B"
    num = float(num)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}" if unit != "B" else f"{num:.0f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def _studio_one_version_key(path: Path) -> tuple[int, str]:
    text = path.name.lower()
    m = re.search(r"studio\s*(?:one|pro)?\s*(\d+)", text)
    version = int(m.group(1)) if m else -1
    return (version, str(path).lower())


def _add_unique_path(items: list[Path], p: Path):
    try:
        p = p.resolve()
        if p.exists() and p.is_dir() and p not in items:
            items.append(p)
    except Exception:
        pass


def crash_search_roots(custom_root: str | None = None) -> list[Path]:
    """
    Returns root folders to scan for dumps/logs.

    Auto priority:
    1. Windows user-mode dumps: %LOCALAPPDATA%\\CrashDumps
    2. Fender Studio Pro paths: %APPDATA%/Fender, %LOCALAPPDATA%/Fender
    3. PreSonus / Studio One roots, versioned newest first
    """
    roots: list[Path] = []

    if custom_root:
        _add_unique_path(roots, Path(custom_root))
        return roots

    appdata = os.environ.get("APPDATA")
    localappdata = os.environ.get("LOCALAPPDATA")
    programdata = os.environ.get("PROGRAMDATA")
    userprofile = os.environ.get("USERPROFILE")

    if localappdata:
        _add_unique_path(roots, Path(localappdata) / "CrashDumps")

    if appdata:
        _add_unique_path(roots, Path(appdata) / "Fender")
    if localappdata:
        _add_unique_path(roots, Path(localappdata) / "Fender")

    candidates: list[Path] = []
    if appdata:
        candidates.append(Path(appdata) / "PreSonus")
    if localappdata:
        candidates.append(Path(localappdata) / "PreSonus")
    if programdata:
        candidates.append(Path(programdata) / "PreSonus")
    if userprofile:
        candidates.extend([
            Path(userprofile) / "Documents" / "Studio One",
            Path(userprofile) / "Documents" / "PreSonus",
            Path(userprofile) / "OneDrive" / "Documents" / "Studio One",
            Path(userprofile) / "OneDrive" / "Documents" / "PreSonus",
        ])

    versioned: list[Path] = []
    fallback: list[Path] = []

    for base in candidates:
        if not base.exists():
            continue

        if re.search(r"studio\s*(?:one|pro)?\s*\d+", base.name.lower()):
            versioned.append(base)
        else:
            try:
                for p in base.glob("Studio One*"):
                    if p.is_dir():
                        versioned.append(p)
                for p in base.glob("Studio Pro*"):
                    if p.is_dir():
                        versioned.append(p)
            except Exception:
                pass
            fallback.append(base)

    for p in sorted(set(versioned), key=_studio_one_version_key, reverse=True):
        _add_unique_path(roots, p)
    for p in fallback:
        _add_unique_path(roots, p)

    return roots


def studio_one_roots(custom_root: str | None = None) -> list[Path]:
    # Backward-compatible alias for old calls.
    return crash_search_roots(custom_root)


def is_own_crash_report_file(path: Path) -> bool:
    name = path.name.lower()
    parts = [p.lower() for p in path.parts]

    if "studiooneprojecttools_crashreports" in parts:
        return True

    if name.endswith(("_crash_report.txt", "_raw_debug_output.txt")):
        return True

    if name in {
        "crash_report.txt",
        "raw_debug_output.txt",
        "studioone_crash_report.txt",
        "studioone_raw_debug_output.txt",
    }:
        return True

    return False


def discover_studio_one_crash_dirs(custom_root: str | None = None) -> tuple[list[Path], list[Path]]:
    dump_dirs: list[Path] = []
    log_dirs: list[Path] = []

    roots = crash_search_roots(custom_root)
    dump_tokens = ("dump", "crash", "minidump", "crashdump", "crashdumps")
    log_tokens = ("log", "logs")

    for root in roots:
        if not root.exists():
            continue

        candidates = [root]
        try:
            candidates += [p for p in root.rglob("*") if p.is_dir()]
        except Exception:
            pass

        for d in candidates:
            name = d.name.lower()
            if name == "studiooneprojecttools_crashreports":
                continue
            text = str(d).lower()

            has_dump = False
            has_log = False
            try:
                for x in d.iterdir():
                    if not x.is_file():
                        continue
                    if is_own_crash_report_file(x):
                        continue
                    sx = x.suffix.lower()
                    if sx in DUMP_EXTENSIONS:
                        has_dump = True
                    elif sx in TEXT_EXTENSIONS:
                        has_log = True
            except Exception:
                pass

            # If selected root is CrashDumps/Fender, direct files should be included even without token.
            if has_dump or any(tok in text for tok in dump_tokens):
                if d not in dump_dirs:
                    dump_dirs.append(d)

            if has_log or any(tok == name for tok in log_tokens) or "fender" in text or "studio pro" in text or "studio one" in text:
                if d not in log_dirs:
                    log_dirs.append(d)

    return dump_dirs, log_dirs


def collect_crash_files(dump_dirs: list[Path], log_dirs: list[Path], limit: int = 200) -> list[CrashFile]:
    files: list[CrashFile] = []
    seen = set()

    def add_from_dir(d: Path, kind: str, exts: set[str]):
        try:
            if d.name.lower() == "studiooneprojecttools_crashreports":
                return

            for p in d.iterdir():
                if not p.is_file():
                    continue
                if p.suffix.lower() not in exts:
                    continue
                if is_own_crash_report_file(p):
                    continue
                key = str(p.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                st = p.stat()
                files.append(CrashFile(path=p, kind=kind, size=st.st_size, mtime=st.st_mtime))
        except Exception:
            pass

    for d in dump_dirs:
        add_from_dir(d, "dump", DUMP_EXTENSIONS)

    for d in log_dirs:
        add_from_dir(d, "log", TEXT_EXTENSIONS)

    files.sort(key=lambda x: x.mtime, reverse=True)
    return files[:limit]


def crash_roots_diagnostics(custom_root: str | None = None) -> str:
    roots = crash_search_roots(custom_root)
    dump_dirs, log_dirs = discover_studio_one_crash_dirs(custom_root)
    files = collect_crash_files(dump_dirs, log_dirs, limit=10000)

    lines = []
    lines.append("Crash/log roots diagnostics")
    lines.append("=" * 80)
    lines.append(f"Mode: {'manual root only' if custom_root else 'auto'}")
    lines.append(f"Roots: {len(roots)}")
    lines.append(f"Dump dirs: {len(dump_dirs)}")
    lines.append(f"Log dirs: {len(log_dirs)}")
    lines.append(f"Files: {len(files)}")
    lines.append("")
    lines.append("Roots")
    lines.append("-" * 80)
    for r in roots:
        lines.append(str(r))
    lines.append("")
    lines.append("Newest files")
    lines.append("-" * 80)
    for f in files[:50]:
        lines.append(f"{f.kind:4} | {format_bytes(f.size):>10} | {f.path}")
    return "\n".join(lines)



def _cdb_priority(path: Path) -> tuple[int, str]:
    text = str(path).lower()
    name = path.name.lower()

    if "\\amd64\\" in text or "/amd64/" in text or "\\x64\\" in text or "/x64/" in text:
        arch_rank = 0
    elif "\\x86\\" in text or "/x86/" in text:
        arch_rank = 1
    elif "\\arm64\\" in text or "/arm64/" in text:
        arch_rank = 2
    else:
        arch_rank = 3

    exe_rank = 0 if name == "cdb.exe" else 1
    return (exe_rank, arch_rank, text)


def find_cdb(custom_path: str | None = None) -> Path | None:
    if custom_path:
        p = Path(custom_path)
        if p.exists() and p.name.lower() == "cdb.exe":
            return p

    found = shutil.which("cdb.exe")
    if found:
        return Path(found)

    hits: list[Path] = []

    for candidate in CDB_CANDIDATES:
        p = Path(candidate)
        if p.exists():
            hits.append(p)

    for base in [
        Path(r"C:\Program Files (x86)\Windows Kits"),
        Path(r"C:\Program Files\Windows Kits"),
    ]:
        if base.exists():
            try:
                hits.extend(base.rglob("cdb.exe"))
            except Exception:
                pass

    for base in [
        Path(r"C:\Program Files\WindowsApps"),
        Path(r"C:\Program Files (x86)\WindowsApps"),
    ]:
        if base.exists():
            try:
                for package in base.glob("Microsoft.WinDbg*"):
                    try:
                        hits.extend(package.rglob("cdb.exe"))
                    except Exception:
                        pass
            except Exception:
                pass

    if hits:
        hits = sorted(set(hits), key=_cdb_priority)
        return hits[0]

    return None


def find_debugger_tools(custom_cdb_path: str | None = None) -> dict:
    result = {
        "cdb": None,
        "windbg": None,
        "windbgx": None,
        "searched": [],
        "notes": [],
    }

    cdb = find_cdb(custom_cdb_path)
    if cdb:
        result["cdb"] = str(cdb)

    candidates = []

    for exe in ("windbg.exe", "WinDbgX.exe", "windbgx.exe", "cdb.exe"):
        found = shutil.which(exe)
        if found:
            candidates.append(Path(found))

    for base in [
        Path(r"C:\Program Files (x86)\Windows Kits"),
        Path(r"C:\Program Files\Windows Kits"),
    ]:
        result["searched"].append(str(base))
        if base.exists():
            try:
                for exe_name in ("cdb.exe", "windbg.exe"):
                    candidates.extend(base.rglob(exe_name))
            except Exception:
                pass

    localappdata = os.environ.get("LOCALAPPDATA")
    programfiles = os.environ.get("ProgramFiles")
    programfiles_x86 = os.environ.get("ProgramFiles(x86)")

    if localappdata:
        windowsapps = Path(localappdata) / "Microsoft" / "WindowsApps"
        result["searched"].append(str(windowsapps))
        if windowsapps.exists():
            for exe_name in ("WinDbgX.exe", "windbgx.exe", "cdb.exe"):
                p = windowsapps / exe_name
                if p.exists():
                    candidates.append(p)

    for pf in [programfiles, programfiles_x86]:
        if not pf:
            continue
        wa = Path(pf) / "WindowsApps"
        result["searched"].append(str(wa))
        if wa.exists():
            try:
                for package in wa.glob("Microsoft.WinDbg*"):
                    for exe_name in ("cdb.exe", "WinDbgX.exe", "windbgx.exe", "windbg.exe"):
                        try:
                            candidates.extend(package.rglob(exe_name))
                        except Exception:
                            pass
            except Exception:
                pass

    seen = set()
    unique_candidates = []
    for p in candidates:
        try:
            key = str(p.resolve()).lower()
        except Exception:
            key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(p)

    cdb_hits = [p for p in unique_candidates if p.name.lower() == "cdb.exe"]
    if cdb_hits and not result["cdb"]:
        result["cdb"] = str(sorted(cdb_hits, key=_cdb_priority)[0])

    for p in unique_candidates:
        name = p.name.lower()
        if name == "windbg.exe" and not result["windbg"]:
            result["windbg"] = str(p)
        elif name == "windbgx.exe" and not result["windbgx"]:
            result["windbgx"] = str(p)

    if result["cdb"]:
        result["notes"].append("cdb.exe found: automated dump analysis is available.")
    else:
        result["notes"].append("cdb.exe not found: automated dump analysis is not available.")
        if result["windbg"] or result["windbgx"]:
            result["notes"].append("WinDbg is installed, but cdb.exe is missing or inaccessible.")
        result["notes"].append("Install Windows SDK component: Debugging Tools for Windows, or manually browse to cdb.exe.")

    return result


def debugger_diagnostics_text(custom_cdb_path: str | None = None) -> str:
    d = find_debugger_tools(custom_cdb_path)
    lines = []
    lines.append("Debugger diagnostics")
    lines.append("=" * 80)
    lines.append(f"cdb.exe:    {d.get('cdb') or 'NOT FOUND'}")
    lines.append("Preferred:  amd64/x64 cdb.exe for Studio One / Fender Studio Pro")
    lines.append(f"windbg.exe: {d.get('windbg') or 'not found'}")
    lines.append(f"WinDbgX:    {d.get('windbgx') or 'not found'}")
    lines.append("")
    lines.append("Notes")
    lines.append("-" * 80)
    for note in d.get("notes", []):
        lines.append(note)
    lines.append("")
    lines.append("Searched")
    lines.append("-" * 80)
    for s in d.get("searched", []):
        lines.append(s)
    return "\n".join(lines)


def run_cdb_dump_analysis(dump_path: Path, cdb_path: Path, timeout_seconds: int = 90) -> str:
    cmd = [
        str(cdb_path),
        "-z", str(dump_path),
        "-c", "!analyze -v; .ecxr; k; lm; q",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout_seconds,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def _first_regex(text: str, patterns: list[str]) -> str:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return (m.group(1) if m.groups() else m.group(0)).strip()
    return ""


def _normalize_mod_name(s: str) -> str:
    s = s.strip().strip("`'\"")
    s = s.replace("\\", "/")
    s = s.split("/")[-1]
    return s.lower()


def is_likely_system_module(mod: str) -> bool:
    m = _normalize_mod_name(mod)
    system = [
        "ntdll.dll", "kernelbase.dll", "kernel32.dll", "ucrtbase.dll",
        "vcruntime", "msvcp", "user32.dll", "gdi32.dll", "win32u.dll",
        "combase.dll", "ole32.dll", "shell32.dll", "advapi32.dll",
        "sechost.dll", "rpcrt4.dll", "studio one.exe", "studioone.exe",
    ]
    return any(x in m for x in system)


def extract_modules(text: str) -> list[str]:
    mods = []

    patterns = [
        r"\b([A-Za-z0-9_.\- ()]+\.(?:dll|vst3|vst|aaxplugin|sys|exe))\b",
        r"\b([A-Za-z0-9_.\- ()]+)![A-Za-z0-9_?$@]+",
    ]

    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            name = _normalize_mod_name(m.group(1))
            if name and name not in mods:
                mods.append(name)

    return mods[:1000]


def parse_crash_text(source_path: Path, text: str, cdb_path: Path | None = None, used_cdb: bool = False) -> CrashAnalysisResult:
    result = CrashAnalysisResult(source_path=source_path, cdb_path=cdb_path, used_cdb=used_cdb)

    result.image_name = _first_regex(text, [
        r"^\s*IMAGE_NAME:\s*(.+)$",
        r"Faulting module name:\s*([^,\r\n]+)",
        r"faulting module:\s*([^,\r\n]+)",
        r"Faulting Module:\s*([^,\r\n]+)",
    ])
    result.module_name = _first_regex(text, [
        r"^\s*MODULE_NAME:\s*(.+)$",
        r"Module Name:\s*([^,\r\n]+)",
    ])
    result.faulting_module = result.image_name or result.module_name

    result.exception_code = _first_regex(text, [
        r"^\s*EXCEPTION_CODE:\s*\((?:NTSTATUS)?\)\s*(0x[0-9a-fA-F]+|[0-9a-fA-F]{8,})",
        r"^\s*ExceptionCode:\s*(0x[0-9a-fA-F]+|[0-9a-fA-F]{8,})",
        r"Exception code:\s*(0x[0-9a-fA-F]+|[0-9a-fA-F]{8,})",
    ])
    result.exception_description = _first_regex(text, [
        r"^\s*EXCEPTION_CODE:.*-\s*(.+)$",
        r"^\s*ExceptionCode:.*\((.+)\)",
    ])
    result.exception_address = _first_regex(text, [
        r"^\s*EXCEPTION_ADDRESS:\s*(.+)$",
        r"^\s*ExceptionAddress:\s*(.+)$",
    ])
    result.process_name = _first_regex(text, [
        r"^\s*PROCESS_NAME:\s*(.+)$",
        r"Faulting application name:\s*([^,\r\n]+)",
    ])

    modules = extract_modules(text)
    result.stack_modules = modules[:120]
    result.loaded_modules = modules

    if not result.faulting_module:
        for mod in result.stack_modules:
            if not is_likely_system_module(mod):
                result.faulting_module = mod
                break

    result.hints = build_hints(result, text)
    return result


def build_hints(result: CrashAnalysisResult, text: str) -> list[str]:
    hay = (text + "\n" + " ".join(result.stack_modules + result.loaded_modules)).lower()
    hints = []

    if "pace" in hay or "ilok" in hay or "eden" in hay:
        hints.append("PACE/iLok найден в контексте. Проверить iLok License Manager, PACE drivers и плагины с iLok.")

    if "neural" in hay or "ndsp" in hay:
        hints.append("Есть след Neural DSP/NDSP. Сначала тестировать bypass/remove Neural DSP плагины.")

    if ".vst3" in hay or ".vst" in hay:
        hints.append("В дампе/логе есть VST/VST3 модуль. Если он faulting module или в верхнем stack — вероятен краш плагина.")

    if ".sys" in hay:
        hints.append("Есть .sys модуль. Если он faulting module — подозревать драйвер/интерфейс/authorization layer.")

    code = (result.exception_code or "").lower()
    if "c0000005" in code:
        hints.append("c0000005 = access violation. Типично для native plugin crash, bad driver hook или повреждённого состояния плагина.")
    elif "80000003" in code:
        hints.append("80000003 = breakpoint/assert. Может быть deliberate break внутри плагина/host.")
    elif "c0000409" in code:
        hints.append("c0000409 = fail-fast / stack buffer overrun. Часто указывает на плохой native module/plugin code.")

    if not hints:
        hints.append("Сильная сигнатура не найдена. Смотри faulting module и первые non-system модули в stack.")

    return hints


def match_plugins_to_crash(result: CrashAnalysisResult, plugins) -> list[str]:
    if not plugins:
        return []

    crash_names = set()
    for val in [result.faulting_module, result.image_name, result.module_name] + result.stack_modules[:100] + result.loaded_modules[:300]:
        if val:
            norm = _normalize_mod_name(val)
            crash_names.add(norm)
            crash_names.add(Path(norm).stem.lower())

    matches = []
    for p in plugins:
        pname = getattr(p, "plugin_name", "") or ""
        track = getattr(p, "track_name", "") or ""
        slot = getattr(p, "slot_name", "") or ""
        bypassed = getattr(p, "bypassed", False)

        clean = re.sub(r"[^a-z0-9]+", "", pname.lower())
        if not clean:
            continue

        hit = False
        for cn in crash_names:
            cclean = re.sub(r"[^a-z0-9]+", "", cn.lower())
            if not cclean:
                continue
            if clean in cclean or cclean in clean:
                hit = True
                break

        # Token fallback for names like "FabFilter Pro-Q 3" vs "Pro-Q 3.vst3"
        if not hit:
            tokens = [t for t in re.split(r"[^a-z0-9]+", pname.lower()) if len(t) >= 3]
            for cn in crash_names:
                if tokens and all(t in cn for t in tokens[:3]):
                    hit = True
                    break

        if hit:
            matches.append(f"{track} / {slot} / {pname} / bypassed={bypassed}")

    return matches[:100]


def analyze_crash_file(path: Path, plugins=None, cdb_path: str | None = None, output_dir: Path | None = None) -> CrashAnalysisResult:
    """
    cdb-free crash analysis.

    .dmp/.mdmp files are parsed directly:
    - ExceptionStream;
    - ModuleListStream;
    - SystemInfoStream;
    - MiscInfoStream.

    No WinDbg, no Windows SDK, no cdb.exe.
    """
    path = Path(path)
    output_dir = output_dir or path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    result = CrashAnalysisResult(source_path=path, used_cdb=False, cdb_path=None)

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

            result.loaded_modules = [m.name for m in mini.modules if m.name]
            result.stack_modules = [h.module.name for h in mini.all_stack_hits if h.module and h.module.name]

            if mini.error:
                result.errors.append(mini.error)

            plugin_names = []
            for p in plugins or []:
                for attr in ("plugin_name", "name", "display_name", "module_name", "vendor", "fx_name"):
                    value = getattr(p, attr, "") or ""
                    if value:
                        plugin_names.append(str(value))
            # Deduplicate, preserve order.
            seen_names = set()
            plugin_names = [
                x for x in plugin_names
                if not (x.lower() in seen_names or seen_names.add(x.lower()))
            ]

            raw = format_minidump_report(mini, plugin_names)
            # Do NOT use legacy match_plugins_to_crash() for dump analysis.
            # It can match against loaded modules and therefore mark nearly the whole project.
            # Project matching is now done inside format_minidump_report() using stack evidence only.
            result.plugin_matches = []

            if mini.faulting_module:
                result.hints.append("Fault surface module was resolved by mapping exception address to loaded module range.")
            elif mini.exception:
                result.hints.append("Exception was found, but address did not fall inside any loaded module range.")
            else:
                result.hints.append("No ExceptionStream found in this minidump.")

            if mini.exception_thread_stack_hits:
                result.hints.append("Stack module scanner found module addresses in the exception thread stack.")
            elif mini.all_stack_hits:
                result.hints.append("Stack module scanner found module addresses in other saved thread stacks.")
            else:
                result.hints.append("Stack module scanner found no module addresses in saved stacks.")

            # For dumps, the full Module Stack Viewer report is the primary report.
            # Do not hide it only in *_pure_dump_report.txt.
            raw_path = output_dir / f"{path.stem}_pure_dump_report.txt"
            raw_path.write_text(raw, encoding="utf-8", errors="replace")
            result.raw_output_path = raw_path

            main_report_path = output_dir / f"{path.stem}_crash_report.txt"
            main_report_path.write_text(raw, encoding="utf-8", errors="replace")
            result.report_path = main_report_path
            return result
        else:
            raw = path.read_text(encoding="utf-8", errors="replace")
            parsed = parse_crash_text(path, raw, cdb_path=None, used_cdb=False)
            parsed.plugin_matches = match_plugins_to_crash(parsed, plugins or [])
            result = parsed

            raw_path = output_dir / f"{path.stem}_raw_log_output.txt"
            raw_path.write_text(raw, encoding="utf-8", errors="replace")
            result.raw_output_path = raw_path

    except Exception as e:
        result.errors.append(str(e))

    report_path = output_dir / f"{path.stem}_crash_report.txt"
    try:
        report_path.write_text(make_report(result), encoding="utf-8", errors="replace")
        result.report_path = report_path
    except Exception:
        pass

    return result

def make_report(r: CrashAnalysisResult) -> str:
    # If a full built-in dump report already exists, use it as the primary report.
    if r.raw_output_path and Path(r.raw_output_path).exists():
        try:
            raw_text = Path(r.raw_output_path).read_text(encoding="utf-8", errors="replace")
            if "Dump Module Stack Viewer" in raw_text or "Pure Dump Analysis" in raw_text:
                return raw_text
        except Exception:
            pass
    lines = []
    lines.append("Studio One Project Tools - Crash Analysis Report")
    lines.append("=" * 80)
    lines.append(f"Source:          {r.source_path}")
    lines.append("Analyzer:        built-in pure dump reader")
    lines.append("")
    lines.append("Crash summary")
    lines.append("-" * 80)
    lines.append(f"Process:         {r.process_name}")
    lines.append(f"Faulting module: {r.faulting_module}")
    lines.append(f"IMAGE_NAME:      {r.image_name}")
    lines.append(f"MODULE_NAME:     {r.module_name}")
    lines.append(f"Exception code:  {r.exception_code}")
    lines.append(f"Description:     {r.exception_description}")
    lines.append(f"Address:         {r.exception_address}")
    lines.append("")
    lines.append("Plugin matches in opened .song")
    lines.append("-" * 80)
    if r.plugin_matches:
        lines.extend(r.plugin_matches)
    else:
        lines.append("No plugin name matches found against currently opened .song.")
    lines.append("")
    lines.append("Hints")
    lines.append("-" * 80)
    lines.extend(r.hints or ["No hints."])
    lines.append("")
    lines.append("Top modules")
    lines.append("-" * 80)
    for mod in r.stack_modules[:80]:
        lines.append(mod)
    lines.append("")
    if r.errors:
        lines.append("Errors")
        lines.append("-" * 80)
        lines.extend(r.errors)
        lines.append("")
    lines.append("Raw dump/log output is saved separately when possible.")
    return "\n".join(lines)


def result_to_display_text(r: CrashAnalysisResult) -> str:
    """
    GUI display output.

    For dump analysis, always show the full project/dump report when it exists.
    Never fall back to the old plugin_matches list, because that can contain the
    entire project and is not crash evidence.
    """
    for candidate in (r.report_path, r.raw_output_path):
        if candidate and Path(candidate).exists():
            try:
                text = Path(candidate).read_text(encoding="utf-8", errors="replace")
                markers = (
                    "Project Crash Match Report",
                    "Dump Module Stack Viewer",
                    "Pure Dump Analysis",
                    "Project FX matched in EXCEPTION THREAD",
                    "Top Exception Thread Non-System Modules",
                    "Top All-Thread Plugin-Path Modules",
                )
                if any(m in text for m in markers):
                    return text
            except Exception:
                pass

    lines = []
    lines.append("Crash Analysis")
    lines.append("=" * 80)
    lines.append(f"Source: {r.source_path}")
    lines.append("Analyzer: built-in pure dump reader")
    lines.append("")
    lines.append(f"Fault surface module: {r.faulting_module or 'not detected'}")
    lines.append(f"Exception: {r.exception_code or 'not detected'} {r.exception_description or ''}".strip())
    lines.append(f"Exception address: {r.exception_address or 'not detected'}")
    lines.append(f"Process: {r.process_name or 'not detected'}")
    lines.append("")
    lines.append("Project FX matches:")
    lines.append("  Full project matching is available only in the full report file.")
    lines.append("  Legacy plugin_matches output is intentionally hidden to avoid listing the whole project.")
    lines.append("")
    if r.hints:
        lines.append("Hints:")
        for h in r.hints:
            lines.append(f"  - {h}")
        lines.append("")
    if r.report_path:
        lines.append(f"Report: {r.report_path}")
    if r.raw_output_path:
        lines.append(f"Raw output: {r.raw_output_path}")
    if r.errors:
        lines.append("")
        lines.append("Errors:")
        for e in r.errors:
            lines.append(f"  - {e}")
    return "\\n".join(lines)
