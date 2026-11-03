#!/usr/bin/env python3
"""
PDF to JPG Converter
====================
A lightweight Tkinter desktop application that renders every page of a PDF
into a high-quality JPEG image using PyMuPDF.

Author: Your Name
License: MIT
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import fitz  # PyMuPDF


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_TITLE = "PDF to JPG Converter"
APP_VERSION = "1.0.0"

DEFAULT_DPI = 300
DEFAULT_QUALITY = 92

MIN_DPI = 72
MAX_DPI = 600

MIN_QUALITY = 1
MAX_QUALITY = 100

BG_COLOR = "#f4f6f8"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConversionOptions:
    """All parameters required by the background conversion worker."""

    pdf_path: Path
    output_dir: Path
    page_indices: list[int]
    dpi: int
    jpeg_quality: int
    output_stem: str
    password: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def human_size(size_in_bytes: int) -> str:
    """Return a human-readable file size."""
    size = float(size_in_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def safe_stem(name: str) -> str:
    """Remove characters that are invalid in Windows file names."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned or "document"


def parse_page_range(expression: str, total_pages: int) -> list[int]:
    """
    Parse a 1-based page-range expression into sorted 0-based page indices.

    Supported examples:
        "" or "all"      -> all pages
        "1-3, 5, 8-10"   -> pages 1, 2, 3, 5, 8, 9 and 10
        "4"              -> page 4 only

    Raises:
        ValueError: if the expression is invalid or outside the document.
    """
    if total_pages <= 0:
        raise ValueError("The PDF does not contain any pages.")

    expression = expression.strip().lower()
    if expression in ("", "all", "*"):
        return list(range(total_pages))

    indices: set[int] = set()

    for raw_token in expression.split(","):
        token = raw_token.strip()
        if not token:
            continue

        if "-" in token:
            parts = token.split("-", 1)
            if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                raise ValueError(f"Invalid page range segment: '{token}'.")

            try:
                start = int(parts[0])
                end = int(parts[1])
            except ValueError as exc:
                raise ValueError(f"Invalid page range segment: '{token}'.") from exc

            if start > end:
                raise ValueError(f"Page range start must be <= end: '{token}'.")
            if start < 1 or end > total_pages:
                raise ValueError(
                    f"Page range '{token}' is outside the valid range 1-{total_pages}."
                )

            indices.update(range(start - 1, end))

        else:
            try:
                page_number = int(token)
            except ValueError as exc:
                raise ValueError(f"Invalid page number: '{token}'.") from exc

            if page_number < 1 or page_number > total_pages:
                raise ValueError(
                    f"Page {page_number} is outside the valid range 1-{total_pages}."
                )

            indices.add(page_number - 1)

    if not indices:
        raise ValueError("No pages were selected.")

    return sorted(indices)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class PDFToJPGApp:
    """Tkinter GUI for converting PDF pages to JPEG images."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_TITLE} v{APP_VERSION}")
        self.root.geometry("860x720")
        self.root.minsize(760, 620)
        self.root.configure(bg=BG_COLOR)

        # Tkinter variables
        self.pdf_path_var = tk.StringVar()
        self.pdf_info_var = tk.StringVar(value="No file selected.")
        self.output_dir_var = tk.StringVar()
        self.dpi_var = tk.StringVar(value=str(DEFAULT_DPI))
        self.quality_var = tk.IntVar(value=DEFAULT_QUALITY)
        self.page_range_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready. Select a PDF file to begin.")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.percent_var = tk.StringVar(value="0%")

        # Threading / cancellation state
        self._queue: queue.Queue = queue.Queue()
        self._cancel_event = threading.Event()
        self._is_running = False

        self._configure_styles()
        self._build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_queue)

        self._log("Ready. Select a PDF file to start.")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            ".",
            background=BG_COLOR,
            foreground="#1f2937",
            font=("Segoe UI", 10),
        )
        style.configure("TFrame", background=BG_COLOR)
        style.configure("TLabel", background=BG_COLOR)
        style.configure(
            "Title.TLabel",
            font=("Segoe UI", 20, "bold"),
            foreground="#111827",
        )
        style.configure(
            "Subtitle.TLabel",
            font=("Segoe UI", 10),
            foreground="#6b7280",
        )
        style.configure(
            "Hint.TLabel",
            font=("Segoe UI", 9),
            foreground="#6b7280",
        )
        style.configure(
            "TLabelframe",
            background=BG_COLOR,
            bordercolor="#d1d5db",
            relief="solid",
        )
        style.configure(
            "TLabelframe.Label",
            background=BG_COLOR,
            foreground="#374151",
            font=("Segoe UI", 10, "bold"),
        )
        style.configure("TButton", padding=(12, 6), font=("Segoe UI", 10))
        style.configure(
            "Accent.TButton",
            font=("Segoe UI", 10, "bold"),
            foreground="#ffffff",
            background="#2563eb",
        )
        style.map(
            "Accent.TButton",
            background=[
                ("active", "#1d4ed8"),
                ("disabled", "#93c5fd"),
            ],
            foreground=[("disabled", "#eff6ff")],
        )
        style.configure("TEntry", fieldbackground="#ffffff", padding=6)
        style.configure("TCombobox", fieldbackground="#ffffff", padding=4)
        style.configure("TSpinbox", fieldbackground="#ffffff", padding=4)
        style.configure(
            "Horizontal.TProgressbar",
            background="#2563eb",
            troughcolor="#e5e7eb",
            bordercolor="#e5e7eb",
            lightcolor="#2563eb",
            darkcolor="#2563eb",
        )

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=20)
        container.pack(fill="both", expand=True)

        # Header -------------------------------------------------------
        header = ttk.Frame(container)
        header.pack(fill="x")
        header.columnconfigure(0, weight=1)

        ttk.Label(
            header,
            text=APP_TITLE,
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            header,
            text=f"v{APP_VERSION}",
            style="Hint.TLabel",
        ).grid(row=0, column=1, sticky="e")

        ttk.Label(
            header,
            text="Convert every page of a PDF into a high-quality JPEG image.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # Source PDF ---------------------------------------------------
        source_frame = ttk.LabelFrame(container, text="Source PDF", padding=(12, 10))
        source_frame.pack(fill="x", pady=(16, 8))
        source_frame.columnconfigure(1, weight=1)

        ttk.Label(source_frame, text="PDF file:").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Entry(
            source_frame,
            textvariable=self.pdf_path_var,
            state="readonly",
        ).grid(row=0, column=1, sticky="ew")

        ttk.Button(
            source_frame,
            text="Browse...",
            command=self._browse_pdf,
        ).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(
            source_frame,
            textvariable=self.pdf_info_var,
            style="Hint.TLabel",
        ).grid(row=1, column=1, columnspan=2, sticky="w", pady=(4, 0))

        # Output folder ------------------------------------------------
        output_frame = ttk.LabelFrame(container, text="Output Folder", padding=(12, 10))
        output_frame.pack(fill="x", pady=8)
        output_frame.columnconfigure(1, weight=1)

        ttk.Label(output_frame, text="Save images to:").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Entry(
            output_frame,
            textvariable=self.output_dir_var,
        ).grid(row=0, column=1, sticky="ew")

        ttk.Button(
            output_frame,
            text="Browse...",
            command=self._browse_output_dir,
        ).grid(row=0, column=2, padx=(8, 0))

        # Conversion settings ------------------------------------------
        settings = ttk.LabelFrame(
            container,
            text="Conversion Settings",
            padding=(12, 10),
        )
        settings.pack(fill="x", pady=8)
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="Resolution (DPI):").grid(
            row=0, column=0, sticky="w", pady=4, padx=(0, 8)
        )
        ttk.Combobox(
            settings,
            textvariable=self.dpi_var,
            values=[str(value) for value in (72, 150, 200, 300, 400, 600)],
            state="readonly",
            width=8,
        ).grid(row=0, column=1, sticky="w", pady=4)

        ttk.Label(
            settings,
            text="Higher DPI = sharper output and larger files.",
            style="Hint.TLabel",
        ).grid(row=0, column=2, sticky="w", padx=(12, 0))

        ttk.Label(settings, text="JPEG quality:").grid(
            row=1, column=0, sticky="w", pady=4, padx=(0, 8)
        )
        ttk.Spinbox(
            settings,
            from_=MIN_QUALITY,
            to=MAX_QUALITY,
            textvariable=self.quality_var,
            width=8,
        ).grid(row=1, column=1, sticky="w", pady=4)

        ttk.Label(
            settings,
            text="1-100. 90-95 is a good balance.",
            style="Hint.TLabel",
        ).grid(row=1, column=2, sticky="w", padx=(12, 0))

        ttk.Label(settings, text="Page range:").grid(
            row=2, column=0, sticky="w", pady=4, padx=(0, 8)
        )
        ttk.Entry(
            settings,
            textvariable=self.page_range_var,
        ).grid(row=2, column=1, sticky="ew", pady=4)

        ttk.Label(
            settings,
            text="Leave empty for all pages. Example: 1-3, 5, 8-10",
            style="Hint.TLabel",
        ).grid(row=2, column=2, sticky="w", padx=(12, 0))

        # Actions ------------------------------------------------------
        button_frame = ttk.Frame(container)
        button_frame.pack(fill="x", pady=(4, 12))

        self.convert_button = ttk.Button(
            button_frame,
            text="Convert to JPG",
            style="Accent.TButton",
            command=self._start_conversion,
        )
        self.convert_button.pack(side="left")

        self.cancel_button = ttk.Button(
            button_frame,
            text="Cancel",
            command=self._cancel_conversion,
            state="disabled",
        )
        self.cancel_button.pack(side="left", padx=8)

        ttk.Button(
            button_frame,
            text="Open Output Folder",
            command=self._open_output_folder,
        ).pack(side="right")

        # Progress -----------------------------------------------------
        progress_frame = ttk.Frame(container)
        progress_frame.pack(fill="x", pady=(0, 8))
        progress_frame.columnconfigure(0, weight=1)

        ttk.Progressbar(
            progress_frame,
            variable=self.progress_var,
            maximum=100,
            mode="determinate",
        ).grid(row=0, column=0, sticky="ew")

        ttk.Label(
            progress_frame,
            textvariable=self.percent_var,
            style="Hint.TLabel",
            width=14,
            anchor="e",
        ).grid(row=0, column=1, padx=(8, 0))

        ttk.Label(
            container,
            textvariable=self.status_var,
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        # Log ----------------------------------------------------------
        log_frame = ttk.LabelFrame(container, text="Log", padding=(8, 8))
        log_frame.pack(fill="both", expand=True, pady=(8, 0))

        self.log_text = tk.Text(
            log_frame,
            height=10,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
            bg="#ffffff",
            fg="#1f2937",
            insertbackground="#1f2937",
            selectbackground="#bfdbfe",
            relief="solid",
            borderwidth=1,
            highlightthickness=0,
            padx=8,
            pady=6,
        )
        scrollbar = ttk.Scrollbar(
            log_frame,
            orient="vertical",
            command=self.log_text.yview,
        )
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.log_text.tag_configure("error", foreground="#b91c1c")
        self.log_text.tag_configure("success", foreground="#15803d")
        self.log_text.tag_configure("info", foreground="#6b7280")

    # ------------------------------------------------------------------
    # File / folder selection
    # ------------------------------------------------------------------

    def _browse_pdf(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Select a PDF file",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not file_path:
            return

        pdf_path = Path(file_path).expanduser()

        try:
            document = fitz.open(str(pdf_path))
            try:
                page_count = document.page_count
                encrypted = document.needs_pass
            finally:
                document.close()
        except Exception as exc:
            messagebox.showerror(
                "Cannot open PDF",
                f"Could not read the selected PDF.\n\n{exc}",
            )
            self._log(f"Error reading PDF: {exc}", "error")
            return

        self.pdf_path_var.set(str(pdf_path))

        if not self.output_dir_var.get().strip():
            default_output = pdf_path.with_name(f"{pdf_path.stem}_pages")
            self.output_dir_var.set(str(default_output))

        info = f"{page_count} page(s) - {human_size(pdf_path.stat().st_size)}"
        if encrypted:
            info += " - password-protected"

        self.pdf_info_var.set(info)
        self._log(f"Selected: {pdf_path.name} ({info})")

    def _browse_output_dir(self) -> None:
        folder = filedialog.askdirectory(title="Select an output folder")
        if folder:
            self.output_dir_var.set(folder)

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def _start_conversion(self) -> None:
        pdf_path = Path(self.pdf_path_var.get().strip()).expanduser()
        if not pdf_path.is_file():
            messagebox.showerror(
                "No PDF selected",
                "Please select a valid PDF file first.",
            )
            return

        if pdf_path.suffix.lower() != ".pdf":
            messagebox.showerror(
                "Invalid file",
                "The selected file is not a PDF.",
            )
            return

        output_text = self.output_dir_var.get().strip()
        if not output_text:
            output_dir = pdf_path.with_name(f"{pdf_path.stem}_pages")
            self.output_dir_var.set(str(output_dir))
        else:
            output_dir = Path(output_text).expanduser()

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(
                "Cannot create output folder",
                f"Could not create the output folder.\n\n{exc}",
            )
            return

        try:
            dpi = int(self.dpi_var.get())
            quality = int(self.quality_var.get())
        except (ValueError, tk.TclError):
            messagebox.showerror(
                "Invalid settings",
                "DPI and JPEG quality must be valid numbers.",
            )
            return

        if not MIN_DPI <= dpi <= MAX_DPI:
            messagebox.showerror(
                "Invalid DPI",
                f"DPI must be between {MIN_DPI} and {MAX_DPI}.",
            )
            return

        if not MIN_QUALITY <= quality <= MAX_QUALITY:
            messagebox.showerror(
                "Invalid JPEG quality",
                f"JPEG quality must be between {MIN_QUALITY} and {MAX_QUALITY}.",
            )
            return

        # Read page count and encryption state before starting the worker.
        try:
            document = fitz.open(str(pdf_path))
            try:
                total_pages = document.page_count
                needs_password = document.needs_pass
            finally:
                document.close()
        except Exception as exc:
            messagebox.showerror(
                "Cannot open PDF",
                f"Could not open the selected PDF.\n\n{exc}",
            )
            return

        try:
            page_indices = parse_page_range(
                self.page_range_var.get(),
                total_pages,
            )
        except ValueError as exc:
            messagebox.showerror("Invalid page range", str(exc))
            return

        password: str | None = None

        if needs_password:
            password = simpledialog.askstring(
                "Password required",
                f"'{pdf_path.name}' is password-protected.\n"
                "Enter the password to continue:",
                show="*",
                parent=self.root,
            )
            if password is None:
                return

            try:
                document = fitz.open(str(pdf_path))
                try:
                    if not document.authenticate(password):
                        messagebox.showerror(
                            "Incorrect password",
                            "The password is incorrect. Please try again.",
                        )
                        return
                finally:
                    document.close()
            except Exception as exc:
                messagebox.showerror(
                    "Cannot unlock PDF",
                    f"Could not unlock the PDF.\n\n{exc}",
                )
                return

        options = ConversionOptions(
            pdf_path=pdf_path,
            output_dir=output_dir,
            page_indices=page_indices,
            dpi=dpi,
            jpeg_quality=quality,
            output_stem=safe_stem(pdf_path.stem),
            password=password,
        )

        self._begin_conversion()
        self._clear_log()
        self._log(
            f"Starting conversion: {len(page_indices)} page(s), "
            f"{dpi} DPI, JPEG quality {quality}."
        )
        self._log(f"Output folder: {output_dir}")

        worker = threading.Thread(
            target=self._convert_worker,
            args=(options,),
            daemon=True,
        )
        worker.start()

    def _convert_worker(self, options: ConversionOptions) -> None:
        """Background worker. Never touches Tkinter widgets directly."""
        document = None

        try:
            document = fitz.open(str(options.pdf_path))

            if document.needs_pass:
                if options.password is None or not document.authenticate(
                    options.password
                ):
                    self._queue.put(
                        (
                            "error",
                            "The PDF is password-protected and could not be unlocked.",
                        )
                    )
                    return

            total = len(options.page_indices)
            success_count = 0
            error_count = 0

            for current, page_index in enumerate(options.page_indices, start=1):
                if self._cancel_event.is_set():
                    self._queue.put(("cancelled", success_count, error_count))
                    return

                try:
                    page = document.load_page(page_index)
                    pixmap = page.get_pixmap(
                        dpi=options.dpi,
                        colorspace=fitz.csRGB,
                        alpha=False,
                    )

                    output_name = (
                        f"{options.output_stem}_page_{page_index + 1:04d}.jpg"
                    )
                    output_path = options.output_dir / output_name

                    jpeg_bytes = pixmap.tobytes(
                        "jpeg",
                        jpg_quality=options.jpeg_quality,
                    )
                    output_path.write_bytes(jpeg_bytes)

                    success_count += 1
                    self._queue.put(
                        (
                            "page_done",
                            current,
                            total,
                            page_index + 1,
                            output_name,
                            pixmap.width,
                            pixmap.height,
                        )
                    )

                except Exception as exc:
                    error_count += 1
                    self._queue.put(
                        (
                            "page_error",
                            current,
                            total,
                            page_index + 1,
                            f"{type(exc).__name__}: {exc}",
                        )
                    )

            self._queue.put(("done", success_count, error_count))

        except Exception as exc:
            self._queue.put(("error", f"{type(exc).__name__}: {exc}"))

        finally:
            if document is not None:
                document.close()

    # ------------------------------------------------------------------
    # Queue polling / progress updates
    # ------------------------------------------------------------------

    def _poll_queue(self) -> None:
        try:
            while True:
                message = self._queue.get_nowait()
                kind = message[0]

                if kind == "page_done":
                    _, current, total, page_number, output_name, width, height = message
                    percent = (current / total) * 100
                    self.progress_var.set(percent)
                    self.percent_var.set(
                        f"{current}/{total} ({percent:.0f}%)"
                    )
                    self.status_var.set(
                        f"Converting page {current} of {total}..."
                    )
                    self._log(
                        f"Page {page_number:>4}: "
                        f"{width}x{height} px -> {output_name}"
                    )

                elif kind == "page_error":
                    _, current, total, page_number, error_text = message
                    percent = (current / total) * 100
                    self.progress_var.set(percent)
                    self.percent_var.set(
                        f"{current}/{total} ({percent:.0f}%)"
                    )
                    self._log(
                        f"Page {page_number:>4}: failed - {error_text}",
                        "error",
                    )

                elif kind == "done":
                    _, success_count, error_count = message
                    self.progress_var.set(100)
                    self.percent_var.set("100%")
                    self._finish_conversion()

                    if error_count:
                        self.status_var.set(
                            f"Completed with errors - "
                            f"{success_count} saved, {error_count} failed."
                        )
                        self._log(
                            f"Conversion finished. "
                            f"{success_count} page(s) saved, "
                            f"{error_count} page(s) failed.",
                            "error",
                        )
                        messagebox.showwarning(
                            "Conversion finished with errors",
                            f"{success_count} page(s) were saved successfully.\n"
                            f"{error_count} page(s) failed.\n\n"
                            "Check the log for details.",
                        )
                    else:
                        self.status_var.set(
                            f"Completed - {success_count} page(s) saved."
                        )
                        self._log(
                            f"Conversion completed successfully. "
                            f"{success_count} page(s) saved to:\n"
                            f"{self.output_dir_var.get()}",
                            "success",
                        )
                        messagebox.showinfo(
                            "Conversion complete",
                            f"{success_count} page(s) were saved as JPG files.\n\n"
                            f"Output folder:\n{self.output_dir_var.get()}",
                        )

                elif kind == "cancelled":
                    _, success_count, error_count = message
                    self.status_var.set("Cancelled.")
                    self._finish_conversion()
                    self._log(
                        f"Conversion cancelled. "
                        f"{success_count} page(s) were saved before cancellation.",
                        "info",
                    )

                elif kind == "error":
                    _, error_text = message
                    self.status_var.set("Failed.")
                    self._finish_conversion()
                    self._log(f"Error: {error_text}", "error")
                    messagebox.showerror(
                        "Conversion failed",
                        error_text,
                    )

        except queue.Empty:
            pass

        finally:
            try:
                self.root.after(100, self._poll_queue)
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _begin_conversion(self) -> None:
        self._is_running = True
        self._cancel_event.clear()
        self.convert_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress_var.set(0)
        self.percent_var.set("0%")
        self.status_var.set("Converting...")

    def _finish_conversion(self) -> None:
        self._is_running = False
        self.convert_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")

    def _cancel_conversion(self) -> None:
        if not self._is_running:
            return
        self._cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.status_var.set("Cancelling...")
        self._log(
            "Cancellation requested. Waiting for the current page to finish...",
            "info",
        )

    def _on_close(self) -> None:
        if self._is_running:
            should_quit = messagebox.askyesno(
                "Conversion in progress",
                "A conversion is still running.\n"
                "Cancel it and quit?",
            )
            if not should_quit:
                return
            self._cancel_event.set()
        self.root.destroy()

    # ------------------------------------------------------------------
    # Miscellaneous
    # ------------------------------------------------------------------

    def _open_output_folder(self) -> None:
        folder_text = self.output_dir_var.get().strip()
        if not folder_text:
            messagebox.showwarning(
                "No output folder",
                "Please select an output folder first.",
            )
            return

        folder = Path(folder_text).expanduser()
        if not folder.is_dir():
            messagebox.showwarning(
                "Folder not found",
                "The output folder does not exist yet.",
            )
            return

        try:
            if sys.platform.startswith("win"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            messagebox.showerror(
                "Cannot open folder",
                f"Could not open the output folder.\n\n{exc}",
            )

    def _log(self, message: str, tag: str | None = None) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n", tag or "")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    root = tk.Tk()
    PDFToJPGApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()