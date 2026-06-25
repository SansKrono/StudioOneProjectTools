from pathlib import Path
import shutil
import subprocess
import sys


MODE_AUTO = "auto"
MODE_DEFAULT = "default"
MODE_15B = "1.5b"
MODE_CUSTOM = "custom"


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def find_es_exe(configured_path: str | None = None) -> Path | None:
    """
    es.exe priority:
    1. manually configured path;
    2. bundled es.exe next to the app;
    3. bundled bin/es.exe;
    4. PATH;
    5. common Everything install folders.
    """
    if configured_path:
        p = Path(configured_path).expanduser()
        if p.exists() and p.is_file():
            return p

    local_candidates = [
        app_base_dir() / "es.exe",
        app_base_dir() / "bin" / "es.exe",
        Path.cwd() / "es.exe",
    ]

    for p in local_candidates:
        if p.exists() and p.is_file():
            return p

    found = shutil.which("es.exe") or shutil.which("es")
    if found:
        p = Path(found)
        if p.exists():
            return p

    candidates = [
        Path(r"C:\Program Files\Everything\es.exe"),
        Path(r"C:\Program Files (x86)\Everything\es.exe"),
        Path(r"C:\Tools\Everything\es.exe"),
    ]

    for p in candidates:
        if p.exists() and p.is_file():
            return p

    return None


def normalize_mode(mode: str | None) -> str:
    mode = (mode or MODE_AUTO).strip().lower()
    if mode in {MODE_AUTO, "auto detect", "autodetect"}:
        return MODE_AUTO
    if mode in {MODE_DEFAULT, "1.4", "normal"}:
        return MODE_DEFAULT
    if mode in {MODE_15B, "1.5", "1.5a", "beta", "alpha"}:
        return MODE_15B
    if mode in {MODE_CUSTOM, "named", "named instance"}:
        return MODE_CUSTOM
    return MODE_AUTO


def instance_plan(mode: str | None = None, custom_instance: str | None = None) -> list[str | None]:
    mode = normalize_mode(mode)
    custom_instance = (custom_instance or "").strip()

    if mode == MODE_DEFAULT:
        return [None]

    if mode == MODE_15B:
        return ["1.5b"]

    if mode == MODE_CUSTOM:
        return [custom_instance or None]

    # Auto mode: normal Everything first, then current Everything 1.5b.
    # Keep 1.5a as a hidden legacy fallback for older beta installs.
    plan: list[str | None] = [None, "1.5b", "1.5a"]
    if custom_instance and custom_instance not in {"1.5b", "1.5a"}:
        plan.append(custom_instance)

    # Deduplicate preserving order.
    out = []
    seen = set()
    for item in plan:
        key = item or "<default>"
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def build_es_args(args: list[str], instance: str | None = None) -> list[str]:
    if instance:
        return ["-instance", instance] + args
    return list(args)


def format_cmd(cmd: list[str]) -> str:
    return " ".join(f'"{x}"' if " " in x else x for x in cmd)


def run_es_command(args: list[str], es_path: str | None = None, instance: str | None = None, timeout: int = 30) -> dict:
    exe = find_es_exe(es_path)
    if not exe:
        return {
            "ok": False,
            "exe": "",
            "cmd": "",
            "returncode": None,
            "stdout": "",
            "stderr": "es.exe not found",
            "instance": instance or "",
        }

    cmd = [str(exe)] + build_es_args(args, instance)

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        return {
            "ok": completed.returncode == 0,
            "exe": str(exe),
            "cmd": format_cmd(cmd),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "instance": instance or "",
        }
    except Exception as e:
        return {
            "ok": False,
            "exe": str(exe),
            "cmd": format_cmd(cmd),
            "returncode": None,
            "stdout": "",
            "stderr": repr(e),
            "instance": instance or "",
        }


def parse_es_stdout_paths(stdout: str) -> list[Path]:
    results = []
    for line in stdout.splitlines():
        line = line.strip().strip('"')
        if not line:
            continue
        p = Path(line)
        if p.exists():
            results.append(p)

    seen = set()
    unique = []
    for p in results:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def search_everything_by_filename(
    filename: str,
    max_results: int = 100,
    es_path: str | None = None,
    instance: str | None = None,
    mode: str | None = None,
) -> list[Path]:
    if not find_es_exe(es_path):
        return []

    queries = [filename, f'"{filename}"']
    instances = instance_plan(mode, instance)

    for inst in instances:
        for query in queries:
            result = run_es_command(
                ["-n", str(max_results), query],
                es_path=es_path,
                instance=inst,
                timeout=30,
            )
            paths = parse_es_stdout_paths(result.get("stdout", ""))
            if paths:
                return paths[:max_results]

    return []



def test_everything_available(es_path: str | None = None) -> dict:
    """
    User-facing quick test.

    Returns:
    - ok: True if es.exe runs and returns without IPC/launch errors; search result count is irrelevant;
    - exe: detected executable path;
    - cmd/stdout/stderr/returncode for diagnostics.
    """
    exe = find_es_exe(es_path)
    if not exe:
        return {
            "ok": False,
            "exe": "",
            "cmd": "",
            "returncode": None,
            "stdout": "",
            "stderr": "es.exe not found",
            "reason": "es.exe was not found.",
        }

    result = run_es_command(["-n", "1", "*"], es_path=es_path, instance=None, timeout=15)
    stdout = (result.get("stdout") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    returncode = result.get("returncode")

    if returncode == 0:
        return {
            **result,
            "ok": True,
            "reason": "es.exe started successfully and the standard compatible Everything interface responded. Search results are not required for this test.",
        }

    lower = stderr.lower()
    if "ipc" in lower or "everything" in lower or "window" in lower or "not found" in lower:
        reason = "es.exe was found, but Everything did not respond. Start Everything and try again."
    else:
        reason = "es.exe was found, but the test command returned an error."

    return {
        **result,
        "ok": False,
        "reason": reason,
    }


def diagnose_everything(es_path: str | None = None, instance: str | None = None, mode: str | None = None) -> str:
    exe = find_es_exe(es_path)
    mode = normalize_mode(mode)
    planned_instances = instance_plan(mode, instance)

    lines = []
    lines.append("Everything / ES diagnostics")
    lines.append("=" * 70)
    lines.append(f"Configured es.exe: {es_path or '<bundled/auto>'}")
    lines.append(f"Detected es.exe:   {exe if exe else 'NOT FOUND'}")
    lines.append(f"Mode:              {mode}")
    lines.append(f"Custom instance:   {instance or '<empty>'}")
    lines.append(f"Instance plan:     {', '.join(i or '<default>' for i in planned_instances)}")
    lines.append(f"Current dir:       {Path.cwd()}")
    lines.append(f"App base dir:      {app_base_dir()}")
    lines.append("")

    tests = [
        ["-n", "5", "*.wav"],
        ["-n", "5", "*.song"],
        ["-n", "5", "wav"],
    ]

    for inst in planned_instances:
        lines.append("")
        lines.append(f"INSTANCE TEST: {inst or '<default>'}")
        lines.append("=" * 70)

        for args in tests:
            result = run_es_command(args, es_path=es_path, instance=inst, timeout=20)
            lines.append("Command:")
            lines.append(result["cmd"])
            lines.append(f"Return code: {result['returncode']}")
            lines.append("STDOUT:")
            stdout = result["stdout"].strip()
            lines.append(stdout if stdout else "<empty>")
            lines.append("STDERR:")
            stderr = result["stderr"].strip()
            lines.append(stderr if stderr else "<empty>")
            lines.append("-" * 70)

    return "\n".join(lines)
