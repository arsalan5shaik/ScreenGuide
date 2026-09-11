import time
from enum import Enum, auto
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QComboBox, QFrame
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush

from ui.design import (
    PANEL_QSS, PANEL_WIDTH, PANEL_HEIGHT, PANEL_RADIUS, PANEL_AUTO_HIDE_MS,
    STATE_IDLE, STATE_LISTENING, STATE_THINKING, STATE_SPEAKING,
    FONT_TITLE, FONT_STATUS, FONT_LABEL,
)
from config import cfg


class AppState(Enum):
    IDLE      = auto()
    LISTENING = auto()
    THINKING  = auto()
    SPEAKING  = auto()


BUSY_STATES = (AppState.THINKING, AppState.SPEAKING)


def _hotkey_label() -> str:
    """Human-readable hotkey from config, e.g. 'ctrl+win' -> 'Ctrl+Win'."""
    try:
        return "+".join(p.strip().capitalize() for p in cfg.hotkey.split("+"))
    except Exception:
        return "Ctrl+Win"


STATE_LABELS = {
    AppState.IDLE:      f"Say 'ScreenGuide' or hold {_hotkey_label()}",
    AppState.LISTENING: "Listening…",
    AppState.THINKING:  "Thinking…",
    AppState.SPEAKING:  "Speaking…",
}

STATE_COLORS = {
    AppState.IDLE:      STATE_IDLE,
    AppState.LISTENING: STATE_LISTENING,
    AppState.THINKING:  STATE_THINKING,
    AppState.SPEAKING:  STATE_SPEAKING,
}


class WaveformWidget(QWidget):
    """Animated waveform bars shown while listening."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self._levels = [0.0] * 12
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._decay)

    def set_level(self, rms: float):
        import random
        peak = min(1.0, rms * 3)
        self._levels = [min(1.0, peak * (0.4 + random.random() * 0.6)) for _ in self._levels]
        self.update()

    def _decay(self):
        self._levels = [max(0.0, v * 0.85) for v in self._levels]
        self.update()

    def start(self):
        self._timer.start(50)

    def stop(self):
        self._timer.stop()
        self._levels = [0.0] * 12
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bar_w = max(3, (w - 4 * len(self._levels)) // len(self._levels))
        spacing = (w - bar_w * len(self._levels)) // (len(self._levels) + 1)
        for i, level in enumerate(self._levels):
            x = spacing + i * (bar_w + spacing)
            bar_h = max(4, int(level * (h - 8)))
            y = (h - bar_h) // 2
            alpha = max(80, int(level * 255))
            color = QColor(0, 120, 255, alpha)
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(x, y, bar_w, bar_h, bar_w // 2, bar_w // 2)
        painter.end()


class MicMeter(QWidget):
    """Thin idle input-level bar.

    Sits under the composer while idle so a muted or wrong input device is
    obvious *before* the user holds the hotkey and talks to nothing.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._level = 0.0
        self._peak = 0.0

    def set_level(self, rms: float):
        self._level = min(1.0, rms * 6)
        self._peak = max(self._peak * 0.92, self._level)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(60, 60, 75, 140)))
        painter.drawRoundedRect(0, 0, w, h, 1, 1)
        if self._peak > 0.01:
            lit = int(w * self._peak)
            # Green until it's hot, amber near clipping.
            c = QColor(50, 200, 100, 210) if self._peak < 0.85 else QColor(255, 180, 0, 220)
            painter.setBrush(QBrush(c))
            painter.drawRoundedRect(0, 0, lit, h, 1, 1)
        painter.end()


PROVIDER_LABELS = {
    "claude":   "Claude",
    "openai":   "GPT-4o",
    "gemini":   "Gemini",
    "copilot":  "Copilot",
    "ollama":   "Ollama",
    "lmstudio": "LM Studio",
}


def _short_model(name: str, limit: int = 16) -> str:
    """Trim a model id to something that fits the badge.

    Character-based rather than pixel-based on purpose: font metrics for a
    stylesheet-styled QLabel aren't reliable before it's shown, so measuring
    produced wildly over-eager truncation.
    """
    name = (name or "").strip()
    return name if len(name) <= limit else name[: limit - 1] + "…"


def provider_label(provider: str, *, full: bool = False) -> str:
    """Badge text for a provider. Ollama/LM Studio append the live model name
    so the badge never claims a model the app isn't actually running — read
    at call time, not import time, since both are switchable from the tray."""
    base = PROVIDER_LABELS.get(provider, provider)
    if provider == "ollama":
        model = cfg.get_ollama_model("vision")
    elif provider == "lmstudio":
        model = cfg.lmstudio_model
    else:
        return base
    if not model:
        return base
    return f"{base} ({model if full else _short_model(model)})"


# Provider model lists are fetched live from each vendor's /models endpoint
# (see ai/model_registry.py for Claude/OpenAI/Gemini and
# ai/github_copilot_provider.py for Copilot). The hardcoded lists below are
# only used as offline fallbacks when no cache exists yet.


def _copilot_model_choices() -> list[tuple[str, str]]:
    """Returns [(model_id, display_label), ...] for the dropdown.
    Free models first, then ascending multiplier. Display shows '(free)' /
    '(1×)' so the user always knows what burns premium quota."""
    try:
        from ai.github_copilot_provider import sorted_model_ids, model_label
    except Exception:
        return [("gpt-4o-mini", "gpt-4o-mini  (free)")]
    out = [(mid, model_label(mid)) for mid in sorted_model_ids()]
    if not out:
        out.append(("gpt-4o-mini", "gpt-4o-mini  (free)"))
    return out


class ProviderBadge(QLabel):
    """Small pill showing active provider.

    Text is elided rather than allowed to grow: model ids like
    'llama3.2-vision:11b' would otherwise push the minimize button off the
    edge of the header.
    """

    def __init__(self, provider: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "background: rgba(0,120,255,25); border: 1px solid rgba(0,120,255,100);"
            "border-radius: 8px; color: rgb(140,180,255); font-size: 11px; padding: 2px 8px;"
        )
        self.set_provider(provider)

    def set_provider(self, provider: str):
        self.setText(provider_label(provider))
        # Full, untruncated id on hover.
        self.setToolTip(provider_label(provider, full=True))


class ConversationView(QScrollArea):
    """Scrolling transcript of the session: what the user said, what
    ScreenGuide answered, and any errors — in order.

    Replaces the previous single append-only QLabel, which never cleared
    between turns and so grew into one undifferentiated wall of text.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._host = QWidget()
        self._host.setStyleSheet("background: transparent;")
        self._col = QVBoxLayout(self._host)
        self._col.setContentsMargins(2, 2, 2, 2)
        self._col.setSpacing(7)
        self._col.addStretch(1)
        self.setWidget(self._host)

        self._live: Optional[QLabel] = None   # assistant bubble being streamed
        self._live_text = ""
        self._placeholder: Optional[QLabel] = None
        self._show_placeholder()

    # ── Internals ─────────────────────────────────────────────────────────

    def _near_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 48

    def _scroll_to_bottom(self):
        bar = self.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _add(self, text: str, kind: str, *, role: str = "") -> QLabel:
        stick = self._near_bottom()
        self._clear_placeholder()
        if role:
            cap = QLabel(role)
            cap.setObjectName("bubble_role")
            self._col.insertWidget(self._col.count() - 1, cap)
        bubble = QLabel(text)
        bubble.setObjectName(f"bubble_{kind}")
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._col.insertWidget(self._col.count() - 1, bubble)
        if stick:
            self._scroll_to_bottom()
        return bubble

    def _show_placeholder(self):
        if self._placeholder is not None:
            return
        self._placeholder = self._add(
            f"Ask about anything on your screen.\n"
            f"Hold {_hotkey_label()} to talk, or type below.",
            "system",
        )

    def _clear_placeholder(self):
        if self._placeholder is not None:
            self._placeholder.setParent(None)
            self._placeholder.deleteLater()
            self._placeholder = None

    # ── Public API ────────────────────────────────────────────────────────

    def add_user(self, text: str):
        self.end_assistant()
        self._add(text, "user", role="You")

    def add_error(self, text: str):
        self.end_assistant()
        self._add(text, "error", role="Error")

    def add_system(self, text: str):
        self._add(text, "system")

    def begin_assistant(self):
        """Open a fresh assistant bubble for the next streamed response."""
        self.end_assistant()
        self._live_text = ""
        self._live = self._add("", "assistant", role="ScreenGuide")

    def append_assistant(self, chunk: str):
        if self._live is None:
            self.begin_assistant()
        stick = self._near_bottom()
        self._live_text += chunk
        self._live.setText(self._live_text)
        if stick:
            self._scroll_to_bottom()

    def end_assistant(self):
        """Close the streaming bubble. Drops it if nothing was ever written."""
        if self._live is not None and not self._live_text.strip():
            # Nothing streamed (cancelled, or a voice command handled locally)
            # — remove the empty bubble and its role caption.
            idx = self._col.indexOf(self._live)
            self._live.setParent(None)
            self._live.deleteLater()
            if idx > 0:
                cap = self._col.itemAt(idx - 1).widget()
                if cap is not None and cap.objectName() == "bubble_role":
                    cap.setParent(None)
                    cap.deleteLater()
        self._live = None
        self._live_text = ""

    def clear(self):
        while self._col.count() > 1:
            item = self._col.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._live = None
        self._live_text = ""
        self._placeholder = None
        self._show_placeholder()


class CompanionPanel(QWidget):
    """Floating companion control panel — equivalent to CompanionPanelView.swift."""

    on_push_to_talk_pressed  = pyqtSignal()
    on_push_to_talk_released = pyqtSignal()
    on_model_changed         = pyqtSignal(str)
    on_document_dropped      = pyqtSignal(str)
    on_text_submitted        = pyqtSignal(str)
    on_stop_clicked          = pyqtSignal()
    _sig_copilot_code        = pyqtSignal(str, str)   # (user_code, verification_uri)
    _sig_copilot_error       = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._state = AppState.IDLE
        self._phase = ""
        self._busy_since = 0.0
        self._last_question = ""
        # True when the panel put itself on screen for a turn (so it may take
        # itself away again). False when the user opened it from the tray,
        # which pins it until they close it.
        self._auto_shown = False
        self._setup_window()
        self._build_ui()
        self._position_bottom_right()

        # Ticks the elapsed-time readout while a request is in flight.
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(100)
        self._elapsed_timer.timeout.connect(self._refresh_status)

        # Retires the panel a few seconds after a turn ends.
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(PANEL_AUTO_HIDE_MS)
        self._hide_timer.timeout.connect(self._maybe_auto_hide)

        self._sig_copilot_code.connect(self._on_copilot_code)
        self._sig_copilot_error.connect(self._on_copilot_error)

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(PANEL_WIDTH, PANEL_HEIGHT)
        self.setObjectName("panel")
        self.setStyleSheet(PANEL_QSS)
        self.setAcceptDrops(True)   # drag-drop PDFs / DOCX / TXT

    # ── Drag-drop handlers ───────────────────────────────────────────────────
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self.on_document_dropped.emit(path)
        event.acceptProposedAction()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(11, 11, 11, 11)
        root.setSpacing(6)

        # ── Header ──────────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("ScreenGuide")
        title.setObjectName("title")
        title.setFont(FONT_TITLE)
        header.addWidget(title)
        header.addStretch()
        self._badge = ProviderBadge(cfg.llm_provider())
        header.addWidget(self._badge)

        self._min_btn = QPushButton("—")
        self._min_btn.setFixedSize(20, 20)
        self._min_btn.setStyleSheet(
            "QPushButton { background: rgba(60,60,75,180); color: rgb(220,220,230);"
            "border: none; border-radius: 10px; font-size: 12px; font-weight: bold; }"
            "QPushButton:hover { background: rgba(80,80,95,220); }"
        )
        self._min_btn.setToolTip("Hide panel (hold the hotkey to bring it back)")
        self._min_btn.clicked.connect(self.dismiss)
        header.addWidget(self._min_btn)
        root.addLayout(header)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color: rgba(60,60,75,180);")
        root.addWidget(div)

        # ── Status row ──────────────────────────────────────────────────
        self._status_dot = QLabel("●")
        self._status_label = QLabel(STATE_LABELS[AppState.IDLE])
        self._status_label.setObjectName("status")
        self._status_label.setFont(FONT_STATUS)
        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        status_row.addWidget(self._status_dot)
        status_row.addWidget(self._status_label, stretch=1)

        # Visible cancel affordance. Esc was already bound, but nothing on
        # screen said so — a long local-model run just looked frozen.
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setObjectName("stop_btn")
        self._stop_btn.setToolTip("Cancel this response (Esc)")
        self._stop_btn.clicked.connect(self.on_stop_clicked.emit)
        self._stop_btn.setVisible(False)
        status_row.addWidget(self._stop_btn)
        root.addLayout(status_row)
        self._apply_state_color(AppState.IDLE)

        # ── Waveform (listening only) ───────────────────────────────────
        self._waveform = WaveformWidget()
        self._waveform.setVisible(False)
        root.addWidget(self._waveform)

        # ── Conversation ────────────────────────────────────────────────
        self._convo = ConversationView()
        root.addWidget(self._convo, stretch=1)

        # ── Composer ────────────────────────────────────────────────────
        compose = QHBoxLayout()
        compose.setSpacing(6)
        self._input = QLineEdit()
        self._input.setObjectName("composer")
        self._input.setPlaceholderText("Type a question…")
        self._input.returnPressed.connect(self._submit_text)
        compose.addWidget(self._input, stretch=1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setObjectName("icon_btn")
        self._send_btn.clicked.connect(self._submit_text)
        compose.addWidget(self._send_btn)
        root.addLayout(compose)

        self._meter = MicMeter()
        root.addWidget(self._meter)

        # ── Footer: model picker + retry ────────────────────────────────
        footer = QHBoxLayout()
        footer.setSpacing(6)
        lbl = QLabel("Model:")
        lbl.setFont(FONT_LABEL)
        lbl.setStyleSheet("color: rgb(100,100,120); font-size: 11px;")
        self._model_combo = QComboBox()
        self._model_combo.setStyleSheet(
            "background: rgba(40,40,50,200); border: 1px solid rgba(60,60,75,180);"
            "border-radius: 6px; color: rgb(200,200,215); padding: 2px 6px; font-size: 11px;"
        )
        self._populate_models()
        self._model_combo.currentIndexChanged.connect(
            lambda _idx: self.on_model_changed.emit(
                self._model_combo.currentData() or ""
            )
        )
        self._retry_btn = QPushButton("Retry")
        self._retry_btn.setObjectName("icon_btn")
        self._retry_btn.setToolTip("Ask the last question again")
        self._retry_btn.clicked.connect(self._retry)
        self._retry_btn.setEnabled(False)

        footer.addWidget(lbl)
        footer.addWidget(self._model_combo, stretch=1)
        footer.addWidget(self._retry_btn)
        root.addLayout(footer)

    # ── Composer actions ──────────────────────────────────────────────────

    def _submit_text(self):
        text = self._input.text().strip()
        if not text or self._state in BUSY_STATES:
            return
        self._input.clear()
        self.on_text_submitted.emit(text)

    def _retry(self):
        if self._last_question and self._state not in BUSY_STATES:
            self.on_text_submitted.emit(self._last_question)

    # ── Model dropdown ────────────────────────────────────────────────────

    def _populate_models(self):
        self._set_models_for(cfg.llm_provider())

    def _set_models_for(self, provider: str):
        # Avoid firing on_model_changed while we rebuild
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        if provider == "copilot":
            for mid, label in _copilot_model_choices():
                self._model_combo.addItem(label, userData=mid)
        elif provider in ("claude", "openai", "gemini"):
            try:
                from ai.model_registry import cached_models
                for m in cached_models(provider):
                    label = m["id"]
                    if not m.get("vision"):
                        label += "  (no vision)"
                    self._model_combo.addItem(label, userData=m["id"])
            except Exception:
                self._model_combo.addItem("default", userData="default")
        elif provider == "lmstudio":
            self._model_combo.addItem(
                cfg.lmstudio_model or "Auto — whatever's loaded",
                userData=cfg.lmstudio_model,
            )
        else:   # ollama
            # Default to auto so OllamaProvider keeps choosing the vision slot
            # for screenshot turns and the text slot otherwise. Pinning a single
            # model here would force one of them onto both kinds of query.
            vis = cfg.get_ollama_model("vision")
            txt = cfg.get_ollama_model("text")
            self._model_combo.addItem(f"Auto — {vis} / {txt}", userData="")
            for name in dict.fromkeys((vis, txt)):
                if name:
                    self._model_combo.addItem(f"Pin to {name}", userData=name)
        self._model_combo.blockSignals(False)
        if self._model_combo.count():
            self.on_model_changed.emit(self._model_combo.currentData() or "")

    def refresh_for_provider(self, provider: str):
        """Called from outside when the active provider is switched at runtime."""
        self._badge.set_provider(provider)
        self._set_models_for(provider)

    def _position_bottom_right(self):
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - PANEL_WIDTH - 20,
                  screen.bottom() - PANEL_HEIGHT - 20)

    # ── Show / hide lifecycle ─────────────────────────────────────────────
    #
    # The panel is hidden at rest. It appears for a turn when the user holds
    # the hotkey or says the wake word, then retires itself once the turn has
    # been idle for a few seconds — unless the user is reading or typing in
    # it, or opened it deliberately from the tray.

    def reveal_for_turn(self):
        """Bring the panel up for an incoming question."""
        self._hide_timer.stop()
        self._auto_shown = True
        if not self.isVisible():
            self.show()
        self.raise_()

    def show_pinned(self):
        """Opened deliberately (tray / double-click) — stays until dismissed."""
        self._hide_timer.stop()
        self._auto_shown = False
        self.show()
        self.raise_()

    def dismiss(self):
        """Hide and forget any pending auto-hide."""
        self._hide_timer.stop()
        self._auto_shown = False
        self.hide()

    def _busy_in_panel(self) -> bool:
        """True when hiding would interrupt the user mid-read or mid-type."""
        return (
            self.underMouse()
            or self._input.hasFocus()
            or bool(self._input.text().strip())
        )

    def _maybe_auto_hide(self):
        if not self._auto_shown or self._state in BUSY_STATES:
            return
        if self._busy_in_panel():
            self._hide_timer.start()      # check again later
            return
        self.hide()
        self._auto_shown = False

    def leaveEvent(self, event):
        # Moving the cursor away re-arms a hide that was deferred by hover.
        super().leaveEvent(event)
        if self._auto_shown and self._state not in BUSY_STATES:
            self._hide_timer.start()

    # ── State + progress ──────────────────────────────────────────────────

    def _apply_state_color(self, state: AppState):
        c = STATE_COLORS[state]
        self._status_dot.setStyleSheet(
            f"color: rgb({c.red()},{c.green()},{c.blue()}); font-size: 10px;"
        )

    def _refresh_status(self):
        """Status line = phase (or state label) + elapsed seconds while busy.

        The elapsed readout matters most on local models, where a single
        answer can take a minute on CPU and the old static 'Thinking…' was
        indistinguishable from a hang.
        """
        base = self._phase or STATE_LABELS[self._state]
        if self._state in BUSY_STATES and self._busy_since:
            base = f"{base}  {time.monotonic() - self._busy_since:.1f}s"
        self._status_label.setText(base)

    def set_state(self, state: AppState):
        was_busy = self._state in BUSY_STATES
        self._state = state
        self._apply_state_color(state)

        if state in BUSY_STATES:
            if not was_busy:
                self._busy_since = time.monotonic()
                self._elapsed_timer.start()
            self._hide_timer.stop()
        else:
            self._elapsed_timer.stop()
            self._busy_since = 0.0
            self._phase = ""
            if state == AppState.LISTENING:
                self._convo.end_assistant()

        if state == AppState.LISTENING:
            # A question is starting — make sure the panel is on screen for it.
            self.reveal_for_turn()
        elif state == AppState.IDLE:
            self._convo.end_assistant()
            if self._auto_shown:
                self._hide_timer.start()

        self._stop_btn.setVisible(state in BUSY_STATES)
        self._input.setEnabled(state not in BUSY_STATES)
        self._send_btn.setEnabled(state not in BUSY_STATES)
        self._retry_btn.setEnabled(
            bool(self._last_question) and state not in BUSY_STATES
        )

        self._waveform.setVisible(state == AppState.LISTENING)
        if state == AppState.LISTENING:
            self._waveform.start()
        else:
            self._waveform.stop()

        self._meter.setVisible(state == AppState.IDLE)
        self._refresh_status()

    def set_phase(self, phase: str):
        """Sub-step within THINKING (capturing screen, locating, generating…)."""
        self._phase = phase
        self._refresh_status()

    # ── Conversation API ──────────────────────────────────────────────────

    def show_transcript(self, text: str):
        """Render what STT actually heard.

        Previously the transcript only went to the log file, so a misheard
        question was indistinguishable from a bad answer.
        """
        self._last_question = text
        self._convo.add_user(text)
        self._convo.begin_assistant()

    def append_response_chunk(self, chunk: str):
        self._convo.append_assistant(chunk)

    def response_done(self, _text: str = ""):
        self._convo.end_assistant()

    def show_error(self, text: str):
        """Errors used to go only to a Windows toast, which is easy to miss
        and often disabled system-wide."""
        self._convo.add_error(text)

    def show_notice(self, text: str):
        """Transient status line in the transcript (model downloads etc.)."""
        self._convo.add_system(text)

    def clear_conversation(self):
        self._last_question = ""
        self._retry_btn.setEnabled(False)
        self._convo.clear()

    def set_audio_level(self, rms: float):
        self._waveform.set_level(rms)
        self._meter.set_level(rms)

    # ── Copilot device-flow login ─────────────────────────────────────────

    def show_copilot_code(self, user_code: str, verification_uri: str):
        """Thread-safe: can be called from any thread. Emits a queued signal
        so the UI update always runs on the Qt main thread."""
        self._sig_copilot_code.emit(user_code, verification_uri)

    def show_copilot_error(self, error: str):
        """Thread-safe version of showing a Copilot login error."""
        self._sig_copilot_error.emit(error)

    def _on_copilot_code(self, user_code: str, verification_uri: str):
        # Pinned: the user has to leave and authorize in a browser, so this
        # must not time out and vanish while they're doing it.
        self.show_pinned()
        self._convo.add_system(
            "GitHub Copilot sign-in\n"
            f"1.  Open:  {verification_uri}\n"
            f"2.  Enter code:  {user_code}\n"
            "3.  Click Authorize — ScreenGuide signs in automatically."
        )
        self._phase = "Waiting for Copilot authorization…"
        self._refresh_status()

    def _on_copilot_error(self, error: str):
        self._convo.add_error(f"Copilot login failed: {error}")

    # ── Mouse drag to reposition ──────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and hasattr(self, '_drag_pos'):
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    # ── Painting: rounded glass background ────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(18, 18, 22, 235)))
        painter.setPen(QPen(QColor(60, 60, 75, 180), 1))
        painter.drawRoundedRect(self.rect(), PANEL_RADIUS, PANEL_RADIUS)
        painter.end()
