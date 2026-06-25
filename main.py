from pathlib import Path
import sys
import traceback


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def write_startup_log(title: str, exc: BaseException):
    log_path = app_dir() / "startup_error_log.txt"

    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n" + "=" * 100 + "\n")
            f.write(title + "\n")
            f.write(str(exc) + "\n\n")
            f.write(traceback.format_exc())
            f.write("\n")
    except Exception:
        pass

    return log_path


def main():
    try:
        from gui.app import StudioOneProjectToolsApp

        app = StudioOneProjectToolsApp()
        app.mainloop()

    except BaseException as exc:
        log_path = write_startup_log("Startup crash", exc)

        print("\nStudio One Project Tools crashed during startup.")
        print(f"Error log: {log_path}")
        print("\nTraceback:")
        traceback.print_exc()

        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Studio One Project Tools startup crash",
                f"{exc}\n\nError log:\n{log_path}"
            )
            root.destroy()
        except Exception:
            pass

        # Keep console visible when launched from .bat
        if not getattr(sys, "frozen", False):
            try:
                input("\nPress Enter to close...")
            except Exception:
                pass


if __name__ == "__main__":
    main()
