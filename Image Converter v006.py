#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DesignUplift Image Processor — PySide6 App (v1.7, Exe Persistence Fix)

CHANGES:
- Fixed SETTINGS_PATH logic to work with PyInstaller --onefile builds.
- Settings (config.json) now reliably save next to the .exe or .py file.
"""

from __future__ import annotations

import os
import sys
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Iterable, Set, Optional

from PySide6 import QtCore, QtGui, QtWidgets

# Pillow
from PIL import Image, ImageOps, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True

# Optional HEIC/HEIF support
try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except Exception:
    HEIF_AVAILABLE = False

APP_ORG = "DesignUplift"
APP_NAME = "ImageProcessor"

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif", ".heic", ".heif"}

# ============================================================================
#  🎨 DESIGN SYSTEM (THEME CONFIG)
# ============================================================================
THEME = {
    "colors": {
        "window_bg":    "#202020",
        "surface_bg":   "#2D2D2D",
        "surface_hover":"#383838",
        "primary":      "#2563EB",
        "primary_hover":"#1D4ED8",
        "success":      "#198F51",
        "success_hover":"#14703F",
        "text_main":    "#FFFFFF",
        "text_muted":   "#A1A1AA",
        "border_drop":  "#4B5563",
    },
    "dimens": {
        "radius":       "8px",
        "btn_height":   "38px",
        "lg_btn_height":"48px",
    """
    if getattr(sys, 'frozen', False):
        # Running as compiled .exe
        return Path(sys.executable).parent
    else:
        # Running as .py script
        return Path(__file__).parent.resolve()

# Save config.json next to the executable/script
SETTINGS_PATH = get_base_path() / "config.json"

def get_downloads_dir() -> Path:
    path = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.DownloadLocation)
    return Path(path) if path else Path.home()

def human_bytes(n: int) -> str:
    step = 1024.0
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < step:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= step
    return f"{n:.1f} PB"

# ----------------------------- Drop Zone ------------------------------------
class DropZone(QtWidgets.QFrame):
    filesDropped = QtCore.Signal(list)
    browseFiles = QtCore.Signal()
    browseFolder = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("widgetDropzone")
        self.setAcceptDrops(True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(160) 
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._hover = False
        self.setToolTip("Drop files or folders")

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)
        r = px("radius")
        
        # Background
        p.setBrush(QtGui.QColor(THEME["colors"]["surface_bg"]))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, r, r)
        
        # Dashed Border
        border_col = QtGui.QColor(THEME["colors"]["primary"]) if self._hover else QtGui.QColor(THEME["colors"]["border_drop"])
        pen = QtGui.QPen(border_col, 2, QtCore.Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, r, r)
        
        # Text
        p.setPen(QtGui.QColor(THEME["colors"]["text_main"]))
        font = self.font()
        font.setFamily(THEME["font_family"])
        font.setBold(True)
        font.setPixelSize(14)
        p.setFont(font)
        
        text_rect = rect.adjusted(0, -10, 0, -10)
        p.drawText(text_rect, QtCore.Qt.AlignmentFlag.AlignCenter, "Drop files or folders here")
        
        # Subtext
        p.setPen(QtGui.QColor(THEME["colors"]["text_muted"]))
        font.setBold(False)
        font.setPixelSize(12)
        p.setFont(font)
        sub_rect = rect.adjusted(0, 20, 0, 20)
        p.drawText(sub_rect, QtCore.Qt.AlignmentFlag.AlignCenter, "— or click to browse —")

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction(); self._hover = True; self.update()
        else:
            e.ignore()

    def dragLeaveEvent(self, e: QtGui.QDragLeaveEvent) -> None:
        self._hover = False; self.update()

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        self._hover = False; self.update()
        urls = e.mimeData().urls()
        paths = []
        for u in urls:
            try:
                paths.append(u.toLocalFile())
            except Exception:
                pass
        if paths:
            self.filesDropped.emit(paths)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == QtCore.Qt.MouseButton.RightButton:
            self.browseFolder.emit()
        elif e.button() == QtCore.Qt.MouseButton.LeftButton:
            self.browseFiles.emit()
        super().mouseReleaseEvent(e)

# ----------------------------- Parallel Worker -------------------------------
@dataclass
class ConvertSettings:
    fmt: str
    quality: int
    width: Optional[int]
    outdir: Path
    downscale_only: bool = True
    open_when_done: bool = True

class WorkerSignals(QtCore.QObject):
    # Result: (success, skipped, error, bytes_in, bytes_out, source_path)
    result = QtCore.Signal(bool, bool, bool, int, int, Path)

class ConvertTask(QtCore.QRunnable):
    def __init__(self, src: Path, settings: ConvertSettings):
        super().__init__()
        self.src = src
        self.s = settings
        self.signals = WorkerSignals()

    def run(self):
        src = self.src
        bytes_in = 0
        bytes_out = 0
        
        try:
            bytes_in = src.stat().st_size
        except Exception:
            pass

        if src.suffix.lower() not in SUPPORTED_EXTS:
            self.signals.result.emit(False, True, False, bytes_in, 0, src)
            return

        try:
            with Image.open(src) as im:
                im.load()

            with Image.open(src) as im:
                try:
                    im = ImageOps.exif_transpose(im)
                except Exception:
                    pass

                target_width = self.s.width or 0
                if target_width and (not self.s.downscale_only or im.width > target_width):
                    ratio = target_width / float(im.width)
                    new_size = (target_width, max(1, int(im.height * ratio)))
                    im = im.resize(new_size, Image.Resampling.LANCZOS)

                fmt = self.s.fmt.lower()
                ext = ".jpg" if fmt == "jpeg" else (".png" if fmt == "png" else ".webp")

                suffix_str = f"_{fmt.upper()}_{self.s.quality}"
                stem = src.stem + suffix_str
                out = self._unique_path(self.s.outdir / f"{stem}{ext}")

                save_kwargs = {}
                icc = im.info.get("icc_profile")
                if icc:
                    save_kwargs["icc_profile"] = icc
                
                exif = im.info.get("exif")
                if exif:
                    save_kwargs["exif"] = exif

                if fmt == "jpeg":
                    if im.mode in ("RGBA", "LA", "P"):
                        im = im.convert("RGB")
                    elif im.mode not in ("RGB", "L"):
                        im = im.convert("RGB")
                    q = int(self.s.quality)
                    save_kwargs.update(dict(format="JPEG", quality=q, optimize=True,
                                            progressive=True, subsampling=1 if q < 85 else 0))
                elif fmt == "png":
                    save_kwargs.update(dict(format="PNG", optimize=True, compress_level=6))
                else:  # webp
                    q = int(self.s.quality)
                    save_kwargs.update(dict(format="WEBP", quality=q, method=6))

                im.save(out, **save_kwargs)

                try:
                    bytes_out = Path(out).stat().st_size
                except Exception:
                    pass

            self.signals.result.emit(True, False, False, bytes_in, bytes_out, src)

        except Exception as e:
            # We could emit the error message too, but for now just fail
            print(f"Error converting {src}: {e}")
            self.signals.result.emit(False, False, True, bytes_in, 0, src)

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        base = path.with_suffix("")
        ext = path.suffix
        i = 1
        while True:
            candidate = Path(f"{base}-{i}{ext}")
            if not candidate.exists():
                return candidate
            i += 1

# --------------------------- Main Window ------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Image Processor v006")
        self.setMinimumSize(850, 600)
        self.resize(950, 650)

        self.threadpool = QtCore.QThreadPool()

        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central); root.setContentsMargins(24,24,24,24); root.setSpacing(24)

        # ---------------- LEFT COLUMN ----------------
        left = QtWidgets.QWidget(); left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0,0,0,0); left_layout.setSpacing(12)

        # Drop Zone
        self.dropzone = DropZone()
        left_layout.addWidget(self.dropzone)

        # List Header / Toolbar
        hbox_list = QtWidgets.QHBoxLayout()
        lblList = QtWidgets.QLabel("Queue")
        lblList.setObjectName("lblSection")
        hbox_list.addWidget(lblList)
        hbox_list.addStretch()
        
        self.btnRemove = QtWidgets.QPushButton("Remove Selected")
        self.btnRemove.setObjectName("btnSmall")
        self.btnRemove.clicked.connect(self._remove_selected)
        hbox_list.addWidget(self.btnRemove)

        self.btnClear = QtWidgets.QPushButton("Clear All")
        self.btnClear.setObjectName("btnSmall")
        self.btnClear.clicked.connect(self._clear_queue)
        hbox_list.addWidget(self.btnClear)
        
        left_layout.addLayout(hbox_list)

        # File List (TreeWidget)
        self.treeFiles = QtWidgets.QTreeWidget()
        self.treeFiles.setHeaderLabels(["File", "Size", "Status"])
        self.treeFiles.setColumnWidth(0, 300)
        self.treeFiles.setColumnWidth(1, 80)
        self.treeFiles.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.treeFiles.setRootIsDecorated(False)
        self.treeFiles.setIndentation(0)
        self.treeFiles.setAlternatingRowColors(True)
        left_layout.addWidget(self.treeFiles, 1)

        root.addWidget(left, 6) # 6/10 width

        # ---------------- RIGHT COLUMN ----------------
        right_container = QtWidgets.QWidget(); right_container.setFixedWidth(280)
        right_layout = QtWidgets.QVBoxLayout(right_container)
        right_layout.setContentsMargins(0,0,0,0); right_layout.setSpacing(px("gap_loose"))

        # -- Format --
        box_fmt = QtWidgets.QVBoxLayout()
        box_fmt.setSpacing(px("gap_label"))
        lblFmt = QtWidgets.QLabel("Format"); lblFmt.setObjectName("lblSection")
        box_fmt.addWidget(lblFmt)
        
        hbox_fmt = QtWidgets.QHBoxLayout(); hbox_fmt.setSpacing(px("gap_tight"))
        self._fmt_group = QtWidgets.QButtonGroup(self); self._fmt_group.setExclusive(True)
        self.fmtButtons = []
        for f in ["WEBP", "JPEG", "PNG"]:
            btn = QtWidgets.QToolButton(); btn.setCheckable(True); btn.setText(f); btn.setProperty("seg", True)
            btn.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
            self._fmt_group.addButton(btn)
            hbox_fmt.addWidget(btn)
            self.fmtButtons.append(btn)
            btn.clicked.connect(self._schedule_save)
        self.fmtButtons[1].setChecked(True)
        box_fmt.addLayout(hbox_fmt)
        right_layout.addLayout(box_fmt)

        # -- Quality --
        box_qual = QtWidgets.QVBoxLayout()
        box_qual.setSpacing(px("gap_label"))
        lblQual = QtWidgets.QLabel("Quality"); lblQual.setObjectName("lblSection")
        box_qual.addWidget(lblQual)

        q_grid = QtWidgets.QGridLayout(); q_grid.setSpacing(px("gap_tight"))
        q_grid.setContentsMargins(0,0,0,0)
        self.qualityButtons: List[QtWidgets.QToolButton] = []
        self._quality_group = QtWidgets.QButtonGroup(self); self._quality_group.setExclusive(True)
        
        qualities = [90, 80, 70, 60, 50]
        positions = [(0,0), (0,1), (0,2), (1,0), (1,1)]
        
        for idx, pct in enumerate(qualities):
            r, c = positions[idx]
            btn = QtWidgets.QToolButton(); btn.setCheckable(True); btn.setText(f"{pct}%"); btn.setProperty("seg", True)
            btn.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
            self._quality_group.addButton(btn)
            q_grid.addWidget(btn, r, c)
            btn.clicked.connect(self._schedule_save)
            self.qualityButtons.append(btn)
        box_qual.addLayout(q_grid)
        right_layout.addLayout(box_qual)

        # -- Width --
        box_w = QtWidgets.QVBoxLayout()
        box_w.setSpacing(px("gap_label"))
        lblWidth = QtWidgets.QLabel("Width (px)"); lblWidth.setObjectName("lblSection")
        box_w.addWidget(lblWidth)
        self.txtWidth = QtWidgets.QLineEdit(); self.txtWidth.setPlaceholderText("Original")
        self.txtWidth.setValidator(QtGui.QIntValidator(1, 100000, self)); self.txtWidth.textChanged.connect(self._schedule_save)
        box_w.addWidget(self.txtWidth)
        right_layout.addLayout(box_w)

        # -- Output --
        box_out = QtWidgets.QVBoxLayout()
        box_out.setSpacing(px("gap_label"))
        lblOut = QtWidgets.QLabel("Output"); lblOut.setObjectName("lblSection")
        box_out.addWidget(lblOut)
        
        self.txtOutputPath = QtWidgets.QLineEdit(); self.txtOutputPath.setReadOnly(True)
        self.txtOutputPath.setPlaceholderText("Select folder...")
        box_out.addWidget(self.txtOutputPath)

        # Browse button slightly separated from input
        self.btnBrowseOutput = QtWidgets.QPushButton("Browse")
        self.btnBrowseOutput.setObjectName("btnBrowse")
        
        # Helper layout to separate input and browse button slightly more than label
        box_browse = QtWidgets.QVBoxLayout(); box_browse.setSpacing(px("gap_tight"))
        box_browse.addWidget(self.btnBrowseOutput)
        
        self.chkOpenWhenDone = QtWidgets.QCheckBox("Open folder after convert"); self.chkOpenWhenDone.setChecked(True)
        box_browse.addWidget(self.chkOpenWhenDone)
        
        box_out.addLayout(box_browse)
        right_layout.addLayout(box_out)

        right_layout.addStretch(1)

        # -- Convert Button --
        self.btnConvertPrimary = QtWidgets.QPushButton("CONVERT 0 ITEMS")
        self.btnConvertPrimary.setObjectName("btnConvertPrimary")
        self.btnConvertPrimary.setDefault(True); self.btnConvertPrimary.setEnabled(False)
        right_layout.addWidget(self.btnConvertPrimary)

        root.addWidget(right_container, 4) # 4/10 width

        # Wire up
        self.dropzone.filesDropped.connect(self._ingest_paths)
        self.dropzone.browseFiles.connect(self._add_files_dialog)
        self.dropzone.browseFolder.connect(self._add_folder_dialog)
        self.btnBrowseOutput.clicked.connect(self._choose_output_dir)
        self.btnConvertPrimary.clicked.connect(self._on_convert)

        # Shortcuts
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+O" if sys.platform=="win32" else "Meta+O"), self, activated=self._add_files_dialog)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+K" if sys.platform=="win32" else "Meta+K"), self, activated=lambda: self._clear_queue())
        QtGui.QShortcut(QtGui.QKeySequence("Delete"), self, activated=self._remove_selected)

        # Data + settings
        self._queue_set: Set[Path] = set()
        self._save_timer = QtCore.QTimer(self); self._save_timer.setSingleShot(True); self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._save_settings)

            "default_quality": 90,
            "default_width": "",
            "open_when_done": True,
            "last_output_path": str(get_downloads_dir()),
            "window": {"w": 850, "h": 600, "x": 100, "y": 100},
        }

    def _load_settings(self) -> None:
        # No longer using parent.mkdir, assuming base dir exists for .exe/.py
        data = self._default_settings()
        if SETTINGS_PATH.exists():
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    on_disk = json.load(f)
                data.update(on_disk)
            except Exception:
                pass
        
        # Format Chips
        saved_fmt = data.get("default_format", "JPEG")
        for b in self.fmtButtons:
            if b.text() == saved_fmt:
                b.setChecked(True)

        # Quality Chips
        saved_q = int(data.get("default_quality", 90))
        for b in self.qualityButtons:
            if b.text() == f"{saved_q}%":
                b.setChecked(True)

        self.txtWidth.setText(str(data.get("default_width","")) if data.get("default_width","") else "")
        self.chkOpenWhenDone.setChecked(bool(data.get("open_when_done", True)))
        
        last_out = Path(data.get("last_output_path", str(get_downloads_dir())))
        self.txtOutputPath.setText(str(last_out if last_out.exists() else get_downloads_dir()))
        
        try:
            w = data.get("window", {})
            self.resize(int(w.get("w", 850)), int(w.get("h", 600)))
            self.move(int(w.get("x", 100)), int(w.get("y", 100)))
        except Exception:
            pass

    def _schedule_save(self): self._save_timer.start()

    def _save_settings(self) -> None:
        data = self._default_settings()
        fmt = "JPEG"
        for b in self.fmtButtons:
            if b.isChecked():
                fmt = b.text()
                break
        
        data.update({
            "default_format": fmt,
            "default_quality": self._current_quality(),
            "default_width": int(self.txtWidth.text()) if self.txtWidth.text().strip() else "",
            "open_when_done": self.chkOpenWhenDone.isChecked(),
            "last_output_path": self.txtOutputPath.text() or str(get_downloads_dir()),
            "window": {"w": self.width(), "h": self.height(), "x": self.x(), "y": self.y()},
        })
        try:
            # Write config.json
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.statusBar().showMessage("Settings saved", 1000)
        except Exception as e:
            print(f"Failed to save settings: {e}")

    # ---------------------- Theme Engine -----------------------------------
    def _apply_theme(self):
        c = THEME["colors"]
        d = THEME["dimens"]
        font = THEME["font_family"]

        palette = QtGui.QPalette()
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(c["window_bg"]))
        palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(c["text_main"]))
        palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(c["window_bg"]))
        palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(c["surface_bg"]))
        palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(c["text_main"]))
        palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(c["surface_bg"]))
        palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(c["text_main"]))
        palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(c["primary"]))
        palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtCore.Qt.GlobalColor.white)
        self.setPalette(palette)

        # Dynamic QSS injection
        css = f"""
        QMainWindow {{ background-color: {c["window_bg"]}; }}
        QWidget {{ color: {c["text_main"]}; font-family: "{font}"; font-size: {d["font_ui"]}; }}
        
        /* HEADERS */
        QLabel#lblSection {{
            color: {c["text_muted"]};
            font-size: {d["font_label"]};
            font-weight: bold;
            margin-bottom: 0px; 
        }}

        /* INPUTS */
        QLineEdit {{
            background-color: {c["surface_bg"]};
            color: {c["text_main"]};
            padding: 0 12px;
            min-height: {d["btn_height"]};
            border-radius: {d["radius"]};
            border: none;
            selection-background-color: {c["primary"]};
        }}
        QLineEdit:focus {{ background-color: {c["surface_hover"]}; }}
        
        /* TREE WIDGET */
        QTreeWidget {{
            background-color: {c["window_bg"]}; 
            color: #D4D4D8;
            border: 1px solid {c["surface_bg"]};
            border-radius: {d["radius"]};
            padding: 4px;
            outline: none;
        }}
        QTreeWidget::item {{
            padding: 6px;
            border-bottom: 1px solid {c["surface_bg"]};
        }}
        QTreeWidget::item:selected {{
            background-color: {c["surface_bg"]};
            color: {c["text_main"]};
        }}
        QHeaderView::section {{
            background-color: {c["window_bg"]};
            color: {c["text_muted"]};
            border: none;
            padding: 4px;
            font-weight: bold;
        }}

        /* CHOICE CHIPS (Format/Quality) */
        QToolButton[seg="true"] {{
            background-color: {c["surface_bg"]};
            border: none;
            color: {c["text_muted"]};
            min-height: {d["btn_height"]};
            border-radius: {d["radius"]};
            font-weight: 600;
        }}
        QToolButton[seg="true"]:hover {{ background-color: {c["surface_hover"]}; }}
        QToolButton[seg="true"]:checked {{
            background-color: {c["primary"]};
            color: white;
        }}
        
        /* ACTION BUTTONS */
        QPushButton#btnBrowse {{
            background-color: {c["primary"]};
            color: white;
            font-weight: bold;
            min-height: {d["btn_height"]};
            border-radius: {d["radius"]};
            border: none;
        }}
        QPushButton#btnBrowse:hover {{ background-color: {c["primary_hover"]}; }}
        
        QPushButton#btnSmall {{
            background-color: {c["surface_bg"]};
            color: {c["text_muted"]};
            font-size: 11px;
            padding: 4px 8px;
            border-radius: 4px;
            border: none;
        }}
        QPushButton#btnSmall:hover {{ color: white; background-color: {c["surface_hover"]}; }}

        /* CONVERT BUTTON */
        QPushButton#btnConvertPrimary {{
            background-color: {c["success"]};
            color: white;
            font-weight: bold;
            font-size: 14px;
            min-height: {d["lg_btn_height"]};
            border-radius: {d["radius"]};
        }}
        QPushButton#btnConvertPrimary:hover:enabled {{ background-color: {c["success_hover"]}; }}
        QPushButton#btnConvertPrimary:disabled {{ 
            background-color: {c["surface_bg"]};
            color: {c["text_muted"]};
            border: 1px solid {c["surface_hover"]};
        }}
        
        QCheckBox {{ spacing: 8px; color: {c["text_muted"]}; }}
        QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 4px; border: 1px solid {c["surface_hover"]}; background: {c["surface_bg"]}; }}
        QCheckBox::indicator:checked {{ background: {c["primary"]}; border: none; image: none; }}
        
        /* SCROLLBAR */
        QScrollBar:vertical {{ border: none; background: {c["window_bg"]}; width: 8px; margin: 0px; }}
        QScrollBar::handle:vertical {{ background: {c["surface_bg"]}; min-height: 20px; border-radius: 4px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """
        self.setStyleSheet(css)

    # ---------------------- UI helpers -------------------------------------
    def _current_quality(self) -> int:
        for b in self.qualityButtons:
            if b.isChecked():
                return int(b.text().rstrip("%"))
        return 90

    def _update_convert_label(self):
        count = self.treeFiles.topLevelItemCount()
        self.btnConvertPrimary.setText(f"CONVERT {count} ITEMS")

    def _update_convert_enabled(self):
        out_ok = bool(self.txtOutputPath.text()) and Path(self.txtOutputPath.text()).exists()
        count = self.treeFiles.topLevelItemCount()
        self.btnConvertPrimary.setEnabled(count > 0 and out_ok)

    # ---------------------- Queue management --------------------------------
    def _ingest_paths(self, paths: Iterable[str]):
        added = 0
        for p in paths:
            path = Path(p)
            if path.is_dir():
                try:
                    for sub in path.rglob("*"):
                        if sub.is_file():
                            if sub.suffix.lower() in SUPPORTED_EXTS:
                                added += self._queue_add(sub)
                except Exception as e:
                    print(f"Folder read error: {path} — {e}")
            elif path.is_file():
                if path.suffix.lower() in SUPPORTED_EXTS:
                    added += self._queue_add(path)
        
        self._update_convert_label()
        self._update_convert_enabled()

    def _queue_add(self, file_path: Path) -> int:
        try:
            ap = file_path.resolve()
            if ap in self._queue_set:
                return 0
            if not ap.exists():
                return 0
            # probe image
            try:
                with Image.open(ap) as im:
                    _ = im.size
            except Exception:
                return 0
            
            # Add to tree
            item = QtWidgets.QTreeWidgetItem(self.treeFiles)
            item.setText(0, ap.name)
            item.setText(1, human_bytes(ap.stat().st_size))
            item.setText(2, "Queued")
            # Store full path in data
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, str(ap))
            
            self._queue_set.add(ap)
            return 1
        except Exception as e:
            print(f"Queue add error: {file_path} — {e}")
            return 0

    def _remove_selected(self):
        root = self.treeFiles.invisibleRootItem()
        for item in self.treeFiles.selectedItems():
            path_str = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if path_str:
                self._queue_set.discard(Path(path_str))
            root.removeChild(item)
        
        self._update_convert_label()
        self._update_convert_enabled()

    def _clear_queue(self):
        self.treeFiles.clear()
        self._queue_set.clear()
        self._update_convert_label()
        self._update_convert_enabled()

    def _add_files_dialog(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Select images", str(get_downloads_dir()),
            "Images (*.jpg *.jpeg *.png *.webp *.tif *.tiff *.bmp *.gif *.heic *.heif)"
        )
        if files:
            self._ingest_paths(files)

    def _add_folder_dialog(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select folder", str(get_downloads_dir()))
        if folder:
            self._ingest_paths([folder])

    # ---------------------- Conversion --------------------------------------
    def _choose_output_dir(self):
        start_dir = self.txtOutputPath.text() or str(get_downloads_dir())
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output folder", start_dir)
        if folder:
            self.txtOutputPath.setText(folder)
            self._schedule_save()
            self._update_convert_enabled()

    def _settings_from_ui(self) -> ConvertSettings:
        fmt = "jpeg"
        for b in self.fmtButtons:
            if b.isChecked():
                fmt = b.text().lower()
                break
        
        q = self._current_quality()
        width = int(self.txtWidth.text()) if self.txtWidth.text().strip() else None
        outdir = Path(self.txtOutputPath.text())
        open_when_done = self.chkOpenWhenDone.isChecked()
        return ConvertSettings(fmt=fmt, quality=q, width=width, outdir=outdir, 
                               downscale_only=True, open_when_done=open_when_done)

    def _on_convert(self):
        if not (self.treeFiles.topLevelItemCount() > 0 and self.txtOutputPath.text() and Path(self.txtOutputPath.text()).exists()):
            QtWidgets.QMessageBox.warning(self, "Convert", "Please add items and choose a valid output folder.")
            return
        
        self.btnConvertPrimary.setEnabled(False)
        self.btnRemove.setEnabled(False)
        self.btnClear.setEnabled(False)
        self.dropzone.setEnabled(False)

        s = self._settings_from_ui()
        self._save_settings()
        
        self._tasks_done = 0
        self._tasks_total = self.treeFiles.topLevelItemCount()
        self._stats_ok = 0
        self._stats_skip = 0
        self._stats_err = 0
        self._stats_bytes_in = 0
        self._stats_bytes_out = 0

        # Reset statuses
        for i in range(self._tasks_total):
            item = self.treeFiles.topLevelItem(i)
            item.setText(2, "Waiting...")

        self._progress_dialog = QtWidgets.QProgressDialog("Converting...", "Cancel", 0, self._tasks_total, self)
        self._progress_dialog.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self._progress_dialog.setValue(0)
        self._progress_dialog.canceled.connect(self._on_cancel)

        # Iterate tree items
        for i in range(self._tasks_total):
            item = self.treeFiles.topLevelItem(i)
            path_str = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if path_str:
                path = Path(path_str)
                task = ConvertTask(path, s)
                # No log signal anymore
                task.signals.result.connect(self._on_task_result)
                self.threadpool.start(task)

    def _on_task_result(self, ok, skip, err, b_in, b_out, src_path):
        if ok: self._stats_ok += 1
        if skip: self._stats_skip += 1
        if err: self._stats_err += 1
        self._stats_bytes_in += b_in
        self._stats_bytes_out += b_out
        
        # Update UI item
        # We need to find the item. Since we don't have a map, we iterate. 
        # For 100s of files this is fine. For 1000s we might want a dict mapping path -> item.
        # But paths are unique in our set.
        
        # Optimization: We could have passed the item reference if we were careful about threading,
        # but touching widgets from threads is forbidden.
        # So we search in main thread.
        
        # Let's just linear search for now, it's safe and simple.
        found_item = None
        for i in range(self.treeFiles.topLevelItemCount()):
            item = self.treeFiles.topLevelItem(i)
            if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == str(src_path):
                found_item = item
                break
        
        if found_item:
            if ok:
                saved = b_in - b_out
                pct = (saved / b_in * 100) if b_in > 0 else 0
                found_item.setText(2, f"Done (-{int(pct)}%)")
                found_item.setForeground(2, QtGui.QBrush(QtGui.QColor(THEME["colors"]["success"])))
            elif skip:
                found_item.setText(2, "Skipped")
            else:
                found_item.setText(2, "Error")
                found_item.setForeground(2, QtGui.QBrush(QtGui.QColor("#EF4444")))

        self._tasks_done += 1
        if self._progress_dialog:
            self._progress_dialog.setValue(self._tasks_done)

        if self._tasks_done >= self._tasks_total:
            self._finish_batch()

    def _on_cancel(self):
        self.threadpool.clear()
        self._finish_batch(cancelled=True)

    def _finish_batch(self, cancelled=False):
        if not cancelled and self._progress_dialog:
            self._progress_dialog.close()
        self._progress_dialog = None
        
        self.btnConvertPrimary.setEnabled(True)
        self.btnRemove.setEnabled(True)
        self.btnClear.setEnabled(True)
        self.dropzone.setEnabled(True)
        
        if not cancelled:
            self.statusBar().showMessage(f"Done. OK: {self._stats_ok}, Err: {self._stats_err}", 4000)
            
            s = self._settings_from_ui()
            if s.open_when_done:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(s.outdir)))

    # -------------------------- Events --------------------------------------
    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        self._save_settings()
        self.threadpool.clear()
        super().closeEvent(e)

# -------------------------- Entrypoint --------------------------------------
def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setOrganizationName(APP_ORG)
    app.setApplicationName(APP_NAME)
    w = MainWindow(); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()