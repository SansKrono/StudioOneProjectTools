import tkinter as tk
import os
import re
import traceback
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import zipfile
import threading
import queue

from core.song_archive import is_song_file
from core.plugin_manager import parse_plugins, remove_bypassed_fx, modify_selected_fx
from core.audio_manager import build_audio_table_rows, format_size
from core.consolidate import move_file_to_folder, consolidate_project_to_new_folder
from core.report_generator import export_txt_report, export_csv_report
from core.missing_files import search_missing_candidates_everything, recover_missing_to_media_and_relink
from core.everything import find_es_exe, diagnose_everything, test_everything_available
from core.settings_manager import load_settings, save_settings
from core.crash_analyzer import (
    discover_studio_one_crash_dirs,
    collect_crash_files,
    analyze_crash_file,
    format_bytes as format_crash_size,
    result_to_display_text,
    crash_roots_diagnostics,
)


class StudioOneProjectToolsApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Studio One Project Tools v2")
        self.geometry("1050x700")
        self.minsize(900, 560)

        self.song_path: Path | None = None
        self.project_dir: Path | None = None

        self.plugins = []
        self.audio_rows = []
        self.missing_candidates = []

        self.consolidate_running = False
        self.consolidate_queue = queue.Queue()
        self.consolidate_worker = None
        self.consolidate_total_bytes = 0
        self.consolidate_copied_bytes = 0
        self.consolidate_current_file_bytes = 0
        self.consolidate_current_file_total = 0
        self.consolidate_last_result = None

        self.batch_projects = []
        self.batch_running = False
        self.batch_queue = queue.Queue()
        self.batch_worker = None

        self.crash_dump_dirs = []
        self.crash_log_dirs = []
        self.crash_files = []
        self.crash_sort_key = "mtime"
        self.crash_sort_reverse = True

        self.audio_filter = "ALL"
        self.audio_sort_key = None
        self.audio_sort_reverse = False

        self.plugin_filter_mode = "all"
        self.plugin_filter_indices = set()
        self.plugin_filter_tracks = set()
        self.plugin_sort_key = "track"
        self.plugin_sort_reverse = False

        self.settings = load_settings()

        self.audio_search_var = tk.StringVar()

        configured_es = self.settings.get("everything_es_path", "")
        detected_es = find_es_exe(configured_es or None)
        self.everything_es_path_var = tk.StringVar(value=str(detected_es) if detected_es else configured_es)

        self.everything_instance_var = tk.StringVar(value=self.settings.get("everything_instance", ""))
        self.studio_one_data_root_var = tk.StringVar(value=self.settings.get("studio_one_data_root", ""))

        self._build_ui()

    # -------------------------
    # Error handling
    # -------------------------

    def log_error(self, title: str, error: Exception):
        try:
            log_path = Path.cwd() / "error_log.txt"
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n" + "=" * 80 + "\n")
                f.write(title + "\n")
                f.write(str(error) + "\n")
                f.write(traceback.format_exc())
                f.write("\n")
        except Exception:
            pass

    def show_error(self, title: str, error: Exception):
        self.log_error(title, error)
        messagebox.showerror(title, f"{error}\n\nDetails saved to error_log.txt")

    # -------------------------
    # UI
    # -------------------------

    def _build_ui(self):
        self._build_menu()

        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)

        ttk.Button(top, text="Open .song", command=self.open_song).pack(side="left")
        self.project_label = ttk.Label(top, text="No project loaded")
        self.project_label.pack(side="left", padx=14)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.dashboard_frame = ttk.Frame(self.notebook)
        self.plugins_frame = ttk.Frame(self.notebook)
        self.audio_frame = ttk.Frame(self.notebook)
        self.consolidate_frame = ttk.Frame(self.notebook)
        self.batch_frame = ttk.Frame(self.notebook)
        self.crash_frame = ttk.Frame(self.notebook)
        self.settings_frame = ttk.Frame(self.notebook)

        self.notebook.add(self.dashboard_frame, text="Dashboard")
        self.notebook.add(self.plugins_frame, text="Plugins")
        self.notebook.add(self.audio_frame, text="Audio / Files")
        self.notebook.add(self.consolidate_frame, text="Consolidate")
        self.notebook.add(self.batch_frame, text="Batch Consolidate")
        self.notebook.add(self.crash_frame, text="Crash Analyzer")
        self.notebook.add(self.settings_frame, text="Settings")

        self._build_dashboard()
        self._build_plugins_page()
        self._build_audio_page()
        self._build_consolidate_page()
        self._build_batch_consolidate_page()
        self._build_crash_analyzer_page()
        self._build_settings_page()

    def _build_menu(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Open .song", command=self.open_song)
        file_menu.add_separator()
        file_menu.add_command(label="Export TXT Report", command=self.export_txt_report_dialog)
        file_menu.add_command(label="Export CSV Report", command=self.export_csv_report_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Settings", command=lambda: self.notebook.select(self.settings_frame))
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)

        menubar.add_cascade(label="File", menu=file_menu)
        self.config(menu=menubar)

    def _build_dashboard(self):
        top = ttk.Frame(self.dashboard_frame)
        top.pack(fill="x", padx=10, pady=(10, 0))

        ttk.Button(top, text="Export TXT Report", command=self.export_txt_report_dialog).pack(side="left")
        ttk.Button(top, text="Export CSV Report", command=self.export_csv_report_dialog).pack(side="left", padx=(8, 0))

        self.dashboard_text = tk.Text(self.dashboard_frame, wrap="word", height=20)
        self.dashboard_text.pack(fill="both", expand=True, padx=10, pady=10)
        self._set_dashboard_text("Open a Studio One .song project to analyze it.")

    def _build_plugins_page(self):
        toolbar = ttk.Frame(self.plugins_frame)
        toolbar.pack(fill="x", padx=8, pady=6)

        ttk.Button(toolbar, text="Refresh", command=self.analyze_project).pack(side="left")
        ttk.Button(toolbar, text="Expand all", command=self.expand_all_plugin_tracks).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Collapse all", command=self.collapse_all_plugin_tracks).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Bypassed FX list", command=self.show_bypassed_fx_list).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Show all", command=self.clear_plugin_filter).pack(side="left", padx=(8, 0))

        ttk.Label(toolbar, text="Search:").pack(side="left", padx=(18, 4))
        self.plugin_search_var = tk.StringVar()
        self.plugin_search_var.trace_add("write", lambda *_: self.populate_plugins())
        ttk.Entry(toolbar, textvariable=self.plugin_search_var, width=26).pack(side="left")

        ttk.Button(toolbar, text="Sel. FX", command=self.filter_selected_fx).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Sel. tracks", command=self.filter_selected_tracks).pack(side="left", padx=(4, 0))
        ttk.Button(toolbar, text="Select visible", command=self.select_all_visible_plugins).pack(side="left", padx=(4, 0))

        frame = ttk.Frame(self.plugins_frame)
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        columns = ("slot", "plugin", "state", "index")
        self.plugins_tree = ttk.Treeview(
            frame,
            columns=columns,
            show="tree headings",
            displaycolumns=("slot", "plugin", "state"),
            selectmode="extended",
        )

        self.plugins_tree.heading("#0", text="Track / Channel", command=lambda: self.sort_plugins_by("track"))
        self.plugins_tree.heading("slot", text="Slot", command=lambda: self.sort_plugins_by("slot"))
        self.plugins_tree.heading("plugin", text="Plug-in", command=lambda: self.sort_plugins_by("plugin"))
        self.plugins_tree.heading("state", text="State", command=lambda: self.sort_plugins_by("bypass"))

        self.plugins_tree.column("#0", width=420, anchor="w")
        self.plugins_tree.column("slot", width=75, anchor="center")
        self.plugins_tree.column("plugin", width=410, anchor="w")
        self.plugins_tree.column("state", width=85, anchor="center")
        self.plugins_tree.column("index", width=0, stretch=False)

        self.plugins_tree.tag_configure("bypassed", foreground="red")
        self.plugins_tree.tag_configure("track_all_bypassed", foreground="red")
        self.plugins_tree.tag_configure("track_some_bypassed", foreground="#b06a00")
        self.plugins_tree.tag_configure("active", foreground="black")

        y = ttk.Scrollbar(frame, orient="vertical", command=self.plugins_tree.yview)
        x = ttk.Scrollbar(frame, orient="horizontal", command=self.plugins_tree.xview)
        self.plugins_tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)

        self.plugins_tree.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.plugins_tree.bind("<Control-a>", self.select_all_visible_plugins_event)
        self.plugins_tree.bind("<Control-A>", self.select_all_visible_plugins_event)
        self.plugins_tree.bind("<Button-3>", self.show_plugins_context_menu)

        self.plugins_context_menu = tk.Menu(self, tearoff=False)
        self.plugins_context_menu.add_command(label="Remove selected FX", command=lambda: self.modify_selected_fx_action("remove"))
        self.plugins_context_menu.add_command(label="Bypass selected FX", command=lambda: self.modify_selected_fx_action("bypass"))
        self.plugins_context_menu.add_command(label="Unbypass selected FX", command=lambda: self.modify_selected_fx_action("unbypass"))
        self.plugins_context_menu.add_separator()
        self.plugins_context_menu.add_command(label="Filter by this plug-in", command=self.context_filter_by_plugin)
        self.plugins_context_menu.add_command(label="Filter by this track", command=self.context_filter_by_track)
        self.plugins_context_menu.add_command(label="Clear filter", command=self.clear_plugin_filter)
        self.plugins_context_menu.add_separator()
        self.plugins_context_menu.add_command(label="Copy plug-in name", command=self.copy_selected_plugin_name)
        self.plugins_context_menu.add_command(label="Copy track name", command=self.copy_selected_track_name)

    def _build_audio_page(self):
        toolbar_outer = ttk.Frame(self.audio_frame)
        toolbar_outer.pack(fill="x", padx=8, pady=(6, 3))

        filters = ttk.Frame(toolbar_outer)
        filters.pack(fill="x")

        ttk.Label(filters, text="Filter:").pack(side="left")
        ttk.Button(filters, text="All", width=7, command=lambda: self.set_audio_filter("ALL")).pack(side="left", padx=(6, 0))
        ttk.Button(filters, text="Normal", width=8, command=lambda: self.set_audio_filter("NORMAL")).pack(side="left", padx=(4, 0))
        ttk.Button(filters, text="Ext", width=6, command=lambda: self.set_audio_filter("EXTERNAL")).pack(side="left", padx=(4, 0))
        ttk.Button(filters, text="Dup", width=6, command=lambda: self.set_audio_filter("DUPLICATE")).pack(side="left", padx=(4, 0))
        ttk.Button(filters, text="Unused", width=8, command=lambda: self.set_audio_filter("UNUSED")).pack(side="left", padx=(4, 0))
        ttk.Button(filters, text="Miss", width=6, command=lambda: self.set_audio_filter("MISSING")).pack(side="left", padx=(4, 0))

        ttk.Label(filters, text="Search:").pack(side="left", padx=(14, 4))
        self.audio_search_var.trace_add("write", lambda *_: self.populate_audio())
        ttk.Entry(filters, textvariable=self.audio_search_var, width=30).pack(side="left")
        ttk.Button(filters, text="Clear", width=7, command=self.clear_audio_search_filter).pack(side="left", padx=(4, 0))

        actions = ttk.Frame(toolbar_outer)
        actions.pack(fill="x", pady=(4, 0))

        ttk.Button(actions, text="Restore missing files", width=22, command=self.restore_missing_files_one_click).pack(side="left")
        ttk.Button(actions, text="Move selected", width=13, command=self.move_selected_audio).pack(side="left", padx=(12, 0))
        ttk.Button(actions, text="Move duplicates", width=15, command=self.move_duplicate_audio).pack(side="left", padx=(4, 0))
        ttk.Button(actions, text="Move unused", width=13, command=self.move_unused_audio).pack(side="left", padx=(4, 0))
        ttk.Label(actions, text="Manual recovery tools are in right-click menu").pack(side="left", padx=(14, 0))

        tree_frame = ttk.Frame(self.audio_frame)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(3, 0))

        columns = ("status", "size", "folder", "file", "ref", "candidate")
        self.audio_tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            displaycolumns=("status", "size", "folder", "file"),
            selectmode="extended",
        )

        for col, title, width, anchor in [
            ("status", "Status", 95, "center"),
            ("size", "Size", 95, "e"),
            ("folder", "Folder / Found candidate", 560, "w"),
            ("file", "File", 280, "w"),
        ]:
            self.audio_tree.heading(col, text=title, command=lambda c=col: self.sort_audio_by(c))
            self.audio_tree.column(col, width=width, anchor=anchor)

        self.audio_tree.column("ref", width=0, stretch=False)
        self.audio_tree.column("candidate", width=0, stretch=False)

        self.audio_tree.tag_configure("NORMAL", foreground="black")
        self.audio_tree.tag_configure("EXTERNAL", foreground="blue")
        self.audio_tree.tag_configure("UNUSED", foreground="#b36b00")
        self.audio_tree.tag_configure("DUPLICATE", foreground="#a000c8")
        self.audio_tree.tag_configure("MISSING", foreground="red")

        self.audio_tree.bind("<Control-a>", self.select_all_audio)
        self.audio_tree.bind("<Control-A>", self.select_all_audio)
        self.audio_tree.bind("<Button-3>", self.show_audio_context_menu)
        self.audio_tree.bind("<Double-1>", lambda event: self.open_selected_audio_location())

        y = ttk.Scrollbar(tree_frame, orient="vertical")
        x = ttk.Scrollbar(tree_frame, orient="horizontal")

        self.audio_tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        y.configure(command=self.audio_tree.yview)
        x.configure(command=self.audio_tree.xview)

        self.audio_tree.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")

        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        bottom = ttk.Frame(self.audio_frame)
        bottom.pack(fill="x", padx=8, pady=6)

        self.audio_stats_label = ttk.Label(bottom, text="Audio: no project loaded")
        self.audio_stats_label.pack(side="left")

        self.audio_context_menu = tk.Menu(self, tearoff=False)
        self.audio_context_menu.add_command(label="Open location", command=self.open_selected_audio_location)
        self.audio_context_menu.add_command(label="Copy path", command=self.copy_selected_audio_path)
        self.audio_context_menu.add_command(label="Copy filename", command=self.copy_selected_audio_filename)
        self.audio_context_menu.add_separator()
        self.audio_context_menu.add_command(label="Restore missing files", command=self.restore_missing_files_one_click)
        self.audio_context_menu.add_command(label="Find missing files only", command=self.search_missing_everything)
        self.audio_context_menu.add_command(label="Restore selected missing", command=self.recover_selected_audio_missing_candidates)
        self.audio_context_menu.add_command(label="Restore all found missing", command=self.recover_all_found_missing_candidates)
        self.audio_context_menu.add_separator()
        self.audio_context_menu.add_command(label="Show only this status", command=self.audio_filter_by_selected_status)
        self.audio_context_menu.add_command(label="Show selected only", command=self.show_selected_audio_only)
        self.audio_context_menu.add_command(label="Clear filter", command=self.clear_audio_search_filter)
        self.audio_context_menu.add_separator()
        self.audio_context_menu.add_command(label="Move selected to folder", command=self.move_selected_audio)
        self.audio_context_menu.add_command(label="Move duplicate side files", command=self.move_duplicate_audio)
        self.audio_context_menu.add_command(label="Move unused files", command=self.move_unused_audio)

    def _build_consolidate_page(self):
        outer = ttk.Frame(self.consolidate_frame)
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        ttk.Label(outer, text="Consolidate Project", font=("Segoe UI", 16, "bold")).pack(anchor="w")

        self.consolidate_copy_all_referenced = tk.BooleanVar(value=True)
        self.consolidate_recover_missing = tk.BooleanVar(value=True)
        self.consolidate_copy_duplicates = tk.BooleanVar(value=True)
        self.consolidate_copy_unused = tk.BooleanVar(value=False)
        self.consolidate_remove_bypassed = tk.BooleanVar(value=False)

        options = [
            ("Copy all referenced audio into new Media folder", self.consolidate_copy_all_referenced),
            ("Recover safe FOUND missing audio into new Media folder", self.consolidate_recover_missing),
            ("Copy non-referenced duplicate files to _Duplicates", self.consolidate_copy_duplicates),
            ("Copy unused audio files to _Unused", self.consolidate_copy_unused),
            ("Also create separate no-bypassed-FX .song copy after Consolidate", self.consolidate_remove_bypassed),
        ]

        for text, var in options:
            ttk.Checkbutton(outer, text=text, variable=var).pack(anchor="w", pady=3)

        ttk.Button(outer, text="Start Consolidation", command=self.consolidate_project).pack(anchor="w", pady=14)

        self.consolidate_status_var = tk.StringVar(value="Ready.")
        ttk.Label(outer, textvariable=self.consolidate_status_var, wraplength=900).pack(anchor="w", pady=(0, 6))

        self.consolidate_progress = ttk.Progressbar(outer, orient="horizontal", mode="determinate", maximum=100)
        self.consolidate_progress.pack(fill="x", pady=(0, 6))

        self.consolidate_detail_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.consolidate_detail_var, wraplength=900).pack(anchor="w", pady=(0, 8))

        help_text = (
            "Creates a NEW autonomous project folder. Original project is not modified. "
            "All existing referenced audio is copied to Media and the new .song is relinked to local Media paths. Optional bypassed-FX cleanup creates a second separate .song copy, leaving the main consolidated file untouched. "
            "If missing files exist, Consolidate automatically runs Everything search and recovers safe FOUND matches."
        )
        ttk.Label(outer, text=help_text, wraplength=900).pack(anchor="w", pady=(4, 0))


    def _build_batch_consolidate_page(self):
        outer = ttk.Frame(self.batch_frame)
        outer.pack(fill="both", expand=True, padx=12, pady=12)

        ttk.Label(outer, text="Batch Consolidate", font=("Segoe UI", 16, "bold")).pack(anchor="w")

        self.batch_input_var = tk.StringVar()
        self.batch_output_var = tk.StringVar()
        self.batch_recursive_var = tk.BooleanVar(value=True)
        self.batch_ignore_autosaves_var = tk.BooleanVar(value=True)
        self.batch_create_no_bypassed_var = tk.BooleanVar(value=False)
        self.batch_recover_missing_var = tk.BooleanVar(value=True)
        self.batch_copy_unused_var = tk.BooleanVar(value=False)
        self.batch_copy_duplicates_var = tk.BooleanVar(value=True)

        source_box = ttk.LabelFrame(outer, text="Folders")
        source_box.pack(fill="x", pady=(10, 8))

        r1 = ttk.Frame(source_box)
        r1.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(r1, text="Input folder:", width=14).pack(side="left")
        ttk.Entry(r1, textvariable=self.batch_input_var).pack(side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Button(r1, text="Browse...", command=self.browse_batch_input_folder).pack(side="left")

        r2 = ttk.Frame(source_box)
        r2.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Label(r2, text="Output folder:", width=14).pack(side="left")
        ttk.Entry(r2, textvariable=self.batch_output_var).pack(side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Button(r2, text="Browse...", command=self.browse_batch_output_folder).pack(side="left")

        options = ttk.LabelFrame(outer, text="Options")
        options.pack(fill="x", pady=(0, 8))

        ttk.Checkbutton(options, text="Recursive search for .song files", variable=self.batch_recursive_var).pack(anchor="w", padx=10, pady=2)
        ttk.Checkbutton(options, text="Ignore autosaves / backups / history / recovery .song files", variable=self.batch_ignore_autosaves_var).pack(anchor="w", padx=10, pady=2)
        ttk.Checkbutton(options, text="Recover safe missing files", variable=self.batch_recover_missing_var).pack(anchor="w", padx=10, pady=2)
        ttk.Checkbutton(options, text="Copy duplicate side files to _Duplicates", variable=self.batch_copy_duplicates_var).pack(anchor="w", padx=10, pady=2)
        ttk.Checkbutton(options, text="Copy unused files to _Unused", variable=self.batch_copy_unused_var).pack(anchor="w", padx=10, pady=2)
        ttk.Checkbutton(options, text="Also create separate no-bypassed-FX .song copy for each project", variable=self.batch_create_no_bypassed_var).pack(anchor="w", padx=10, pady=(2, 8))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(0, 8))
        ttk.Button(buttons, text="Scan projects", command=self.scan_batch_projects).pack(side="left")
        ttk.Button(buttons, text="Start Batch Consolidate", command=self.start_batch_consolidate).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Open output folder", command=self.open_batch_output_folder).pack(side="left", padx=(8, 0))

        self.batch_status_var = tk.StringVar(value="Ready.")
        ttk.Label(outer, textvariable=self.batch_status_var, wraplength=980).pack(anchor="w", pady=(0, 4))

        self.batch_progress = ttk.Progressbar(outer, orient="horizontal", mode="determinate", maximum=100)
        self.batch_progress.pack(fill="x", pady=(0, 6))

        self.batch_detail_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.batch_detail_var, wraplength=980).pack(anchor="w", pady=(0, 8))

        tree_frame = ttk.Frame(outer)
        tree_frame.pack(fill="both", expand=True)

        columns = ("status", "audio", "missing", "output")
        self.batch_tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", selectmode="extended")
        self.batch_tree.heading("#0", text="Project")
        self.batch_tree.heading("status", text="Status")
        self.batch_tree.heading("audio", text="Audio")
        self.batch_tree.heading("missing", text="Missing")
        self.batch_tree.heading("output", text="Output")

        self.batch_tree.column("#0", width=360, anchor="w")
        self.batch_tree.column("status", width=160, anchor="center")
        self.batch_tree.column("audio", width=80, anchor="center")
        self.batch_tree.column("missing", width=80, anchor="center")
        self.batch_tree.column("output", width=420, anchor="w")

        y = ttk.Scrollbar(tree_frame, orient="vertical", command=self.batch_tree.yview)
        x = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.batch_tree.xview)
        self.batch_tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)

        self.batch_tree.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        help_text = (
            "Batch uses the stable PRESERVE_FOLDERS consolidate engine. "
            "Each original project is untouched. Main consolidated .song keeps the folder structure; "
            "optional no-bypassed-FX copies are created separately."
        )
        ttk.Label(outer, text=help_text, wraplength=980).pack(anchor="w", pady=(8, 0))


    def _build_crash_analyzer_page(self):
        outer = ttk.Frame(self.crash_frame)
        outer.pack(fill="both", expand=True, padx=12, pady=12)

        ttk.Label(outer, text="Crash Analyzer", font=("Segoe UI", 16, "bold")).pack(anchor="w")

        top = ttk.Frame(outer)
        top.pack(fill="x", pady=(10, 6))

        ttk.Button(top, text="Refresh Studio One crash files", command=self.refresh_crash_files).pack(side="left")
        ttk.Button(top, text="Diagnose roots", command=self.diagnose_crash_roots).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Analyze selected", command=self.analyze_selected_crash_file).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Analyze latest crash", command=self.analyze_latest_crash_file).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Open dump folder", command=self.open_first_dump_folder).pack(side="left", padx=(16, 0))
        ttk.Button(top, text="Open logs folder", command=self.open_first_log_folder).pack(side="left", padx=(6, 0))
        ttk.Button(top, text="Open report folder", command=self.open_crash_report_folder).pack(side="left", padx=(6, 0))

        self.crash_status_var = tk.StringVar(value="Ready. Open a .song first for plugin matching, then refresh crash files.")
        ttk.Label(outer, textvariable=self.crash_status_var, wraplength=980).pack(anchor="w", pady=(0, 6))

        root_box = ttk.LabelFrame(outer, text="Studio One data folder")
        root_box.pack(fill="x", pady=(0, 8))
        root_row = ttk.Frame(root_box)
        root_row.pack(fill="x", padx=8, pady=6)
        ttk.Label(root_row, text="Root:", width=9).pack(side="left")
        ttk.Entry(root_row, textvariable=self.studio_one_data_root_var).pack(side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Button(root_row, text="Select exact folder...", command=self.select_studio_one_data_root).pack(side="left")
        ttk.Button(root_row, text="Auto", command=self.clear_studio_one_data_root).pack(side="left", padx=(6, 0))
        ttk.Label(root_box, text="Auto checks %LOCALAPPDATA%\\CrashDumps first, then Fender Studio Pro and Studio One roots. Manual root scans only selected folder.").pack(anchor="w", padx=8, pady=(0, 6))

        ttk.Label(root_box, text="Dump analysis is built in: no cdb.exe, no WinDbg, no Windows SDK.").pack(anchor="w", padx=8, pady=(0, 6))

        split = ttk.PanedWindow(outer, orient="vertical")
        split.pack(fill="both", expand=True)

        files_frame = ttk.Frame(split)
        result_frame = ttk.Frame(split)
        split.add(files_frame, weight=1)
        split.add(result_frame, weight=2)

        columns = ("kind", "size", "modified", "mtime", "path")
        self.crash_tree = ttk.Treeview(
            files_frame,
            columns=columns,
            show="tree headings",
            displaycolumns=("kind", "size", "modified", "path"),
            selectmode="browse",
        )
        self.crash_tree.heading("#0", text="File", command=lambda: self.sort_crash_files_by("file"))
        self.crash_tree.heading("kind", text="Kind", command=lambda: self.sort_crash_files_by("kind"))
        self.crash_tree.heading("size", text="Size", command=lambda: self.sort_crash_files_by("size"))
        self.crash_tree.heading("modified", text="Modified", command=lambda: self.sort_crash_files_by("mtime"))
        self.crash_tree.heading("path", text="Path", command=lambda: self.sort_crash_files_by("path"))

        self.crash_tree.column("#0", width=260, anchor="w")
        self.crash_tree.column("kind", width=70, anchor="center")
        self.crash_tree.column("size", width=90, anchor="e")
        self.crash_tree.column("modified", width=160, anchor="center")
        self.crash_tree.column("mtime", width=0, stretch=False)
        self.crash_tree.column("path", width=520, anchor="w")

        y = ttk.Scrollbar(files_frame, orient="vertical", command=self.crash_tree.yview)
        x = ttk.Scrollbar(files_frame, orient="horizontal", command=self.crash_tree.xview)
        self.crash_tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        self.crash_tree.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        files_frame.rowconfigure(0, weight=1)
        files_frame.columnconfigure(0, weight=1)
        self.crash_tree.bind("<Double-1>", lambda event: self.analyze_selected_crash_file())

        result_toolbar = ttk.Frame(result_frame)
        result_toolbar.pack(fill="x", pady=(8, 4))
        ttk.Button(result_toolbar, text="Copy result", command=self.copy_crash_result).pack(side="left")
        ttk.Button(result_toolbar, text="Highlight hiscore", command=self.apply_crash_result_highlighting).pack(side="left", padx=(8, 0))
        ttk.Button(result_toolbar, text="Open selected file location", command=self.open_selected_crash_location).pack(side="left", padx=(6, 0))

        text_frame = ttk.Frame(result_frame)
        text_frame.pack(fill="both", expand=True)

        self.crash_result_text = tk.Text(text_frame, wrap="word", height=12)
        result_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.crash_result_text.yview)
        self.crash_result_text.configure(yscrollcommand=result_scroll.set)
        self.crash_result_text.grid(row=0, column=0, sticky="nsew")
        self.crash_result_text.tag_configure("crash_hiscore", foreground="#b00000")
        self.crash_result_text.tag_configure("crash_section", foreground="#003366")
        result_scroll.grid(row=0, column=1, sticky="ns")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        self.refresh_crash_files()

    def _build_settings_page(self):
        outer = ttk.Frame(self.settings_frame)
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        ttk.Label(outer, text="Settings", font=("Segoe UI", 16, "bold")).pack(anchor="w")

        everything_box = ttk.LabelFrame(outer, text="Everything / missing file search")
        everything_box.pack(fill="x", pady=(14, 8))

        row = ttk.Frame(everything_box)
        row.pack(fill="x", padx=10, pady=(10, 6))

        ttk.Label(row, text="es.exe:", width=18).pack(side="left")
        ttk.Entry(row, textvariable=self.everything_es_path_var).pack(side="left", fill="x", expand=True, padx=(8, 8))
        ttk.Button(row, text="Browse...", command=self.browse_everything_es_path).pack(side="left")
        ttk.Button(row, text="Auto-detect", command=self.autodetect_everything_es_path).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="Test", command=self.test_everything_es_path).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="Save", command=self.save_app_settings).pack(side="left", padx=(6, 0))

        help_text = (
            "Bundled es.exe is detected automatically at startup. "
            "The tool uses the standard Everything-compatible ES interface without -instance."
        )
        ttk.Label(everything_box, text=help_text, wraplength=900).pack(anchor="w", padx=10, pady=(0, 10))

        folders_box = ttk.LabelFrame(outer, text="Project folders")
        folders_box.pack(fill="x", pady=(10, 8))

        self.media_folder_var = tk.StringVar(value=self.settings.get("media_folder_name", "Media"))
        self.duplicates_folder_var = tk.StringVar(value=self.settings.get("duplicates_folder_name", "_Duplicates"))
        self.unused_folder_var = tk.StringVar(value=self.settings.get("unused_folder_name", "_Unused"))

        for label, var in [
            ("Media folder:", self.media_folder_var),
            ("Duplicates folder:", self.duplicates_folder_var),
            ("Unused folder:", self.unused_folder_var),
        ]:
            r = ttk.Frame(folders_box)
            r.pack(fill="x", padx=10, pady=4)
            ttk.Label(r, text=label, width=18).pack(side="left")
            ttk.Entry(r, textvariable=var, width=40).pack(side="left")

        ttk.Button(folders_box, text="Save folder settings", command=self.save_app_settings).pack(anchor="w", padx=10, pady=10)

    # -------------------------
    # Project load/analyze
    # -------------------------

    def open_song(self):
        filename = filedialog.askopenfilename(
            title="Open Studio One .song",
            filetypes=[("Studio One song", "*.song"), ("All files", "*.*")]
        )

        if not filename:
            return

        path = Path(filename)

        if not is_song_file(path):
            messagebox.showerror("Invalid file", "This is not a valid Studio One .song archive.")
            return

        self.song_path = path
        self.project_dir = path.parent
        self.project_label.config(text=str(path))
        self.analyze_project()

    def analyze_project(self):
        if not self.song_path or not self.project_dir:
            messagebox.showinfo("No project", "Open a .song file first.")
            return

        try:
            self.plugins = parse_plugins(self.song_path)
        except Exception as e:
            self.plugins = []
            self.show_error("Plugin analysis failed", e)

        try:
            archive_texts = []
            with zipfile.ZipFile(self.song_path, "r") as z:
                for name in z.namelist():
                    if name.lower().endswith((".xml", ".settings", ".txt", ".songdata", ".json")):
                        try:
                            archive_texts.append(z.read(name).decode("utf-8", errors="replace"))
                        except Exception:
                            pass

            self.audio_rows = build_audio_table_rows(self.project_dir, archive_texts)
        except Exception as e:
            self.audio_rows = []
            self.show_error("Audio analysis failed", e)

        self.populate_plugins()
        self.populate_audio()
        self.update_dashboard()

    # -------------------------
    # Plugins
    # -------------------------

    def _filtered_plugins(self):
        items = list(self.plugins)
        query = self.plugin_search_var.get().strip().lower() if hasattr(self, "plugin_search_var") else ""

        if query:
            items = [
                p for p in items
                if query in p.track_name.lower()
                or query in p.plugin_name.lower()
                or query in p.slot_name.lower()
            ]

        if self.plugin_filter_mode == "selected_fx":
            items = [p for p in items if p.index in self.plugin_filter_indices]
        elif self.plugin_filter_mode == "selected_tracks":
            items = [p for p in items if p.track_name in self.plugin_filter_tracks]
        elif self.plugin_filter_mode == "bypassed_fx_list":
            items = [p for p in items if p.bypassed]

        key = self.plugin_sort_key
        if key == "track":
            items.sort(key=lambda p: (p.track_name.lower(), p.slot_name.lower(), p.plugin_name.lower()), reverse=self.plugin_sort_reverse)
        elif key == "slot":
            items.sort(key=lambda p: (p.slot_name.lower(), p.track_name.lower(), p.plugin_name.lower()), reverse=self.plugin_sort_reverse)
        elif key == "plugin":
            items.sort(key=lambda p: (p.plugin_name.lower(), p.track_name.lower(), p.slot_name.lower()), reverse=self.plugin_sort_reverse)
        elif key == "bypass":
            items.sort(key=lambda p: (not p.bypassed, p.track_name.lower(), p.slot_name.lower()), reverse=self.plugin_sort_reverse)

        return items

    def populate_plugins(self):
        self.plugins_tree.delete(*self.plugins_tree.get_children())

        items = self._filtered_plugins()

        # Flat list mode: only bypassed FX, no parent track rows.
        # This avoids selecting whole track groups with active/non-bypassed FX inside.
        if self.plugin_filter_mode == "bypassed_fx_list":
            for p in items:
                self.plugins_tree.insert(
                    "",
                    "end",
                    text=p.track_name,
                    values=(p.slot_name, p.plugin_name, "BYPASSED", str(p.index)),
                    tags=("bypassed",),
                )
            return

        by_track = {}

        for p in items:
            by_track.setdefault(p.track_name or "(no track name)", []).append(p)

        for track_name, track_items in by_track.items():
            total = len(track_items)
            bypassed_count = sum(1 for p in track_items if p.bypassed)

            if bypassed_count == total and total > 0:
                parent_tag = "track_all_bypassed"
            elif bypassed_count > 0:
                parent_tag = "track_some_bypassed"
            else:
                parent_tag = "active"

            parent_text = f"{track_name}    [{total} FX | {bypassed_count} bypassed]"
            parent_id = self.plugins_tree.insert("", "end", text=parent_text, values=("", "", "", ""), tags=(parent_tag,), open=False)

            for p in track_items:
                tags = ("bypassed",) if p.bypassed else ("active",)
                self.plugins_tree.insert(
                    parent_id,
                    "end",
                    text="",
                    values=(p.slot_name, p.plugin_name, "BYPASSED" if p.bypassed else "ACTIVE", str(p.index)),
                    tags=tags,
                )

    def sort_plugins_by(self, key: str):
        if self.plugin_sort_key == key:
            self.plugin_sort_reverse = not self.plugin_sort_reverse
        else:
            self.plugin_sort_key = key
            self.plugin_sort_reverse = False
        self.populate_plugins()

    def expand_all_plugin_tracks(self):
        for item in self.plugins_tree.get_children():
            self.plugins_tree.item(item, open=True)

    def collapse_all_plugin_tracks(self):
        for item in self.plugins_tree.get_children():
            self.plugins_tree.item(item, open=False)

    def show_bypassed_fx_list(self):
        self.plugin_filter_mode = "bypassed_fx_list"
        self.plugin_filter_indices = set()
        self.plugin_filter_tracks = set()
        if hasattr(self, "plugin_search_var"):
            self.plugin_search_var.set("")
        self.populate_plugins()

    def clear_plugin_filter(self):
        self.plugin_filter_mode = "all"
        self.plugin_filter_indices = set()
        self.plugin_filter_tracks = set()
        if hasattr(self, "plugin_search_var"):
            self.plugin_search_var.set("")
        self.populate_plugins()

    def selected_fx_indices_from_table(self) -> set[int]:
        indices = set()

        for item in self.plugins_tree.selection():
            values = self.plugins_tree.item(item, "values")

            if values and len(values) >= 4 and str(values[3]).strip() != "":
                try:
                    indices.add(int(values[3]))
                except Exception:
                    pass
                continue

            for child in self.plugins_tree.get_children(item):
                child_values = self.plugins_tree.item(child, "values")
                if child_values and len(child_values) >= 4 and str(child_values[3]).strip() != "":
                    try:
                        indices.add(int(child_values[3]))
                    except Exception:
                        pass

        return indices

    def selected_tracks_from_table(self) -> set[str]:
        tracks = set()

        for item in self.plugins_tree.selection():
            values = self.plugins_tree.item(item, "values")

            if not (values and len(values) >= 4 and str(values[3]).strip() != ""):
                text = self.plugins_tree.item(item, "text")
                if text:
                    tracks.add(text.split("    [", 1)[0])
                continue

            try:
                idx = int(values[3])
                for p in self.plugins:
                    if p.index == idx:
                        tracks.add(p.track_name)
                        break
            except Exception:
                pass

        return tracks

    def filter_selected_fx(self):
        indices = self.selected_fx_indices_from_table()
        if not indices:
            messagebox.showwarning("Studio One Project Tools", "Select one or more FX rows or track groups first.")
            return

        self.plugin_filter_mode = "selected_fx"
        self.plugin_filter_indices = set(indices)
        self.plugin_filter_tracks = set()
        self.populate_plugins()

    def filter_selected_tracks(self):
        tracks = self.selected_tracks_from_table()
        if not tracks:
            messagebox.showwarning("Studio One Project Tools", "Select one or more tracks or FX rows first.")
            return

        self.plugin_filter_mode = "selected_tracks"
        self.plugin_filter_tracks = set(tracks)
        self.plugin_filter_indices = set()
        self.populate_plugins()

    def select_all_visible_plugins_event(self, event=None):
        self.select_all_visible_plugins()
        return "break"

    def select_all_visible_plugins(self):
        items = []

        def collect(node):
            items.append(node)
            for child in self.plugins_tree.get_children(node):
                collect(child)

        for root_item in self.plugins_tree.get_children():
            collect(root_item)

        self.plugins_tree.selection_set(items)

    def show_plugins_context_menu(self, event):
        row = self.plugins_tree.identify_row(event.y)
        if row:
            if row not in self.plugins_tree.selection():
                self.plugins_tree.selection_set(row)
            self.plugins_tree.focus(row)

        try:
            self.plugins_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.plugins_context_menu.grab_release()

    def _focused_or_first_selected_plugin(self):
        item = self.plugins_tree.focus()
        if not item:
            selected = self.plugins_tree.selection()
            item = selected[0] if selected else ""

        if not item:
            return None

        values = self.plugins_tree.item(item, "values")

        if values and len(values) >= 4 and str(values[3]).strip() != "":
            try:
                idx = int(values[3])
                for p in self.plugins:
                    if p.index == idx:
                        return p
            except Exception:
                return None

        children = self.plugins_tree.get_children(item)
        if children:
            child_values = self.plugins_tree.item(children[0], "values")
            if child_values and len(child_values) >= 4 and str(child_values[3]).strip() != "":
                try:
                    idx = int(child_values[3])
                    for p in self.plugins:
                        if p.index == idx:
                            return p
                except Exception:
                    pass

        return None

    def context_filter_by_plugin(self):
        p = self._focused_or_first_selected_plugin()
        if p:
            self.plugin_search_var.set(p.plugin_name)

    def context_filter_by_track(self):
        p = self._focused_or_first_selected_plugin()
        if p:
            self.plugin_search_var.set(p.track_name)

    def copy_selected_plugin_name(self):
        p = self._focused_or_first_selected_plugin()
        if p:
            self.clipboard_clear()
            self.clipboard_append(p.plugin_name)

    def copy_selected_track_name(self):
        p = self._focused_or_first_selected_plugin()
        if p:
            self.clipboard_clear()
            self.clipboard_append(p.track_name)

    def modify_selected_fx_action(self, action: str):
        if not self.song_path:
            messagebox.showinfo("No project", "Open a .song file first.")
            return

        indices = self.selected_fx_indices_from_table()
        if not indices:
            messagebox.showwarning("No FX selected", "Select one or more FX rows or track groups first.")
            return

        confirm = messagebox.askyesno(
            "Studio One Project Tools",
            f"Selected FX: {len(indices)}\n\n"
            f"Action: {action}\n\n"
            "A new .song copy will be created. Original project will not be overwritten.\n\n"
            "Continue?"
        )
        if not confirm:
            return

        suffix = {
            "remove": "_selected_fx_removed.song",
            "bypass": "_selected_fx_bypassed.song",
            "unbypass": "_selected_fx_unbypassed.song",
        }.get(action, "_selected_fx_modified.song")

        output = filedialog.asksaveasfilename(
            title="Save modified .song copy",
            defaultextension=".song",
            initialfile=self.song_path.stem + suffix,
            filetypes=[("Studio One song", "*.song")],
        )

        if not output:
            return

        try:
            _, changed = modify_selected_fx(self.song_path, Path(output), indices, action)
            messagebox.showinfo("Done", f"Saved:\n{output}\n\nChanged FX: {len(changed)}")
        except Exception as e:
            self.show_error("Modify selected FX failed", e)

    # -------------------------
    # Audio / Files
    # -------------------------

    def audio_candidates_for_item(self, item):
        ref = item.reference or item.filename
        if not ref:
            return []

        candidates = getattr(self, "missing_candidates", [])
        exact = [c for c in candidates if c.missing_reference == ref]
        if exact:
            return exact

        name = item.filename.lower()
        return [c for c in candidates if c.filename.lower() == name]

    def audio_candidate_signature(self, candidate):
        return (candidate.size, candidate.quick_hash)

    def audio_candidate_status_for_item(self, item):
        candidates = self.audio_candidates_for_item(item)

        if not candidates:
            return ("NOT_FOUND", None, 0)

        signatures = {self.audio_candidate_signature(c) for c in candidates}

        if len(signatures) == 1:
            return ("FOUND", candidates[0], len(candidates))

        return ("AMBIGUOUS", None, len(candidates))

    def audio_candidate_for_item(self, item):
        status, candidate, count = self.audio_candidate_status_for_item(item)
        return candidate if status == "FOUND" else None

    def audio_candidate_path_for_item(self, item) -> str:
        c = self.audio_candidate_for_item(item)
        return str(c.source_path) if c else ""

    def audio_display_folder(self, item) -> str:
        if item.status == "MISSING":
            status, candidate, count = self.audio_candidate_status_for_item(item)

            if status == "FOUND" and candidate:
                if count == 1:
                    return "FOUND: " + str(candidate.source_path.parent)
                return f"FOUND: {count} identical copies, using " + str(candidate.source_path.parent)

            if status == "AMBIGUOUS":
                return f"AMBIGUOUS: {count} different files with same name"

        return item.folder

    def _filtered_audio_rows_with_indices(self):
        rows = list(enumerate(self.audio_rows))

        if self.audio_filter != "ALL":
            rows = [(i, r) for i, r in rows if r.status == self.audio_filter]

        query = self.audio_search_var.get().strip().lower() if hasattr(self, "audio_search_var") else ""
        if query:
            rows = [
                (i, r) for i, r in rows
                if query in r.status.lower()
                or query in (r.folder or "").lower()
                or query in (r.filename or "").lower()
                or query in str(r.path or "").lower()
                or query in str(r.reference or "").lower()
                or query in self.audio_candidate_path_for_item(r).lower()
            ]

        return rows

    def populate_audio(self):
        self.audio_tree.delete(*self.audio_tree.get_children())

        for idx, item in self._filtered_audio_rows_with_indices():
            self.audio_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    item.status,
                    format_size(item.size),
                    self.audio_display_folder(item),
                    item.filename,
                    item.reference or "",
                    self.audio_candidate_path_for_item(item),
                ),
                tags=(item.status,),
            )

        self.update_audio_stats()

    def set_audio_filter(self, status: str):
        self.audio_filter = status
        self.populate_audio()

    def clear_audio_search_filter(self):
        self.audio_filter = "ALL"
        self.audio_search_var.set("")
        self.populate_audio()

    def sort_audio_by(self, col: str):
        if self.audio_sort_key == col:
            self.audio_sort_reverse = not self.audio_sort_reverse
        else:
            self.audio_sort_key = col
            self.audio_sort_reverse = False

        key_map = {
            "status": lambda r: r.status,
            "size": lambda r: r.size if r.size is not None else -1,
            "folder": lambda r: (r.folder or "").lower(),
            "file": lambda r: (r.filename or "").lower(),
        }

        self.audio_rows.sort(key=key_map[col], reverse=self.audio_sort_reverse)
        self.populate_audio()

    def select_all_audio(self, event=None):
        self.audio_tree.selection_set(self.audio_tree.get_children())
        return "break"

    def selected_audio_items(self):
        items = []
        for iid in self.audio_tree.selection():
            try:
                items.append(self.audio_rows[int(iid)])
            except Exception:
                pass
        return items

    def focused_or_first_selected_audio_item(self):
        iid = self.audio_tree.focus()
        if not iid:
            selected = self.audio_tree.selection()
            iid = selected[0] if selected else ""

        if not iid:
            return None

        try:
            return self.audio_rows[int(iid)]
        except Exception:
            return None

    def show_audio_context_menu(self, event):
        row = self.audio_tree.identify_row(event.y)
        if row:
            if row not in self.audio_tree.selection():
                self.audio_tree.selection_set(row)
            self.audio_tree.focus(row)

        try:
            self.audio_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.audio_context_menu.grab_release()

    def open_selected_audio_location(self):
        item = self.focused_or_first_selected_audio_item()
        if not item:
            return

        c = self.audio_candidate_for_item(item)
        if c and c.source_path.exists():
            os.startfile(str(c.source_path.parent))
            return

        if item.path and item.path.exists():
            os.startfile(str(item.path.parent))
            return

        if item.folder and Path(item.folder).exists():
            os.startfile(str(item.folder))
            return

        messagebox.showinfo("No location", "This row does not have an existing local path.")

    def copy_selected_audio_path(self):
        item = self.focused_or_first_selected_audio_item()
        if not item:
            return

        c = self.audio_candidate_for_item(item)
        if c:
            text = str(c.source_path)
        else:
            text = str(item.path) if item.path else (item.reference or item.filename or "")

        self.clipboard_clear()
        self.clipboard_append(text)

    def copy_selected_audio_filename(self):
        item = self.focused_or_first_selected_audio_item()
        if not item:
            return
        self.clipboard_clear()
        self.clipboard_append(item.filename or "")

    def audio_filter_by_selected_status(self):
        item = self.focused_or_first_selected_audio_item()
        if item:
            self.set_audio_filter(item.status)

    def show_selected_audio_only(self):
        selected = self.selected_audio_items()
        if not selected:
            return

        selected_ids = {id(item) for item in selected}

        self.audio_tree.delete(*self.audio_tree.get_children())
        for idx, item in enumerate(self.audio_rows):
            if id(item) not in selected_ids:
                continue
            self.audio_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(item.status, format_size(item.size), self.audio_display_folder(item), item.filename, item.reference or "", self.audio_candidate_path_for_item(item)),
                tags=(item.status,),
            )
        self.update_audio_stats()

    def update_audio_stats(self):
        total = len(self.audio_rows)
        visible = len(self.audio_tree.get_children()) if hasattr(self, "audio_tree") else 0

        counts = {"NORMAL": 0, "EXTERNAL": 0, "DUPLICATE": 0, "UNUSED": 0, "MISSING": 0}
        total_size = 0
        duplicate_size = 0
        unused_size = 0

        for item in self.audio_rows:
            counts[item.status] = counts.get(item.status, 0) + 1
            if item.size:
                total_size += item.size
                if item.status == "DUPLICATE":
                    duplicate_size += item.size
                elif item.status == "UNUSED":
                    unused_size += item.size

        text = (
            f"Visible: {visible}/{total}    "
            f"N: {counts.get('NORMAL', 0)}    "
            f"Ext: {counts.get('EXTERNAL', 0)}    "
            f"Dup: {counts.get('DUPLICATE', 0)}    "
            f"Unused: {counts.get('UNUSED', 0)}    "
            f"Miss: {counts.get('MISSING', 0)}    "
            f"Total: {format_size(total_size)}    "
            f"Dup: {format_size(duplicate_size)}    "
            f"Unused: {format_size(unused_size)}"
        )

        self.audio_stats_label.config(text=text)

    def move_selected_audio(self):
        selected = self.selected_audio_items()
        if not selected:
            messagebox.showinfo("No selection", "Select audio files first.")
            return

        movable = [item for item in selected if item.path and item.path.exists()]
        if not movable:
            messagebox.showinfo("No movable files", "Selected rows do not contain existing local files.")
            return

        target = filedialog.askdirectory(title="Move selected audio to folder")
        if not target:
            return

        target_dir = Path(target)
        moved = 0
        errors = []

        for item in movable:
            try:
                move_file_to_folder(item.path, target_dir)
                moved += 1
            except Exception as e:
                errors.append(f"{item.filename}: {e}")

        message = f"Moved: {moved}"
        if errors:
            message += "\n\nErrors:\n" + "\n".join(errors[:20])

        messagebox.showinfo("Move complete", message)
        self.analyze_project()

    def move_duplicate_audio(self):
        duplicates = [r for r in self.audio_rows if r.status == "DUPLICATE" and r.path and r.path.exists() and r.duplicate_key]

        if not duplicates:
            messagebox.showinfo("No duplicates", "No duplicate audio files detected.")
            return

        target = filedialog.askdirectory(title="Move duplicate audio to folder")
        if not target:
            return

        target_dir = Path(target)
        by_group = {}
        for item in duplicates:
            by_group.setdefault(item.duplicate_key, []).append(item)

        moved = 0
        kept = 0
        errors = []

        for key, group in by_group.items():
            group = sorted(group, key=lambda r: str(r.path).lower())
            kept += 1
            for item in group[1:]:
                try:
                    if item.path and item.path.exists():
                        move_file_to_folder(item.path, target_dir)
                        moved += 1
                except Exception as e:
                    errors.append(f"{item.filename}: {e}")

        message = f"Duplicate groups: {len(by_group)}\nKept originals: {kept}\nMoved duplicates: {moved}"
        if errors:
            message += "\n\nErrors:\n" + "\n".join(errors[:20])

        messagebox.showinfo("Duplicate move complete", message)
        self.analyze_project()

    def move_unused_audio(self):
        unused = [r for r in self.audio_rows if r.status == "UNUSED" and r.path and r.path.exists()]

        if not unused:
            messagebox.showinfo("No unused audio", "No unused audio files detected.")
            return

        confirm = messagebox.askyesno(
            "Move unused audio",
            f"Unused files detected: {len(unused)}\n\nFiles will be moved, not deleted.\n\nContinue?"
        )
        if not confirm:
            return

        target = filedialog.askdirectory(title="Move unused audio to folder")
        if not target:
            return

        target_dir = Path(target)
        moved = 0
        errors = []

        for item in unused:
            try:
                move_file_to_folder(item.path, target_dir)
                moved += 1
            except Exception as e:
                errors.append(f"{item.filename}: {e}")

        message = f"Moved unused files: {moved}"
        if errors:
            message += "\n\nErrors:\n" + "\n".join(errors[:20])

        messagebox.showinfo("Unused move complete", message)
        self.analyze_project()

    # -------------------------
    # Missing via Everything
    # -------------------------

    def restore_missing_files_one_click(self):
        if not self.song_path or not self.project_dir:
            messagebox.showinfo("No project", "Open and analyze a .song file first.")
            return

        missing_count = sum(1 for a in self.audio_rows if a.status == "MISSING")
        if missing_count == 0:
            messagebox.showinfo("No missing files", "No missing audio references detected.")
            return

        es = find_es_exe(self.everything_es_path_var.get().strip() or self.settings.get("everything_es_path") or None)
        if not es:
            messagebox.showerror(
                "Missing file search unavailable",
                "Search engine executable was not found.\n\nOpen Settings and select es.exe."
            )
            return

        try:
            self.missing_candidates = search_missing_candidates_everything(
                self.audio_rows,
                es_path=self.everything_es_path_var.get().strip() or self.settings.get("everything_es_path") or None,
                instance=None,
                mode="default",
            )
        except Exception as e:
            self.show_error("Missing file search failed", e)
            return

        safe_indices = []
        skipped_not_found = []
        skipped_ambiguous = []

        for idx, item in enumerate(self.audio_rows):
            if item.status != "MISSING":
                continue
            status, candidate, count = self.audio_candidate_status_for_item(item)
            ref = item.reference or item.filename

            if status == "FOUND" and candidate:
                safe_indices.append(str(idx))
            elif status == "AMBIGUOUS":
                skipped_ambiguous.append((ref, count))
            else:
                skipped_not_found.append(ref)

        if not safe_indices:
            self.audio_filter = "MISSING"
            self.populate_audio()
            messagebox.showinfo(
                "Restore missing files",
                "No safe matches found.\n\n"
                f"Missing refs: {missing_count}\n"
                f"Ambiguous: {len(skipped_ambiguous)}\n"
                f"Not found: {len(skipped_not_found)}\n\n"
                "Use right-click manual recovery tools if needed."
            )
            return

        self.audio_filter = "MISSING"
        self.populate_audio()
        self.audio_tree.selection_set([iid for iid in safe_indices if self.audio_tree.exists(iid)])

        confirm = messagebox.askyesno(
            "Restore missing files",
            f"Missing refs: {missing_count}\n"
            f"Safe matches to restore: {len(safe_indices)}\n"
            f"Ambiguous skipped: {len(skipped_ambiguous)}\n"
            f"Not found skipped: {len(skipped_not_found)}\n\n"
            "The tool will create a new relinked .song copy.\n"
            "Original .song will not be overwritten.\n\nContinue?"
        )
        if not confirm:
            return

        self.recover_selected_audio_missing_candidates()

    def search_missing_everything(self):
        if not self.song_path:
            messagebox.showinfo("No project", "Open and analyze a .song file first.")
            return

        missing_count = sum(1 for a in self.audio_rows if a.status == "MISSING")
        if missing_count == 0:
            messagebox.showinfo("No missing files", "No missing audio references detected.")
            return

        es = find_es_exe(self.everything_es_path_var.get().strip() or self.settings.get("everything_es_path") or None)
        if not es:
            messagebox.showerror(
                "Missing file search unavailable",
                "Search engine executable was not found.\n\nOpen Settings and select es.exe."
            )
            return

        try:
            self.missing_candidates = search_missing_candidates_everything(
                self.audio_rows,
                es_path=self.settings.get("everything_es_path") or None,
                instance=self.settings.get("everything_instance") or None,
            )
        except Exception as e:
            self.show_error("Missing file search failed", e)
            return

        self.audio_filter = "MISSING"
        self.populate_audio()
        self.notebook.select(self.audio_frame)

        unique_refs = {c.missing_reference for c in self.missing_candidates}

        messagebox.showinfo(
            "Missing file search complete",
            f"Missing references: {missing_count}\n"
            f"References with candidates: {len(unique_refs)}\n"
            f"Total candidates found: {len(self.missing_candidates)}"
        )

    def recover_all_found_missing_candidates(self):
        # Select all MISSING rows that have safe FOUND matches, then reuse selected recovery.
        safe_indices = []

        for idx, item in enumerate(self.audio_rows):
            if item.status != "MISSING":
                continue

            status, candidate, count = self.audio_candidate_status_for_item(item)
            if status == "FOUND" and candidate:
                safe_indices.append(str(idx))

        if not safe_indices:
            messagebox.showinfo("No safe FOUND matches", "No MISSING rows with safe FOUND matches.")
            return

        self.audio_filter = "MISSING"
        self.populate_audio()
        self.audio_tree.selection_set([iid for iid in safe_indices if self.audio_tree.exists(iid)])
        self.recover_selected_audio_missing_candidates()

    def recover_selected_audio_missing_candidates(self):
        if not self.song_path or not self.project_dir:
            messagebox.showinfo("No project", "Open and analyze a .song file first.")
            return

        selected_items = self.selected_audio_items()
        if not selected_items:
            messagebox.showinfo("No selection", "Select missing audio rows first.")
            return

        missing_items = [item for item in selected_items if item.status == "MISSING"]
        if not missing_items:
            messagebox.showinfo("No missing rows", "Select one or more MISSING rows in Audio/Files.")
            return

        selected_candidates = []
        skipped_not_found = []
        skipped_ambiguous = []
        seen_refs = set()

        for item in missing_items:
            status, candidate, count = self.audio_candidate_status_for_item(item)
            ref = item.reference or item.filename

            if status == "FOUND" and candidate:
                if candidate.missing_reference not in seen_refs:
                    seen_refs.add(candidate.missing_reference)
                    selected_candidates.append(candidate)
            elif status == "AMBIGUOUS":
                skipped_ambiguous.append((ref, count))
            else:
                skipped_not_found.append(ref)

        if not selected_candidates:
            msg = "No safe FOUND matches selected.\n\n"
            if skipped_ambiguous:
                msg += f"Ambiguous skipped: {len(skipped_ambiguous)}\n"
            if skipped_not_found:
                msg += f"Not found skipped: {len(skipped_not_found)}\n"
            msg += "\nRun Find missing files first, or resolve ambiguous files manually."
            messagebox.showinfo("No safe matches", msg)
            return

        details = [
            f"Selected missing rows: {len(missing_items)}",
            f"Safe FOUND matches to recover: {len(selected_candidates)}",
        ]

        if skipped_ambiguous:
            details.append(f"Ambiguous skipped: {len(skipped_ambiguous)}")
        if skipped_not_found:
            details.append(f"Not found skipped: {len(skipped_not_found)}")

        confirm = messagebox.askyesno(
            "Recover missing files",
            "\n".join(details)
            + "\n\nOnly safe FOUND matches will be recovered.\n"
            + "Multiple identical copies are treated as the same file by size + quick hash.\n\n"
            + "The tool will copy files into Media and create a new relinked .song copy.\n"
            + "Original .song will not be overwritten.\n\nContinue?"
        )
        if not confirm:
            return

        output = filedialog.asksaveasfilename(
            title="Save relinked .song copy",
            defaultextension=".song",
            initialfile=self.song_path.stem + "_relinked_missing.song",
            filetypes=[("Studio One song", "*.song")]
        )

        if not output:
            return

        output_path = Path(output)

        try:
            _, copied = recover_missing_to_media_and_relink(
                song_path=self.song_path,
                output_path=output_path,
                project_dir=self.project_dir,
                selected_candidates=selected_candidates,
                media_folder_name=self.settings.get("media_folder_name", "Media"),
            )
        except Exception as e:
            self.show_error("Recovery failed", e)
            return

        report_path = output_path.with_name(output_path.stem + "_relink_report.txt")
        try:
            lines = []
            lines.append("Studio One Project Tools - Relink Report")
            lines.append("=" * 80)
            lines.append(f"Original song: {self.song_path}")
            lines.append(f"Relinked song: {output_path}")
            lines.append(f"Project dir: {self.project_dir}")
            lines.append(f"Recovered files: {len(copied)}")
            lines.append(f"Skipped ambiguous: {len(skipped_ambiguous)}")
            lines.append(f"Skipped not found: {len(skipped_not_found)}")
            lines.append("")

            by_ref = {c.missing_reference: c for c in selected_candidates}

            for ref, dest in copied.items():
                c = by_ref.get(ref)
                lines.append("-" * 80)
                lines.append(f"Old reference: {ref}")
                if c:
                    same_count = len([
                        x for x in self.missing_candidates
                        if x.missing_reference == c.missing_reference
                        and x.size == c.size
                        and x.quick_hash == c.quick_hash
                    ])
                    lines.append(f"Found source:  {c.source_path}")
                    lines.append(f"Identical copies found: {same_count}")
                    lines.append(f"Source size:   {c.size if c.size is not None else ''}")
                    lines.append(f"Quick hash:    {c.quick_hash or ''}")
                    lines.append(f"Confidence:    size + first/last MB quick hash")
                lines.append(f"Copied to:     {dest}")
                try:
                    rel = dest.resolve().relative_to(self.project_dir.resolve())
                    new_ref = str(rel).replace("/", "\\")
                    lines.append(f"New reference: {new_ref}")
                except Exception:
                    lines.append(f"New reference: {dest}")

            if skipped_ambiguous:
                lines.append("")
                lines.append("SKIPPED AMBIGUOUS")
                lines.append("=" * 80)
                for ref, count in skipped_ambiguous:
                    lines.append(f"{ref}: {count} different candidates by size/hash")

            if skipped_not_found:
                lines.append("")
                lines.append("SKIPPED NOT FOUND")
                lines.append("=" * 80)
                for ref in skipped_not_found:
                    lines.append(str(ref))

            report_path.write_text("\n".join(lines), encoding="utf-8")
        except Exception as e:
            self.show_error("Relink report failed", e)
            return

        lines = [
            f"Saved relinked project:\n{output_path}",
            "",
            f"Relink report:\n{report_path}",
            "",
            f"Recovered files: {len(copied)}",
        ]

        if skipped_ambiguous:
            lines.append(f"Skipped ambiguous: {len(skipped_ambiguous)}")
        if skipped_not_found:
            lines.append(f"Skipped not found: {len(skipped_not_found)}")

        messagebox.showinfo("Recovery complete", "\n".join(lines))
        self.analyze_project()

    # -------------------------
    # Reports
    # -------------------------

    def export_txt_report_dialog(self):
        if not self.song_path:
            messagebox.showinfo("No project", "Open and analyze a .song file first.")
            return

        output = filedialog.asksaveasfilename(
            title="Export TXT Report",
            defaultextension=".txt",
            initialfile=self.song_path.stem + "_report.txt",
            filetypes=[("Text report", "*.txt"), ("All files", "*.*")]
        )

        if not output:
            return

        try:
            export_txt_report(Path(output), self.song_path, self.plugins, self.audio_rows)
            messagebox.showinfo("Report exported", f"Saved:\n{output}")
        except Exception as e:
            self.show_error("Export failed", e)

    def export_csv_report_dialog(self):
        if not self.song_path:
            messagebox.showinfo("No project", "Open and analyze a .song file first.")
            return

        output = filedialog.asksaveasfilename(
            title="Export CSV Report",
            defaultextension=".csv",
            initialfile=self.song_path.stem + "_report.csv",
            filetypes=[("CSV report", "*.csv"), ("All files", "*.*")]
        )

        if not output:
            return

        try:
            export_csv_report(Path(output), self.song_path, self.plugins, self.audio_rows)
            messagebox.showinfo("Report exported", f"Saved:\n{output}")
        except Exception as e:
            self.show_error("Export failed", e)

    # -------------------------
    # Settings
    # -------------------------

    def browse_everything_es_path(self):
        filename = filedialog.askopenfilename(
            title="Select es.exe",
            filetypes=[("Everything CLI", "es.exe"), ("Executable", "*.exe"), ("All files", "*.*")]
        )
        if filename:
            self.everything_es_path_var.set(filename)


    def autodetect_everything_es_path(self):
        es = find_es_exe(None)
        if es:
            self.everything_es_path_var.set(str(es))
            messagebox.showinfo("es.exe detected", f"Detected:\n{es}")
        else:
            messagebox.showwarning(
                "es.exe not found",
                "es.exe was not found automatically.\n\n"
                "The bundled es.exe should be placed next to the app. "
                "You can also select it manually."
            )

    def save_app_settings(self):
        self.settings["everything_es_path"] = self.everything_es_path_var.get().strip()
        self.settings["everything_instance"] = ""
        self.settings["media_folder_name"] = self.media_folder_var.get().strip() or "Media"
        self.settings["duplicates_folder_name"] = self.duplicates_folder_var.get().strip() or "_Duplicates"
        self.settings["unused_folder_name"] = self.unused_folder_var.get().strip() or "_Unused"

        try:
            path = save_settings(self.settings)
            messagebox.showinfo("Settings saved", f"Saved:\n{path}")
        except Exception as e:
            self.show_error("Save settings failed", e)

    def test_everything_es_path(self):
        path = self.everything_es_path_var.get().strip()

        try:
            quick = test_everything_available(path or None)
            report = diagnose_everything(path or None, None, "default")
        except Exception as e:
            self.show_error("Everything test failed", e)
            return

        log_path = Path.cwd() / "everything_diagnostics.txt"
        try:
            log_path.write_text(report, encoding="utf-8")
        except Exception:
            pass

        exe = quick.get("exe") or "<not found>"
        cmd = quick.get("cmd") or "<not executed>"
        reason = quick.get("reason") or ""
        stdout = (quick.get("stdout") or "").strip()
        stderr = (quick.get("stderr") or "").strip()
        rc = quick.get("returncode")

        if quick.get("ok"):
            msg = (
                "Everything test passed.\n\n"
                "es.exe is working correctly.\n"
                "Standard compatible mode is OK.\n\n"
                f"Detected es.exe:\n{exe}\n\n"
                f"Return code: {rc}\n"
                f"Diagnostics saved to:\n{log_path}"
            )
            messagebox.showinfo("Everything test passed", msg)
        else:
            details = [
                "Everything test failed.",
                "",
                reason,
                "",
                f"Detected es.exe:",
                str(exe),
                "",
                f"Command:",
                str(cmd),
                "",
                f"Return code: {rc}",
            ]
            if stderr:
                details += ["", "Error:", stderr[:1200]]
            elif stdout:
                details += ["", "Output:", stdout[:1200]]
            details += ["", f"Full diagnostics saved to:", str(log_path)]
            messagebox.showerror("Everything test failed", "\n".join(details))


    # -------------------------
    # Other actions
    # -------------------------

    def remove_bypassed_fx_copy(self):
        if not self.song_path:
            messagebox.showinfo("No project", "Open a .song file first.")
            return

        output = filedialog.asksaveasfilename(
            title="Save cleaned .song copy",
            defaultextension=".song",
            initialfile=self.song_path.stem + "_no_bypassed_fx.song",
            filetypes=[("Studio One song", "*.song")]
        )

        if not output:
            return

        try:
            remove_bypassed_fx(self.song_path, Path(output))
            messagebox.showinfo("Done", f"Saved:\n{output}")
        except Exception as e:
            self.show_error("Remove bypassed FX failed", e)

    def consolidate_project(self):
        if self.consolidate_running:
            messagebox.showinfo("Consolidate", "Consolidate is already running.")
            return

        if not self.song_path or not self.project_dir:
            messagebox.showinfo("No project", "Open and analyze a .song file first.")
            return

        if not self.consolidate_copy_all_referenced.get():
            messagebox.showwarning(
                "Unsafe option",
                "Consolidate requires copying all referenced audio into the new Media folder.\n\n"
                "This option is locked logically for v1; it will still copy all referenced audio."
            )

        missing_count = sum(1 for a in self.audio_rows if a.status == "MISSING")

        output_parent = filedialog.askdirectory(title="Select parent folder for consolidated project")
        if not output_parent:
            return

        unique_missing_names = len({(a.reference or a.filename).replace("\\", "/").split("/")[-1].lower() for a in self.audio_rows if a.status == "MISSING"})

        summary = [
            "Consolidate will create a NEW autonomous project folder.",
            "",
            f"Source song: {self.song_path}",
            f"Output parent: {output_parent}",
            "",
            "Original project will NOT be modified.",
            "Mode: COPY ONLY.",
            "",
            f"Audio rows: {len(self.audio_rows)}",
            f"Missing references: {missing_count}",
            f"Unique missing names: {unique_missing_names}",
            f"Automatic Everything search: {'yes' if self.consolidate_recover_missing.get() and missing_count else 'not needed/off'}",
            f"Create separate no-bypassed-FX copy: {'yes' if self.consolidate_remove_bypassed.get() else 'no'}",
            "",
            "Large projects can take a long time. Progress will be shown below."
        ]

        confirm = messagebox.askyesno("Start Consolidation", "\n".join(summary) + "\n\nContinue?")
        if not confirm:
            return

        options = {
            "song_path": self.song_path,
            "project_dir": self.project_dir,
            "output_parent": Path(output_parent),
            "audio_rows": list(self.audio_rows),
            "media_folder_name": self.settings.get("media_folder_name", "Media"),
            "duplicates_folder_name": self.settings.get("duplicates_folder_name", "_Duplicates"),
            "unused_folder_name": self.settings.get("unused_folder_name", "_Unused"),
            "recover_missing": self.consolidate_recover_missing.get(),
            "copy_unused": self.consolidate_copy_unused.get(),
            "copy_duplicate_nonreferenced": self.consolidate_copy_duplicates.get(),
            "remove_bypassed_fx": False,
            "create_no_bypassed_copy": self.consolidate_remove_bypassed.get(),
            "es_path": self.everything_es_path_var.get().strip() or self.settings.get("everything_es_path") or None,
            "instance": None,
            "mode": "default",
        }

        self.consolidate_running = True
        self.consolidate_total_bytes = 0
        self.consolidate_copied_bytes = 0
        self.consolidate_current_file_bytes = 0
        self.consolidate_current_file_total = 0
        self.consolidate_last_result = None

        try:
            self.consolidate_progress.configure(value=0, maximum=100)
            self.consolidate_status_var.set("Starting consolidate...")
            self.consolidate_detail_var.set("")
            self.update_idletasks()
        except Exception:
            pass

        while not self.consolidate_queue.empty():
            try:
                self.consolidate_queue.get_nowait()
            except Exception:
                break

        self.consolidate_worker = threading.Thread(
            target=self._consolidate_worker_entry,
            args=(options,),
            daemon=True,
        )
        self.consolidate_worker.start()
        self.after(120, self._poll_consolidate_queue)

    def _consolidate_worker_entry(self, options: dict):
        try:
            missing_count = sum(1 for a in options["audio_rows"] if a.status == "MISSING")
            missing_candidates = []

            if options["recover_missing"] and missing_count:
                self.consolidate_queue.put({
                    "type": "status",
                    "status": "Searching missing files via Everything...",
                    "detail": f"{missing_count} missing references. This runs before copy."
                })

                es = find_es_exe(options["es_path"])
                if not es:
                    raise RuntimeError(
                        "Missing references were detected, but es.exe was not found. "
                        "Put es.exe next to the app or select it in Settings."
                    )

                missing_candidates = search_missing_candidates_everything(
                    options["audio_rows"],
                    max_results_per_file=12,
                    es_path=options["es_path"],
                    instance=options["instance"],
                    mode=options.get("mode") or "auto",
                )

                candidate_refs = {c.missing_reference for c in missing_candidates}
                self.consolidate_queue.put({
                    "type": "status",
                    "status": "Everything search complete.",
                    "detail": f"Candidate refs found: {len(candidate_refs)}"
                })

            def progress_callback(payload):
                self.consolidate_queue.put({"type": "progress", **payload})

            result = consolidate_project_to_new_folder(
                song_path=options["song_path"],
                project_dir=options["project_dir"],
                output_parent=options["output_parent"],
                audio_rows=options["audio_rows"],
                missing_candidates=missing_candidates,
                media_folder_name=options["media_folder_name"],
                duplicates_folder_name=options["duplicates_folder_name"],
                unused_folder_name=options["unused_folder_name"],
                recover_missing=options["recover_missing"],
                copy_unused=options["copy_unused"],
                copy_duplicate_nonreferenced=options["copy_duplicate_nonreferenced"],
                remove_bypassed_fx=False,
                progress_callback=progress_callback,
            )

            # Optional safe bypassed-FX cleanup:
            # Do NOT mutate the main consolidated song. Create a second .song copy
            # using the same separate cleaner that already works from Plugins page.
            if options.get("create_no_bypassed_copy"):
                self.consolidate_queue.put({
                    "type": "status",
                    "status": "Creating separate no-bypassed-FX .song copy...",
                    "detail": "Main consolidated .song stays untouched."
                })
                clean_song = result.output_dir / f"{result.output_song.stem}_no_bypassed_fx.song"
                remove_bypassed_fx(result.output_song, clean_song)
                setattr(result, "no_bypassed_song", clean_song)
            else:
                setattr(result, "no_bypassed_song", None)

            self.consolidate_queue.put({"type": "done", "result": result})
        except Exception as e:
            self.consolidate_queue.put({"type": "error", "error": e, "traceback": traceback.format_exc()})

    def _poll_consolidate_queue(self):
        try:
            while True:
                msg = self.consolidate_queue.get_nowait()
                self._handle_consolidate_message(msg)
        except queue.Empty:
            pass

        if self.consolidate_running:
            self.after(120, self._poll_consolidate_queue)

    def _handle_consolidate_message(self, msg: dict):
        mtype = msg.get("type")

        if mtype == "status":
            self.consolidate_status_var.set(msg.get("status", "Working..."))
            self.consolidate_detail_var.set(msg.get("detail", ""))
            return

        if mtype == "progress":
            self._update_consolidate_progress(msg)
            return

        if mtype == "error":
            self.consolidate_running = False
            self.consolidate_status_var.set("Consolidate failed.")
            self.consolidate_detail_var.set(str(msg.get("error", "")))
            try:
                log_path = Path.cwd() / "error_log.txt"
                with log_path.open("a", encoding="utf-8") as f:
                    f.write("\n" + "=" * 80 + "\n")
                    f.write("Consolidate worker failed\n")
                    f.write(str(msg.get("error", "")) + "\n")
                    f.write(msg.get("traceback", ""))
                    f.write("\n")
            except Exception:
                pass
            messagebox.showerror("Consolidate failed", f"{msg.get('error')}\n\nDetails saved to error_log.txt")
            return

        if mtype == "done":
            self.consolidate_running = False
            result = msg.get("result")
            self.consolidate_last_result = result
            self.consolidate_progress.configure(value=100, maximum=100)
            self.consolidate_status_var.set("Consolidate complete.")
            self.consolidate_detail_var.set(f"Output: {result.output_dir}")

            msg_lines = [
                "Consolidate complete.",
                "",
                f"Output folder:\n{result.output_dir}",
                "",
                f"Output song:\n{result.output_song}",
                "",
                f"Referenced audio copied: {result.copied_audio}",
                f"Recovered missing: {result.recovered_missing}",
                f"Skipped missing not found: {result.skipped_missing}",
                f"Skipped ambiguous: {result.skipped_ambiguous}",
                f"Unused side copies: {result.copied_unused}",
                f"Duplicate side copies: {result.copied_duplicates}",
                f"Main consolidated .song keeps bypassed FX unchanged.",
                f"No-bypassed-FX copy:\n{getattr(result, 'no_bypassed_song', '') or 'not created'}",
                f"Relink replacement hits: {getattr(result, 'relink_replacements', 0)}",
                f"Post-check missing after consolidate: {getattr(result, 'verify_missing_after', 'n/a')}",
                f"Errors: {len(result.errors)}",
                "",
                f"Report:\n{result.report_path}",
                f"Relink debug:\n{getattr(result, 'relink_debug_report_path', '')}",
            ]

            messagebox.showinfo("Consolidate complete", "\n".join(msg_lines))

            try:
                os.startfile(str(result.output_dir))
            except Exception:
                pass

    def _format_bytes_local(self, value: int | float | None) -> str:
        if value is None:
            return "0 B"
        value = float(value)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if abs(value) < 1024:
                return f"{value:.1f} {unit}" if unit != "B" else f"{value:.0f} {unit}"
            value /= 1024
        return f"{value:.1f} PB"

    def _update_consolidate_progress(self, msg: dict):
        phase = msg.get("phase", "")

        if phase == "plan":
            self.consolidate_total_bytes = int(msg.get("total_bytes") or 0)
            self.consolidate_copied_bytes = 0
            total_files = msg.get("total_files", 0)
            self.consolidate_status_var.set("Copy plan ready.")
            self.consolidate_detail_var.set(
                f"{total_files} referenced audio files / {self._format_bytes_local(self.consolidate_total_bytes)}"
            )
            self.consolidate_progress.configure(maximum=max(self.consolidate_total_bytes, 1), value=0)
            return

        if phase == "copy_file_start":
            self.consolidate_current_file_bytes = 0
            self.consolidate_current_file_total = int(msg.get("current_total_bytes") or 0)
            current_file = msg.get("current_file", "")
            self.consolidate_status_var.set(f"Copying: {current_file}")
            self._refresh_consolidate_detail(msg)
            return

        if phase == "copy_file_progress":
            delta = int(msg.get("copied_bytes_delta") or 0)
            self.consolidate_copied_bytes += delta
            self.consolidate_current_file_bytes = int(msg.get("current_copied_bytes") or 0)
            self.consolidate_progress.configure(value=min(self.consolidate_copied_bytes, max(self.consolidate_total_bytes, 1)))
            self._refresh_consolidate_detail(msg)
            return

        if phase == "copy_file_done":
            current_file = msg.get("current_file", "")
            self.consolidate_status_var.set(f"Copied: {current_file}")
            self._refresh_consolidate_detail(msg)
            return

        if phase in {"copy_referenced", "recover_missing", "copy_unused", "copy_duplicate", "write_song", "write_reports", "verify", "done"}:
            message = msg.get("message") or phase
            self.consolidate_status_var.set(message)
            self._refresh_consolidate_detail(msg)
            return

    def _refresh_consolidate_detail(self, msg: dict):
        total = max(self.consolidate_total_bytes, 1)
        copied = min(self.consolidate_copied_bytes, total)
        pct = (copied / total) * 100 if total else 0
        current_file = msg.get("current_file", "")
        cur_copied = int(msg.get("current_copied_bytes") or self.consolidate_current_file_bytes or 0)
        cur_total = int(msg.get("current_total_bytes") or self.consolidate_current_file_total or 0)

        detail = (
            f"Total copied: {self._format_bytes_local(copied)} / {self._format_bytes_local(self.consolidate_total_bytes)} "
            f"({pct:.1f}%)"
        )

        if current_file:
            detail += (
                f" | Current: {current_file} "
                f"{self._format_bytes_local(cur_copied)} / {self._format_bytes_local(cur_total)}"
            )

        file_index = msg.get("file_index")
        total_files = msg.get("total_files")
        if file_index and total_files:
            detail += f" | File {file_index}/{total_files}"

        self.consolidate_detail_var.set(detail)



    # -------------------------
    # Batch Consolidate
    # -------------------------

    def browse_batch_input_folder(self):
        folder = filedialog.askdirectory(title="Select folder with Studio One projects")
        if folder:
            self.batch_input_var.set(folder)

    def browse_batch_output_folder(self):
        folder = filedialog.askdirectory(title="Select output parent folder for consolidated projects")
        if folder:
            self.batch_output_var.set(folder)

    def open_batch_output_folder(self):
        folder = self.batch_output_var.get().strip()
        if folder and Path(folder).exists():
            try:
                os.startfile(folder)
            except Exception:
                pass

    def _read_song_archive_texts_for_batch(self, song_path: Path) -> list[str]:
        archive_texts = []
        with zipfile.ZipFile(song_path, "r") as z:
            for name in z.namelist():
                if name.lower().endswith((".xml", ".settings", ".txt", ".songdata", ".json")):
                    try:
                        archive_texts.append(z.read(name).decode("utf-8", errors="replace"))
                    except Exception:
                        pass
        return archive_texts


    def _is_autosave_or_backup_song(self, path: Path) -> bool:
        """
        Filters Studio One autosave/backup/history/recovery projects from Batch scan.
        Conservative: only used when the checkbox is enabled.
        """
        text = str(path).lower()
        name = path.name.lower()
        parent_parts = [p.lower() for p in path.parts]

        folder_tokens = [
            "autosave", "auto-save", "auto save",
            "backup", "backups",
            "history", "versions",
            "recovery", "recovered",
            "crash", "restore",
            "autosaves",
        ]

        name_tokens = [
            "autosave", "auto-save", "auto save",
            "backup", "history", "recovery", "recovered",
            "crash", "restore",
        ]

        if any(tok in text for tok in folder_tokens):
            return True

        if any(tok in name for tok in name_tokens):
            return True

        # Common Studio One backup/autosave naming patterns.
        # Examples: "Song (Autosaved).song", "Song - backup.song", "Song.backup.song"
        patterns = [
            r"autosav",
            r"auto[-_ ]?save",
            r"backup",
            r"recovery",
            r"recovered",
            r"history",
            r"crash",
        ]
        return any(re.search(pat, name) for pat in patterns)

    def scan_batch_projects(self):
        if self.batch_running:
            messagebox.showinfo("Batch", "Batch is running.")
            return

        input_folder = Path(self.batch_input_var.get().strip())
        if not input_folder.exists() or not input_folder.is_dir():
            messagebox.showerror("Input folder", "Select a valid input folder.")
            return

        pattern_iter = input_folder.rglob("*.song") if self.batch_recursive_var.get() else input_folder.glob("*.song")
        projects = []

        skipped_autosaves = 0

        for p in sorted(pattern_iter):
            # Avoid scanning previous consolidated outputs.
            parts_lower = [part.lower() for part in p.parts]
            if any("_consolidated" in part for part in parts_lower):
                continue
            if p.name.lower().endswith("_no_bypassed_fx.song"):
                continue
            if self.batch_ignore_autosaves_var.get() and self._is_autosave_or_backup_song(p):
                skipped_autosaves += 1
                continue
            try:
                if is_song_file(p):
                    projects.append(p)
            except Exception:
                continue

        self.batch_projects = projects
        self.batch_tree.delete(*self.batch_tree.get_children())

        for i, p in enumerate(projects):
            self.batch_tree.insert("", "end", iid=str(i), text=str(p), values=("Queued", "", "", ""))

        detail = "Ready to start batch consolidate." if projects else "No projects found."
        if self.batch_ignore_autosaves_var.get():
            detail += f" Skipped autosaves/backups: {skipped_autosaves}."
        self.batch_status_var.set(f"Found {len(projects)} .song project(s).")
        self.batch_detail_var.set(detail)

    def start_batch_consolidate(self):
        if self.batch_running:
            messagebox.showinfo("Batch", "Batch is already running.")
            return

        if not self.batch_projects:
            self.scan_batch_projects()

        if not self.batch_projects:
            messagebox.showinfo("Batch", "No projects to consolidate.")
            return

        output_parent = Path(self.batch_output_var.get().strip())
        if not output_parent.exists() or not output_parent.is_dir():
            messagebox.showerror("Output folder", "Select a valid output folder.")
            return

        summary = [
            "Start Batch Consolidate?",
            "",
            f"Projects: {len(self.batch_projects)}",
            f"Output parent: {output_parent}",
            f"Ignore autosaves/backups: {'yes' if self.batch_ignore_autosaves_var.get() else 'no'}",
            "",
            "Original projects will NOT be modified.",
            "Mode: PRESERVE_FOLDERS.",
            "",
            f"Recover missing: {'yes' if self.batch_recover_missing_var.get() else 'no'}",
            f"Copy unused side files: {'yes' if self.batch_copy_unused_var.get() else 'no'}",
            f"Copy duplicate side files: {'yes' if self.batch_copy_duplicates_var.get() else 'no'}",
            f"Create no-bypassed-FX copies: {'yes' if self.batch_create_no_bypassed_var.get() else 'no'}",
        ]

        if not messagebox.askyesno("Batch Consolidate", "\n".join(summary) + "\n\nContinue?"):
            return

        while not self.batch_queue.empty():
            try:
                self.batch_queue.get_nowait()
            except Exception:
                break

        self.batch_running = True
        self.batch_progress.configure(maximum=len(self.batch_projects), value=0)
        self.batch_status_var.set("Batch starting...")
        self.batch_detail_var.set("")

        options = {
            "projects": list(self.batch_projects),
            "output_parent": output_parent,
            "recover_missing": self.batch_recover_missing_var.get(),
            "copy_unused": self.batch_copy_unused_var.get(),
            "copy_duplicates": self.batch_copy_duplicates_var.get(),
            "create_no_bypassed": self.batch_create_no_bypassed_var.get(),
            "media_folder_name": self.settings.get("media_folder_name", "Media"),
            "duplicates_folder_name": self.settings.get("duplicates_folder_name", "_Duplicates"),
            "unused_folder_name": self.settings.get("unused_folder_name", "_Unused"),
            "es_path": self.everything_es_path_var.get().strip() or self.settings.get("everything_es_path") or None,
            "instance": None,
            "mode": "default",
        }

        self.batch_worker = threading.Thread(target=self._batch_worker_entry, args=(options,), daemon=True)
        self.batch_worker.start()
        self.after(150, self._poll_batch_queue)

    def _batch_worker_entry(self, options: dict):
        projects = options["projects"]
        total = len(projects)

        for idx, song_path in enumerate(projects):
            try:
                self.batch_queue.put({
                    "type": "project_start",
                    "index": idx,
                    "project": str(song_path),
                    "message": f"Analyzing {song_path.name}",
                })

                project_dir = song_path.parent
                archive_texts = self._read_song_archive_texts_for_batch(song_path)
                audio_rows = build_audio_table_rows(project_dir, archive_texts)

                missing_count = sum(1 for r in audio_rows if r.status == "MISSING")
                missing_candidates = []

                if options["recover_missing"] and missing_count:
                    self.batch_queue.put({
                        "type": "project_status",
                        "index": idx,
                        "status": "Searching missing",
                        "detail": f"{song_path.name}: {missing_count} missing references",
                    })

                    es = find_es_exe(options["es_path"])
                    if es:
                        missing_candidates = search_missing_candidates_everything(
                            audio_rows,
                            max_results_per_file=12,
                            es_path=options["es_path"],
                            instance=options["instance"],
                            mode=options.get("mode") or "auto",
                        )
                    else:
                        self.batch_queue.put({
                            "type": "project_status",
                            "index": idx,
                            "status": "Missing search skipped",
                            "detail": "es.exe not found",
                        })

                def progress_callback(payload, project_index=idx, project_name=song_path.name):
                    self.batch_queue.put({
                        "type": "project_progress",
                        "index": project_index,
                        "project_name": project_name,
                        **payload,
                    })

                result = consolidate_project_to_new_folder(
                    song_path=song_path,
                    project_dir=project_dir,
                    output_parent=options["output_parent"],
                    audio_rows=audio_rows,
                    missing_candidates=missing_candidates,
                    media_folder_name=options["media_folder_name"],
                    duplicates_folder_name=options["duplicates_folder_name"],
                    unused_folder_name=options["unused_folder_name"],
                    recover_missing=options["recover_missing"],
                    copy_unused=options["copy_unused"],
                    copy_duplicate_nonreferenced=options["copy_duplicates"],
                    remove_bypassed_fx=False,
                    progress_callback=progress_callback,
                )

                if options["create_no_bypassed"]:
                    self.batch_queue.put({
                        "type": "project_status",
                        "index": idx,
                        "status": "Creating no-bypassed copy",
                        "detail": result.output_song.name,
                    })
                    clean_song = result.output_dir / f"{result.output_song.stem}_no_bypassed_fx.song"
                    remove_bypassed_fx(result.output_song, clean_song)
                    setattr(result, "no_bypassed_song", clean_song)

                self.batch_queue.put({
                    "type": "project_done",
                    "index": idx,
                    "result": result,
                    "audio_count": len([r for r in audio_rows if r.reference]),
                    "missing_count": missing_count,
                    "done_count": idx + 1,
                    "total": total,
                })
            except Exception as e:
                self.batch_queue.put({
                    "type": "project_error",
                    "index": idx,
                    "project": str(song_path),
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                    "done_count": idx + 1,
                    "total": total,
                })

        self.batch_queue.put({"type": "batch_done", "total": total})

    def _poll_batch_queue(self):
        try:
            while True:
                msg = self.batch_queue.get_nowait()
                self._handle_batch_message(msg)
        except queue.Empty:
            pass

        if self.batch_running:
            self.after(150, self._poll_batch_queue)

    def _handle_batch_message(self, msg: dict):
        mtype = msg.get("type")
        idx = msg.get("index")

        if mtype == "project_start":
            if self.batch_tree.exists(str(idx)):
                self.batch_tree.set(str(idx), "status", "Analyzing")
            self.batch_status_var.set(msg.get("message", "Analyzing..."))
            self.batch_detail_var.set(msg.get("project", ""))
            return

        if mtype == "project_status":
            if idx is not None and self.batch_tree.exists(str(idx)):
                self.batch_tree.set(str(idx), "status", msg.get("status", "Working"))
            self.batch_status_var.set(msg.get("status", "Working"))
            self.batch_detail_var.set(msg.get("detail", ""))
            return

        if mtype == "project_progress":
            phase = msg.get("phase", "")
            project_name = msg.get("project_name", "")
            message = msg.get("message") or phase
            if idx is not None and self.batch_tree.exists(str(idx)):
                self.batch_tree.set(str(idx), "status", message[:40])
            self.batch_status_var.set(f"{project_name}: {message}")

            current_file = msg.get("current_file", "")
            total_bytes = msg.get("total_bytes") or msg.get("current_total_bytes") or 0
            copied = msg.get("current_copied_bytes") or 0
            if current_file:
                self.batch_detail_var.set(f"Current: {current_file} | {self._format_bytes_local(copied)} / {self._format_bytes_local(total_bytes)}")
            else:
                self.batch_detail_var.set("")
            return

        if mtype == "project_done":
            result = msg.get("result")
            missing_after = getattr(result, "verify_missing_after", 0)
            status = "Done" if not missing_after else f"WARN missing {missing_after}"
            if self.batch_tree.exists(str(idx)):
                self.batch_tree.set(str(idx), "status", status)
                self.batch_tree.set(str(idx), "audio", str(msg.get("audio_count", "")))
                self.batch_tree.set(str(idx), "missing", f"{msg.get('missing_count', '')} -> {missing_after}")
                self.batch_tree.set(str(idx), "output", str(result.output_dir))
            self.batch_progress.configure(value=msg.get("done_count", 0))
            self.batch_status_var.set(f"{status} {msg.get('done_count')}/{msg.get('total')}: {result.output_dir.name}")
            self.batch_detail_var.set(str(result.output_song))
            return

        if mtype == "project_error":
            if self.batch_tree.exists(str(idx)):
                self.batch_tree.set(str(idx), "status", "ERROR")
                self.batch_tree.set(str(idx), "output", msg.get("error", ""))
            self.batch_progress.configure(value=msg.get("done_count", 0))
            self.batch_status_var.set(f"Error in project {idx + 1}: {msg.get('error', '')}")

            try:
                log_path = Path.cwd() / "batch_error_log.txt"
                with log_path.open("a", encoding="utf-8") as f:
                    f.write("\n" + "=" * 80 + "\n")
                    f.write(msg.get("project", "") + "\n")
                    f.write(msg.get("error", "") + "\n")
                    f.write(msg.get("traceback", "") + "\n")
            except Exception:
                pass
            return

        if mtype == "batch_done":
            self.batch_running = False
            self.batch_status_var.set(f"Batch complete. Projects processed: {msg.get('total', 0)}")
            self.batch_detail_var.set("Check output folders and reports.")
            messagebox.showinfo("Batch complete", f"Batch complete.\nProjects processed: {msg.get('total', 0)}")



    # -------------------------
    # Crash Analyzer
    # -------------------------


    def select_studio_one_data_root(self):
        folder = filedialog.askdirectory(title="Select exact dumps/logs folder or Studio One data folder")
        if folder:
            self.studio_one_data_root_var.set(folder)
            self.settings["studio_one_data_root"] = folder
            save_settings(self.settings)
            self.refresh_crash_files()

    def clear_studio_one_data_root(self):
        self.studio_one_data_root_var.set("")
        self.settings["studio_one_data_root"] = ""
        save_settings(self.settings)
        self.refresh_crash_files()


    def diagnose_crash_roots(self):
        try:
            text = crash_roots_diagnostics(self.studio_one_data_root_var.get().strip() or None)
            if hasattr(self, "crash_result_text"):
                self.crash_result_text.delete("1.0", "end")
                self.crash_result_text.insert("1.0", text)
                self.apply_crash_result_highlighting()
            self.crash_status_var.set("Crash/log roots diagnostics complete.")
        except Exception as e:
            self.show_error("Crash/log roots diagnostics failed", e)




    def _sort_crash_files_current(self):
        key = getattr(self, "crash_sort_key", "mtime")
        reverse = getattr(self, "crash_sort_reverse", True)

        def sort_value(cf):
            if key == "mtime":
                return cf.mtime
            if key == "size":
                return cf.size
            if key == "kind":
                return cf.kind.lower()
            if key == "file":
                return cf.path.name.lower()
            if key == "path":
                return str(cf.path).lower()
            return cf.mtime

        self.crash_files.sort(key=sort_value, reverse=reverse)

    def sort_crash_files_by(self, key: str):
        if getattr(self, "crash_sort_key", None) == key:
            self.crash_sort_reverse = not getattr(self, "crash_sort_reverse", True)
        else:
            self.crash_sort_key = key
            # Default for date/size is newest/biggest first; text columns A-Z.
            self.crash_sort_reverse = True if key in {"mtime", "size"} else False

        self._sort_crash_files_current()
        self.populate_crash_tree()

    def populate_crash_tree(self):
        if not hasattr(self, "crash_tree"):
            return

        import datetime
        self.crash_tree.delete(*self.crash_tree.get_children())

        for i, cf in enumerate(self.crash_files):
            modified = datetime.datetime.fromtimestamp(cf.mtime).strftime("%Y-%m-%d %H:%M:%S")
            self.crash_tree.insert(
                "",
                "end",
                iid=str(i),
                text=cf.path.name,
                values=(cf.kind, format_crash_size(cf.size), modified, str(cf.mtime), str(cf.path)),
            )

    def refresh_crash_files(self):
        try:
            self.crash_dump_dirs, self.crash_log_dirs = discover_studio_one_crash_dirs(self.studio_one_data_root_var.get().strip() or None)
            self.crash_files = collect_crash_files(self.crash_dump_dirs, self.crash_log_dirs, limit=200)
        except Exception as e:
            self.show_error("Crash file discovery failed", e)
            return

        self._sort_crash_files_current()
        if hasattr(self, "crash_tree"):
            self.populate_crash_tree()


        manual_root = self.studio_one_data_root_var.get().strip()
        active_root = manual_root or "%LOCALAPPDATA%\\CrashDumps + Fender Studio Pro + Studio One roots"
        mode = "manual root only" if manual_root else "auto"
        self.crash_status_var.set(
            f"Mode: {mode} | Root: {active_root} | Found crash files: {len(self.crash_files)} | dump folders: {len(self.crash_dump_dirs)} | log folders: {len(self.crash_log_dirs)} | analyzer: built-in dump reader"
        )



    def configure_crash_text_tags(self):
        try:
            self.crash_result_text.tag_configure("crash_hiscore", foreground="red")
            self.crash_result_text.tag_configure("crash_section", foreground="#003366")
        except Exception:
            pass

    def apply_crash_result_highlighting(self):
        """
        Highlight strongest evidence in Crash Analyzer output.

        Rules:
        - max score= is highlighted red for project FX matches;
        - max rank= is highlighted red for generic non-system module evidence;
        - works without vendor hardcoding.
        """
        try:
            self.configure_crash_text_tags()
            text_widget = self.crash_result_text
            text_widget.tag_remove("crash_hiscore", "1.0", "end")
            text_widget.tag_remove("crash_section", "1.0", "end")

            full_text = text_widget.get("1.0", "end-1c")
            scores = [int(x) for x in re.findall(r"\bscore=(\d+)\b", full_text)]
            ranks = [int(x) for x in re.findall(r"\brank=(\d+)\b", full_text)]
            max_score = max(scores) if scores else None
            max_rank = max(ranks) if ranks else None

            lines = full_text.splitlines()
            heading_markers = (
                "Project FX matched",
                "Top Exception Thread",
                "Top All-Thread",
                "Loaded project FX",
                "Loaded Module Counts",
                "Exception",
                "Fault surface module",
            )

            for line_no, line in enumerate(lines, start=1):
                stripped = line.strip()

                if any(stripped.startswith(m) for m in heading_markers):
                    text_widget.tag_add("crash_section", f"{line_no}.0", f"{line_no}.end")

                hit = False
                if max_score is not None and re.search(rf"\bscore={max_score}\b", line):
                    hit = True
                if max_rank is not None and re.search(rf"\brank={max_rank}\b", line):
                    hit = True

                if hit:
                    text_widget.tag_add("crash_hiscore", f"{line_no}.0", f"{line_no}.end")

            parts = []
            if max_score is not None:
                parts.append(f"score={max_score}")
            if max_rank is not None:
                parts.append(f"rank={max_rank}")
            if parts:
                self.crash_status_var.set("Highlighted: " + ", ".join(parts))
            else:
                self.crash_status_var.set("No score= or rank= values found to highlight.")
        except Exception as e:
            try:
                self.crash_status_var.set(f"Highlight failed: {e}")
            except Exception:
                pass


    def _selected_crash_file(self):
        sel = self.crash_tree.selection() if hasattr(self, "crash_tree") else []
        if not sel:
            return None
        idx = int(sel[0])
        if idx < 0 or idx >= len(self.crash_files):
            return None
        return self.crash_files[idx]

    def analyze_latest_crash_file(self):
        if not self.crash_files:
            self.refresh_crash_files()
        if not self.crash_files:
            messagebox.showinfo("Crash Analyzer", "No Studio One dump/log files found.")
            return
        self.crash_sort_key = "mtime"
        self.crash_sort_reverse = True
        self._sort_crash_files_current()
        self.populate_crash_tree()
        self.crash_tree.selection_set("0")
        self.crash_tree.focus("0")
        self.analyze_selected_crash_file()

    def analyze_selected_crash_file(self):
        cf = self._selected_crash_file()
        if not cf:
            messagebox.showinfo("Crash Analyzer", "Select a dump/log file first.")
            return

        self.crash_status_var.set(f"Analyzing: {cf.path.name}")
        self.update_idletasks()

        try:
            output_dir = cf.path.parent / "StudioOneProjectTools_CrashReports"
            result = analyze_crash_file(
                cf.path,
                plugins=self.plugins,
                output_dir=output_dir,
            )
            txt = result_to_display_text(result)
            self.crash_result_text.delete("1.0", "end")
            self.crash_result_text.insert("1.0", txt)
            self.apply_crash_result_highlighting()
            self.crash_status_var.set(f"Analysis complete: {cf.path.name}")
            if result.report_path:
                self.last_crash_report_folder = result.report_path.parent
        except Exception as e:
            self.show_error("Crash analysis failed", e)

    def copy_crash_result(self):
        if not hasattr(self, "crash_result_text"):
            return
        text = self.crash_result_text.get("1.0", "end").strip()
        self.clipboard_clear()
        self.clipboard_append(text)

    def open_selected_crash_location(self):
        cf = self._selected_crash_file()
        if not cf:
            return
        try:
            os.startfile(str(cf.path.parent))
        except Exception:
            pass

    def open_first_dump_folder(self):
        if not self.crash_dump_dirs:
            self.refresh_crash_files()
        if self.crash_dump_dirs:
            try:
                os.startfile(str(self.crash_dump_dirs[0]))
            except Exception:
                pass
        else:
            messagebox.showinfo("Crash Analyzer", "No Studio One dump folder found.")

    def open_first_log_folder(self):
        if not self.crash_log_dirs:
            self.refresh_crash_files()
        if self.crash_log_dirs:
            try:
                os.startfile(str(self.crash_log_dirs[0]))
            except Exception:
                pass
        else:
            messagebox.showinfo("Crash Analyzer", "No Studio One logs folder found.")

    def open_crash_report_folder(self):
        folder = getattr(self, "last_crash_report_folder", None)
        if folder and Path(folder).exists():
            try:
                os.startfile(str(folder))
            except Exception:
                pass
        else:
            cf = self._selected_crash_file()
            if cf:
                report_dir = cf.path.parent / "StudioOneProjectTools_CrashReports"
                report_dir.mkdir(exist_ok=True)
                try:
                    os.startfile(str(report_dir))
                except Exception:
                    pass


    def update_dashboard(self):
        total_plugins = len(self.plugins)
        bypassed = sum(1 for p in self.plugins if p.bypassed)

        missing = sum(1 for a in self.audio_rows if a.status == "MISSING")
        duplicates = sum(1 for a in self.audio_rows if a.status == "DUPLICATE")
        unused = sum(1 for a in self.audio_rows if a.status == "UNUSED")
        external = sum(1 for a in self.audio_rows if a.status == "EXTERNAL")
        normal = sum(1 for a in self.audio_rows if a.status == "NORMAL")

        score = 100
        score -= min(40, missing * 10)
        score -= min(25, duplicates)
        score -= min(20, unused // 2)
        score -= min(20, bypassed // 5)
        score -= min(10, external)
        score = max(0, score)

        text = f"""Project Health

Project:
{self.song_path}

Plugins:
Total plugins ............ {total_plugins}
Bypassed FX .............. {bypassed}

Audio / Files:
Normal audio ............. {normal}
Missing audio ............ {missing}
Duplicate audio .......... {duplicates}
Unused audio ............. {unused}
External audio ........... {external}

Health Score:
{score} / 100
"""
        self._set_dashboard_text(text)

    def _set_dashboard_text(self, text: str):
        self.dashboard_text.configure(state="normal")
        self.dashboard_text.delete("1.0", "end")
        self.dashboard_text.insert("1.0", text)
        self.dashboard_text.configure(state="disabled")
