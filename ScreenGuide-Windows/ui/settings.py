"""
Settings window.

Everything here was previously either a hand-edit in .env or buried several
levels into the tray menu. Changes apply immediately and are written back to
.env so they survive a restart.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QComboBox,
    QSpinBox, QCheckBox, QPushButton, QTabWidget, QLineEdit, QFrame,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from config import cfg

SETTINGS_QSS = """
QWidget#settings { background-color: rgb(20, 20, 25); }
QTabWidget::pane { border: 1px solid rgba(60,60,75,150); border-radius: 8px; top: -1px; }
QTabBar::tab {
    background: rgba(38,38,48,200); color: rgb(190,190,208);
    padding: 7px 16px; border: 1px solid rgba(60,60,75,150);
    border-bottom: none; border-top-left-radius: 7px; border-top-right-radius: 7px;
    margin-right: 2px; font-size: 12px;
}
QTabBar::tab:selected { background: rgba(0,120,255,45); color: rgb(226,236,255); }
QLabel { color: rgb(228,228,238); background: transparent; font-size: 12px; }
QLabel#hint { color: rgb(130,130,154); font-size: 11px; }
QLabel#warn { color: rgb(255,186,120); font-size: 11px; }
QLabel#head { color: rgb(240,240,248); font-size: 13px; font-weight: bold; }
QComboBox, QSpinBox, QLineEdit {
    background: rgba(38,38,48,220); border: 1px solid rgba(70,70,88,170);
    border-radius: 7px; color: rgb(232,232,242); font-size: 12px; padding: 5px 8px;
    min-height: 18px;
}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus { border: 1px solid rgba(0,120,255,190); }
QComboBox QAbstractItemView {
    background: rgb(30,30,38); color: rgb(226,226,236);
    selection-background-color: rgba(0,120,255,120);
    border: 1px solid rgba(70,70,88,170);
}
QCheckBox { color: rgb(226,226,236); font-size: 12px; spacing: 7px; }
QCheckBox::indicator {
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid rgba(90,90,112,200); background: rgba(38,38,48,220);
}
QCheckBox::indicator:checked { background: rgb(0,120,255); border-color: rgb(0,120,255); }
QPushButton {
    background: rgba(52,52,64,200); border: 1px solid rgba(74,74,92,170);
    border-radius: 8px; color: rgb(228,228,238); font-size: 12px;
    font-weight: 600; padding: 6px 13px;
}
QPushButton:hover { background: rgba(70,70,86,230); }
QPushButton#danger {
    background: rgba(255,70,70,28); border: 1px solid rgba(255,70,70,140);
    color: rgb(255,160,160);
}
QPushButton#danger:hover { background: rgba(255,70,70,60); }
QFrame#sep { background: rgba(60,60,75,140); max-height: 1px; border: none; }
"""

# Longest edge sent to the model. More pixels = better small text, and
# proportionally more time on a local model.
DETAIL_LEVELS = [
    ("Fast — 960px",     960),
    ("Balanced — 1280px", 1280),
    ("Detailed — 1600px", 1600),
    ("Maximum — 1920px",  1920),
]


class SettingsWindow(QWidget):
    """Model, screen-capture and behaviour settings."""

    on_provider_changed = pyqtSignal(str)
    on_models_changed   = pyqtSignal()
    on_hotkey_changed   = pyqtSignal(str)

    def __init__(self, manager=None, parent=None):
        super().__init__(parent)
        self._manager = manager
        self.setObjectName("settings")
        self.setWindowTitle("ScreenGuide — Settings")
        self.setStyleSheet(SETTINGS_QSS)
        self.resize(560, 580)
        self.setMinimumSize(500, 500)

        self._installed: dict[str, list[str]] = {"vision": [], "text": []}
        self._build_ui()
        self._load_installed_models()
        self._refresh_stats()

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(5000)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        tabs = QTabWidget()
        tabs.addTab(self._models_tab(), "Model")
        tabs.addTab(self._capture_tab(), "Screen capture")
        tabs.addTab(self._behaviour_tab(), "Behaviour")
        root.addWidget(tabs, stretch=1)

        foot = QHBoxLayout()
        self._status = QLabel("")
        self._status.setObjectName("hint")
        foot.addWidget(self._status, stretch=1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        foot.addWidget(close)
        root.addLayout(foot)

    def _models_tab(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(14, 14, 14, 14)
        col.setSpacing(9)
        form = QFormLayout()
        form.setSpacing(9)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._provider = QComboBox()
        for p in cfg.available_llm_providers():
            self._provider.addItem(p, userData=p)
        idx = self._provider.findData(cfg.llm_provider())
        if idx >= 0:
            self._provider.setCurrentIndex(idx)
        self._provider.currentIndexChanged.connect(self._apply_provider)
        form.addRow("Provider:", self._provider)

        self._vision = QComboBox()
        self._vision.setEditable(True)
        self._vision.currentTextChanged.connect(self._apply_vision)
        form.addRow("Vision model:", self._vision)

        vh = QLabel(
            "Used for anything that looks at the screen. Needs a model with "
            "vision support — qwen2.5vl reads screen text far better than "
            "llava, which blurs it away before the model sees it."
        )
        vh.setObjectName("hint")
        vh.setWordWrap(True)
        form.addRow("", vh)

        self._text_model = QComboBox()
        self._text_model.setEditable(True)
        self._text_model.currentTextChanged.connect(self._apply_text_model)
        form.addRow("Text model:", self._text_model)

        th = QLabel("Used when no screenshot is involved — code questions, "
                    "journal lookups, follow-ups.")
        th.setObjectName("hint")
        th.setWordWrap(True)
        form.addRow("", th)
        col.addLayout(form)

        sep = QFrame()
        sep.setObjectName("sep")
        sep.setFrameShape(QFrame.Shape.HLine)
        col.addWidget(sep)

        self._model_note = QLabel("")
        self._model_note.setObjectName("hint")
        self._model_note.setWordWrap(True)
        col.addWidget(self._model_note)

        row = QHBoxLayout()
        refresh = QPushButton("Refresh installed models")
        refresh.clicked.connect(self._load_installed_models)
        row.addWidget(refresh)
        row.addStretch()
        col.addLayout(row)
        col.addStretch(1)
        return w

    def _capture_tab(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(14, 14, 14, 14)
        col.setSpacing(9)

        head = QLabel("Rolling screen history")
        head.setObjectName("head")
        col.addWidget(head)

        blurb = QLabel(
            "ScreenGuide saves a screenshot on a timer so it can see what "
            "changed, not just what's on screen now. Images are written to a "
            "folder on this computer, are never uploaded anywhere, and are "
            "deleted automatically once they pass the age limit."
        )
        blurb.setObjectName("hint")
        blurb.setWordWrap(True)
        col.addWidget(blurb)

        self._cap_enabled = QCheckBox("Capture my screen on a timer")
        self._cap_enabled.setChecked(cfg.screenshot_history_enabled)
        self._cap_enabled.toggled.connect(self._apply_capture_enabled)
        col.addWidget(self._cap_enabled)

        form = QFormLayout()
        form.setSpacing(9)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._interval = QSpinBox()
        self._interval.setRange(5, 3600)
        self._interval.setSingleStep(15)
        self._interval.setSuffix(" seconds")
        self._interval.setValue(cfg.screenshot_interval_s)
        self._interval.valueChanged.connect(self._apply_capture_cfg)
        form.addRow("Capture every:", self._interval)

        self._retention = QSpinBox()
        self._retention.setRange(1, 1440)
        self._retention.setSingleStep(15)
        self._retention.setSuffix(" minutes")
        self._retention.setValue(cfg.screenshot_retention_min)
        self._retention.valueChanged.connect(self._apply_capture_cfg)
        form.addRow("Keep for:", self._retention)

        self._send_count = QSpinBox()
        self._send_count.setRange(1, 6)
        self._send_count.setSuffix(" images")
        self._send_count.setValue(cfg.screenshot_send_count)
        self._send_count.valueChanged.connect(self._apply_send_count)
        form.addRow("Send to model:", self._send_count)

        self._detail = QComboBox()
        for label, px in DETAIL_LEVELS:
            self._detail.addItem(label, userData=px)
        di = self._detail.findData(cfg.screenshot_max_width)
        if di < 0:
            self._detail.addItem(f"Custom — {cfg.screenshot_max_width}px",
                                 userData=cfg.screenshot_max_width)
            di = self._detail.count() - 1
        self._detail.setCurrentIndex(di)
        self._detail.currentIndexChanged.connect(self._apply_capture_cfg)
        form.addRow("Detail:", self._detail)
        col.addLayout(form)

        self._cost_note = QLabel("")
        self._cost_note.setObjectName("warn")
        self._cost_note.setWordWrap(True)
        col.addWidget(self._cost_note)

        sep = QFrame()
        sep.setObjectName("sep")
        sep.setFrameShape(QFrame.Shape.HLine)
        col.addWidget(sep)

        self._stats = QLabel("")
        self._stats.setObjectName("hint")
        self._stats.setWordWrap(True)
        col.addWidget(self._stats)

        row = QHBoxLayout()
        open_btn = QPushButton("Open folder")
        open_btn.clicked.connect(self._open_folder)
        row.addWidget(open_btn)
        row.addStretch()
        wipe = QPushButton("Delete all screenshots")
        wipe.setObjectName("danger")
        wipe.clicked.connect(self._wipe)
        row.addWidget(wipe)
        col.addLayout(row)
        col.addStretch(1)
        self._update_cost_note()
        return w

    def _behaviour_tab(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(14, 14, 14, 14)
        col.setSpacing(9)
        form = QFormLayout()
        form.setSpacing(9)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._hotkey = QLineEdit(cfg.hotkey)
        self._hotkey.setPlaceholderText("ctrl+win")
        self._hotkey.editingFinished.connect(self._apply_hotkey)
        form.addRow("Push-to-talk:", self._hotkey)

        hk = QLabel("Takes effect after a restart. Modifier-only combos "
                    "(ctrl+win) are held down; combos ending in a normal key "
                    "(ctrl+alt+space) are held with the last key tapped.")
        hk.setObjectName("hint")
        hk.setWordWrap(True)
        form.addRow("", hk)

        self._whisper = QComboBox()
        for m in ("tiny", "base", "small", "medium"):
            self._whisper.addItem(m, userData=m)
        wi = self._whisper.findData(cfg.whisper_model)
        if wi >= 0:
            self._whisper.setCurrentIndex(wi)
        self._whisper.currentIndexChanged.connect(self._apply_whisper)
        form.addRow("Speech model:", self._whisper)

        self._lang = QLineEdit(cfg.response_language)
        self._lang.setPlaceholderText("auto-detect — or en, de, hi, fr…")
        self._lang.editingFinished.connect(self._apply_lang)
        form.addRow("Reply language:", self._lang)
        col.addLayout(form)

        sep = QFrame()
        sep.setObjectName("sep")
        sep.setFrameShape(QFrame.Shape.HLine)
        col.addWidget(sep)

        note = QLabel(
            "Tutor toggles — slow mode, quiz mode, privacy guard, OCR, "
            "multilingual — remain in the tray menu under Tutor Mode."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        col.addWidget(note)
        col.addStretch(1)
        return w

    # ── Apply handlers ────────────────────────────────────────────────────

    def _flash(self, msg: str):
        self._status.setText(msg)
        QTimer.singleShot(2500, lambda: self._status.setText(""))

    def _apply_provider(self):
        name = self._provider.currentData()
        if not name:
            return
        if self._manager:
            self._manager.set_active_provider(name)
        else:
            cfg.set_active_llm(name)
        self.on_provider_changed.emit(name)
        self._load_installed_models()
        self._flash(f"Switched to {name}")

    def _apply_vision(self, name: str):
        name = (name or "").strip()
        if not name or name == cfg.get_ollama_model("vision"):
            return
        if self._manager:
            self._manager.set_ollama_model("vision", name)
        cfg.set_setting("ollama_vision_model", name)
        self.on_models_changed.emit()
        self._update_model_note()
        self._flash(f"Vision model → {name}")

    def _apply_text_model(self, name: str):
        name = (name or "").strip()
        if not name or name == cfg.get_ollama_model("text"):
            return
        if self._manager:
            self._manager.set_ollama_model("text", name)
        cfg.set_setting("ollama_text_model", name)
        self.on_models_changed.emit()
        self._flash(f"Text model → {name}")

    def _apply_capture_enabled(self, on: bool):
        if self._manager:
            self._manager.set_history_enabled(on)
        else:
            cfg.set_setting("screenshot_history_enabled", on)
        self._refresh_stats()
        self._flash("Screen capture on" if on else
                    "Screen capture off — stored images deleted")

    def _apply_capture_cfg(self):
        px = self._detail.currentData() or cfg.screenshot_max_width
        if self._manager:
            self._manager.configure_history(
                interval_s=self._interval.value(),
                retention_min=self._retention.value(),
                max_width=px,
            )
        else:
            cfg.set_setting("screenshot_interval_s", self._interval.value())
            cfg.set_setting("screenshot_retention_min", self._retention.value())
            cfg.set_setting("screenshot_max_width", px)
        self._update_cost_note()
        self._flash("Capture settings saved")

    def _apply_send_count(self, n: int):
        cfg.set_setting("screenshot_send_count", int(n))
        self._update_cost_note()
        self._flash(f"Sending {n} image{'s' if n != 1 else ''} per question")

    def _apply_hotkey(self):
        val = self._hotkey.text().strip().lower()
        if not val or val == cfg.hotkey:
            return
        cfg.set_setting("hotkey", val)
        self.on_hotkey_changed.emit(val)
        self._flash("Hotkey saved — restart to apply")

    def _apply_whisper(self):
        cfg.set_setting("whisper_model", self._whisper.currentData())
        self._flash("Speech model saved — restart to apply")

    def _apply_lang(self):
        val = self._lang.text().strip()
        if val == cfg.response_language:
            return
        if self._manager:
            self._manager.set_response_language(val)
        cfg.set_setting("response_language", val)
        self._flash("Reply language saved")

    # ── Data ──────────────────────────────────────────────────────────────

    def _load_installed_models(self):
        """Populate the model dropdowns from whatever Ollama actually has."""
        vision, text = [], []
        try:
            import httpx
            r = httpx.get(f"{cfg.ollama_host.rstrip('/')}/api/tags", timeout=5)
            for m in r.json().get("models", []):
                name = m.get("name", "")
                caps = m.get("capabilities") or []
                (vision if "vision" in caps else text).append(name)
        except Exception:
            pass
        # Any model can answer text-only questions; only some can see.
        text = sorted(set(text) | set(vision))
        self._installed = {"vision": sorted(vision), "text": text}

        self._fill(self._vision, self._installed["vision"],
                   cfg.get_ollama_model("vision"))
        self._fill(self._text_model, self._installed["text"],
                   cfg.get_ollama_model("text"))
        self._update_model_note()

    @staticmethod
    def _fill(combo: QComboBox, names: list[str], current: str):
        combo.blockSignals(True)
        combo.clear()
        for n in names:
            combo.addItem(n, userData=n)
        if current:
            i = combo.findData(current)
            if i >= 0:
                combo.setCurrentIndex(i)
            else:
                combo.insertItem(0, current, userData=current)
                combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _update_model_note(self):
        if not self._installed["vision"]:
            self._model_note.setText(
                "No vision-capable model installed. Pull one first:\n"
                "    ollama pull qwen2.5vl:3b"
            )
            return
        current = (self._vision.currentText() or "").lower()
        if current.startswith("llava"):
            self._model_note.setText(
                "llava resizes every screenshot to 336×336 before the model "
                "sees it, so on-screen text is unreadable and it tends to "
                "invent plausible-looking answers. qwen2.5vl keeps the "
                "original resolution and can actually read the screen."
            )
        else:
            self._model_note.setText(
                f"{len(self._installed['vision'])} vision model(s) installed. "
                "Pull more with:  ollama pull <name>"
            )

    def _update_cost_note(self):
        n = self._send_count.value()
        px = self._detail.currentData() or 1280
        if n <= 1 and px <= 1280:
            self._cost_note.setText("")
            return
        self._cost_note.setText(
            f"{n} image(s) at {px}px per question. On a local model without a "
            f"dedicated GPU each image adds roughly a minute of thinking time "
            f"before the first word appears — drop to 1 image or a lower "
            f"detail level if replies feel too slow."
        )

    def _refresh_stats(self):
        stats = {}
        if self._manager:
            try:
                stats = self._manager.history_stats()
            except Exception:
                stats = {}
        if not stats:
            self._stats.setText(f"Folder: {cfg.screenshot_dir}")
            return
        mb = stats.get("bytes", 0) / 1e6
        count = stats.get("count", 0)
        newest = stats.get("newest_age_s")
        when = "none yet" if newest is None else f"{int(newest)}s ago"
        err = stats.get("last_error")
        line = (f"{count} screenshot(s), {mb:.1f} MB — newest {when}\n"
                f"Folder: {stats.get('folder', cfg.screenshot_dir)}")
        if err:
            line += f"\nLast capture error: {err}"
        self._stats.setText(line)

    def _open_folder(self):
        import os
        import subprocess
        path = str(cfg.screenshot_dir)
        try:
            os.makedirs(path, exist_ok=True)
            os.startfile(path)
        except Exception:
            subprocess.Popen(["explorer", path])

    def _wipe(self):
        if QMessageBox.question(
            self, "Delete screenshots",
            "Delete every stored screenshot?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        n = self._manager.clear_history_screenshots() if self._manager else 0
        self._refresh_stats()
        self._flash(f"Deleted {n} screenshot(s)")

    def closeEvent(self, event):
        self._stats_timer.stop()
        super().closeEvent(event)
