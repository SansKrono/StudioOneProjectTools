"""
Thin wrapper around Everything's command-line client (es.exe).

Only the standard Everything IPC interface is used (no -instance switching).
es.exe is only ever looked up in trusted locations: an explicitly configured
path, the app folder, absolute PATH entries and Program Files. The current
working directory is never searched, so a stray es.exe next to a downloaded
project cannot be executed by accident.
"""

from pathlib import Path
import os
import subprocess

from core.common import app_dir


ES_EXE = "es.exe"

# Hide the console window es.exe would otherwise flash on every call.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _is_file(p: Path) -> bool:
    try:
        return p.is_file()
    except OSError:
        return False


def _search_path_for(exe_name: str) -> Path | None:
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not os.path.isabs(entry):
            continue
        candidate = Path(entry) / exe_name
        if _is_file(candidate):
            return candidate
    return None


def find_es_exe(configured_path: str | None = None) -> Path | None:
    """
    es.exe lookup order:
    1. explicitly configured path (must be absolute);
    2. es.exe bundled next to the app, or in its bin/ folder;
    3. absolute PATH entries;
    4. the default Everything install folders under Program Files.
    """
    if configured_path:
        p = Path(configured_path).expanduser()
        if p.is_absolute() and _is_file(p):
            return p

    for p in (app_dir() / ES_EXE, app_dir() / "bin" / ES_EXE):
        if _is_file(p):
            return p

    found = _search_path_for(ES_EXE)
    if found:
        return found

    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            p = Path(base) / "Everything" / ES_EXE
            if _is_file(p):
                return p

    return None


def format_cmd(cmd: list[str]) -> str:
    return " ".join(f'"{x}"' if " " in x else x for x in cmd)


def run_es_command(args: list[str], es_path: str | None = None, timeout: int = 30) -> dict:
    exe = find_es_exe(es_path)
    if not exe:
        return {"ok": False, "exe": "", "cmd": "", "returncode": None, "stdout": "", "stderr": "es.exe not found"}

    cmd = [str(exe)] + list(args)

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
        return {
            "ok": completed.returncode == 0,
            "exe": str(exe),
            "cmd": format_cmd(cmd),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "exe": str(exe), "cmd": format_cmd(cmd), "returncode": None, "stdout": "", "stderr": repr(e)}


def parse_es_stdout_paths(stdout: str) -> list[Path]:
    unique = []
    seen = set()
    for line in stdout.splitlines():
        line = line.strip().strip('"')
        if not line:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(Path(line))
    return unique


def search_everything_by_filename(filename: str, max_results: int = 12, es_path: str | None = None) -> list[Path]:
    """
    Return existing files whose name is exactly filename (case-insensitive).

    The query is always quoted: it is a literal phrase for Everything, and it can
    never be mistaken for an es.exe command-line option even if the name comes
    from an untrusted .song file and starts with "-". Everything may return
    names that only contain the phrase, so a larger raw result window is fetched
    and filtered down to exact matches.
    """
    filename = filename.replace('"', "").strip()
    if not filename:
        return []

    raw_window = str(max(max_results * 10, 100))
    result = run_es_command(["-n", raw_window, f'"{filename}"'], es_path=es_path)

    wanted = filename.lower()
    matches = []
    for p in parse_es_stdout_paths(result.get("stdout", "")):
        if p.name.lower() == wanted and _is_file(p):
            matches.append(p)
            if len(matches) >= max_results:
                break
    return matches


def test_everything_available(es_path: str | None = None) -> dict:
    """
    Quick user-facing test: ok is True if es.exe runs and Everything answers.
    The number of search results is irrelevant.
    """
    exe = find_es_exe(es_path)
    if not exe:
        return {"ok": False, "exe": "", "cmd": "", "returncode": None, "stdout": "", "stderr": "es.exe not found",
                "reason": "es.exe was not found."}

    result = run_es_command(["-n", "1", "*"], es_path=es_path, timeout=15)

    if result.get("returncode") == 0:
        return {**result, "ok": True, "reason": "es.exe started and Everything responded. Search results are not required for this test."}

    lower = (result.get("stderr") or "").lower()
    if "ipc" in lower or "everything" in lower or "window" in lower or "not found" in lower:
        reason = "es.exe was found, but Everything did not respond. Start Everything and try again."
    else:
        reason = "es.exe was found, but the test command returned an error."

    return {**result, "ok": False, "reason": reason}


def diagnose_everything(es_path: str | None = None) -> str:
    exe = find_es_exe(es_path)

    lines = [
        "Everything / es.exe diagnostics",
        "=" * 70,
        f"Configured es.exe: {es_path or '<auto>'}",
        f"Detected es.exe:   {exe if exe else 'NOT FOUND'}",
        f"App folder:        {app_dir()}",
        "",
    ]

    for args in (["-n", "5", "*.wav"], ["-n", "5", "*.song"]):
        result = run_es_command(args, es_path=es_path, timeout=20)
        lines += [
            "Command:",
            result["cmd"],
            f"Return code: {result['returncode']}",
            "STDOUT:",
            result["stdout"].strip() or "<empty>",
            "STDERR:",
            result["stderr"].strip() or "<empty>",
            "-" * 70,
        ]

    return "\n".join(lines)
