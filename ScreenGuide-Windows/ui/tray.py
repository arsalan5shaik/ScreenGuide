from PyQt6.QtWidgets import (
    QSystemTrayIcon, QMenu, QDialog, QVBoxLayout, QTextEdit,
    QPushButton, QLabel, QHBoxLayout,
)
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QObject

from config import cfg


def _make_tray_icon(color: QColor) -> QIcon:
    """Generate a simple coloured circle as the tray icon.
    Must be called AFTER QApplication exists (Qt requirement)."""
    px = QPixmap(QSize(22, 22))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(3, 3, 16, 16)
    painter.end()
    return QIcon(px)


class TrayManager(QObject):
    """Windows system tray icon and context menu."""

    on_show_panel         = pyqtSignal()
    on_hide_panel         = pyqtSignal()
    on_quit               = pyqtSignal()
    on_toggle_search      = pyqtSignal(bool)
    on_toggle_wake_word   = pyqtSignal(bool)
    on_toggle_slow_mode   = pyqtSignal(bool)
    on_toggle_quiz_mode   = pyqtSignal(bool)
    on_toggle_privacy     = pyqtSignal(bool)
    on_switch_provider    = pyqtSignal(str)     # "claude" | "openai" | "copilot" | ...
    on_copilot_login      = pyqtSignal()
    on_copilot_refresh    = pyqtSignal()
    on_ollama_set_model   = pyqtSignal(str, str)   # (kind, name): kind = "vision" | "text"
    on_ollama_pull        = pyqtSignal(str)        # model name
    on_ollama_refresh     = pyqtSignal()
    on_stop               = pyqtSignal()
    on_toggle_code_mode   = pyqtSignal(bool)
    on_toggle_multilang   = pyqtSignal(bool)
    on_toggle_journal     = pyqtSignal(bool)
    on_toggle_ocr         = pyqtSignal(bool)
    on_record_start       = pyqtSignal()
    on_record_stop        = pyqtSignal()
    on_collab_start       = pyqtSignal()
    on_collab_join        = pyqtSignal()
    on_workflow_start     = pyqtSignal()
    on_workflow_stop      = pyqtSignal()
    on_journal_open       = pyqtSignal()
    on_attach_doc         = pyqtSignal()
    on_run_setup          = pyqtSignal()
    on_diagnostics        = pyqtSignal()
    on_set_mic_device     = pyqtSignal(int)     # sounddevice input device index
    on_set_response_language = pyqtSignal(str)  # "" = auto-detect, else ISO code
    on_set_custom_instructions = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Icons MUST be created after QApplication exists
        self._icons = {
            "idle":      _make_tray_icon(QColor(80, 80, 120)),
            "listening": _make_tray_icon(QColor(50, 200, 100)),
            "thinking":  _make_tray_icon(QColor(0, 120, 255)),
            "speaking":  _make_tray_icon(QColor(255, 140, 0)),
        }

        self._tray = QSystemTrayIcon()
        self._tray.setIcon(self._icons["idle"])
        self._tray.setToolTip(
            f"ScreenGuide - AI Companion\nHold {cfg.hotkey} to speak"
        )
        self._search_enabled = True
        self._wake_enabled = True
        self._response_language = ""
        try:
            from config import cfg as _cfg
            self._custom_instructions = _cfg.custom_instructions
        except Exception:
            self._custom_instructions = ""
        self._slow_enabled = False
        self._quiz_enabled = False
        self._privacy_enabled = True
        self._code_enabled = True
        self._multilang_enabled = True
        self._journal_enabled = True
        self._ocr_enabled = True
        self._is_recording = False

        # Ollama model state — populated by manager via set_ollama_models()
        self._ollama_installed: dict[str, list[str]] = {"vision": [], "text": []}

        self._build_menu()
        self._tray.activated.connect(self._on_activated)
        self._tray.show()

    def _build_menu(self):
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background: rgb(22,22,28); border: 1px solid rgb(55,55,70);"
            "border-radius: 8px; color: rgb(220,220,230); font-size: 13px; }"
            "QMenu::item:selected { background: rgb(0,90,200); border-radius: 4px; }"
            "QMenu::separator { height: 1px; background: rgb(55,55,70); margin: 4px 8px; }"
        )

        providers = cfg.describe()
        info = menu.addAction(
            f"LLM: {providers['llm']}  |  STT: {providers['stt']}  |  TTS: {providers['tts']}"
        )
        info.setEnabled(False)
        menu.addSeparator()

        show_action = menu.addAction("Show Panel")
        show_action.triggered.connect(self.on_show_panel)

        hide_action = menu.addAction("Hide Panel")
        hide_action.triggered.connect(self.on_hide_panel)

        stop_action = menu.addAction("Stop (Esc)")
        stop_action.triggered.connect(self.on_stop)

        menu.addSeparator()

        # Model switcher submenu
        switch_menu = menu.addMenu(f"Model: {providers['llm']}")
        active = providers['llm']
        for name in cfg.available_llm_providers():
            label = f"● {name}" if name == active else f"  {name}"
            act = switch_menu.addAction(label)
            act.triggered.connect(lambda _=False, n=name: self.on_switch_provider.emit(n))
        switch_menu.addSeparator()
        login_act = switch_menu.addAction("Sign in to GitHub Copilot…")
        login_act.triggered.connect(self.on_copilot_login)
        refresh_act = switch_menu.addAction("Refresh Copilot models")
        refresh_act.triggered.connect(self.on_copilot_refresh)

        # ── Ollama-specific submenu (always visible — Ollama is the offline fallback) ──
        self._build_ollama_submenu(menu, providers)

        menu.addSeparator()

        search_action = menu.addAction(
            "Web Search: ON" if self._search_enabled else "Web Search: OFF"
        )
        search_action.setCheckable(True)
        search_action.setChecked(self._search_enabled)
        search_action.triggered.connect(self._toggle_search)
        self._search_action = search_action

        wake_action = menu.addAction(
            "Wake word 'ScreenGuide': ON" if self._wake_enabled else "Wake word 'ScreenGuide': OFF"
        )
        wake_action.setCheckable(True)
        wake_action.setChecked(self._wake_enabled)
        wake_action.triggered.connect(self._toggle_wake)
        self._wake_action = wake_action

        self._build_language_submenu(menu)

        scope_label = "Instructions for ScreenGuide…"
        scope_action = menu.addAction(scope_label)
        scope_action.triggered.connect(self._prompt_custom_instructions)

        # ── Tutor toggles ──
        menu.addSeparator()
        tutor_menu = menu.addMenu("Tutor Mode")

        slow_action = tutor_menu.addAction(
            "Slow Mode (teacher pace): ON" if self._slow_enabled
            else "Slow Mode (teacher pace): OFF"
        )
        slow_action.setCheckable(True)
        slow_action.setChecked(self._slow_enabled)
        slow_action.triggered.connect(self._toggle_slow)
        self._slow_action = slow_action

        quiz_action = tutor_menu.addAction(
            "Quiz Mode: ON" if self._quiz_enabled else "Quiz Mode: OFF"
        )
        quiz_action.setCheckable(True)
        quiz_action.setChecked(self._quiz_enabled)
        quiz_action.triggered.connect(self._toggle_quiz)
        self._quiz_action = quiz_action

        privacy_action = tutor_menu.addAction(
            "Privacy Guard: ON" if self._privacy_enabled
            else "Privacy Guard: OFF"
        )
        privacy_action.setCheckable(True)
        privacy_action.setChecked(self._privacy_enabled)
        privacy_action.triggered.connect(self._toggle_privacy)
        self._privacy_action = privacy_action

        code_action = tutor_menu.addAction(
            "Code Mode (auto): ON" if self._code_enabled else "Code Mode (auto): OFF"
        )
        code_action.setCheckable(True)
        code_action.setChecked(self._code_enabled)
        code_action.triggered.connect(self._toggle_code)
        self._code_action = code_action

        ml_action = tutor_menu.addAction(
            "Multilingual: ON" if self._multilang_enabled else "Multilingual: OFF"
        )
        ml_action.setCheckable(True)
        ml_action.setChecked(self._multilang_enabled)
        ml_action.triggered.connect(self._toggle_multilang)
        self._ml_action = ml_action

        ocr_action = tutor_menu.addAction(
            "OCR Fallback: ON" if self._ocr_enabled else "OCR Fallback: OFF"
        )
        ocr_action.setCheckable(True)
        ocr_action.setChecked(self._ocr_enabled)
        ocr_action.triggered.connect(self._toggle_ocr)
        self._ocr_action = ocr_action

