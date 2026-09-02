"""
Knowledge Journal browser.

Every Q&A already lands in SQLite (see tutor_features/journal.py), but until
now the only way to read any of it back was to ask "what did we cover today?"
out loud and get a ten-item spoken summary. This is the window over it:
search across both sides of the exchange, filter by date and app, read the
full answer, re-ask a question, or delete an entry.

Opened from Tray → Journal → Browse history…
"""

import time
from datetime import datetime, timedelta

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QListWidget, QListWidgetItem, QScrollArea, QFrame,
    QSplitter, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from tutor_features import journal


HISTORY_QSS = """
QWidget#history {
    background-color: rgb(20, 20, 25);
}
QLabel { color: rgb(236, 236, 244); background: transparent; }
QLabel#h_title { font-size: 15px; font-weight: bold; }
QLabel#h_meta  { color: rgb(132, 132, 156); font-size: 11px; }
QLabel#h_count { color: rgb(132, 132, 156); font-size: 11px; }
QLabel#h_q {
    color: rgb(214, 228, 255); font-size: 13px; font-weight: 600;
    background: rgba(0,120,255,28); border: 1px solid rgba(0,120,255,80);
    border-radius: 8px; padding: 8px 10px;
}
QLabel#h_a {
    color: rgb(226, 226, 236); font-size: 13px;
    background: rgba(44,44,54,190); border: 1px solid rgba(70,70,88,150);
    border-radius: 8px; padding: 8px 10px;
}
QLabel#h_empty { color: rgb(120, 120, 145); font-size: 12px; }
QLineEdit {
    background: rgba(38,38,48,220); border: 1px solid rgba(70,70,88,170);
    border-radius: 8px; color: rgb(236,236,244); font-size: 13px;
    padding: 6px 10px;
    selection-background-color: rgba(0,120,255,140);
}
QLineEdit:focus { border: 1px solid rgba(0,120,255,190); }
QComboBox {
    background: rgba(38,38,48,220); border: 1px solid rgba(70,70,88,170);
    border-radius: 8px; color: rgb(220,220,232); font-size: 12px;
    padding: 5px 8px;
}
QComboBox QAbstractItemView {
    background: rgb(30,30,38); color: rgb(226,226,236);
    selection-background-color: rgba(0,120,255,120);
    border: 1px solid rgba(70,70,88,170);
}
QListWidget {
    background: rgba(28,28,35,200); border: 1px solid rgba(60,60,75,150);
    border-radius: 8px; color: rgb(222,222,232); font-size: 12px;
    outline: none;
}
QListWidget::item { padding: 7px 9px; border-bottom: 1px solid rgba(60,60,75,90); }
QListWidget::item:selected { background: rgba(0,120,255,70); color: rgb(240,244,255); }
QListWidget::item:hover:!selected { background: rgba(70,70,90,90); }
QPushButton {
    background: rgba(52,52,64,200); border: 1px solid rgba(74,74,92,170);
    border-radius: 8px; color: rgb(228,228,238); font-size: 12px;
    font-weight: 600; padding: 6px 12px;
}
QPushButton:hover { background: rgba(70,70,86,230); }
QPushButton:disabled { background: rgba(40,40,50,140); color: rgb(96,96,116); }
QPushButton#danger {
    background: rgba(255,70,70,28); border: 1px solid rgba(255,70,70,140);
    color: rgb(255,160,160);
}
QPushButton#danger:hover { background: rgba(255,70,70,60); }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 6px; }
QScrollBar::handle:vertical { background: rgba(100,100,120,140); border-radius: 3px; }
QSplitter::handle { background: rgba(60,60,75,120); }
"""


RANGES = [
    ("All time",   None),
    ("Today",      "today"),
    ("Last 7 days", 7 * 86400),
    ("Last 30 days", 30 * 86400),
]


def _since_for(choice) -> float | None:
    if choice is None:
        return None
    if choice == "today":
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp()
    return time.time() - float(choice)


def _rel_day(ts: float) -> str:
    d = datetime.fromtimestamp(ts).date()
    today = datetime.now().date()
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return d.strftime("%a %d %b %Y")


class HistoryWindow(QWidget):
    """Searchable browser over the knowledge journal."""

    on_ask_again = pyqtSignal(str)

    PAGE = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("history")
        self.setWindowTitle("ScreenGuide — Journal")
        self.setStyleSheet(HISTORY_QSS)
        self.resize(860, 560)
        self.setMinimumSize(620, 420)

        self._rows: list[dict] = []
        self._current: dict | None = None

        # Typing shouldn't hit SQLite on every keystroke.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self.reload)

        self._build_ui()
        self.reload()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        head = QHBoxLayout()
        t = QLabel("Journal")
        t.setObjectName("h_title")
        head.addWidget(t)
        head.addStretch()
        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("h_count")
        head.addWidget(self._count_lbl)
        root.addLayout(head)

        # Filters
        filt = QHBoxLayout()
        filt.setSpacing(7)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search questions and answers…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _t: self._debounce.start())
        filt.addWidget(self._search, stretch=1)

        self._range = QComboBox()
        for label, val in RANGES:
            self._range.addItem(label, userData=val)
        self._range.currentIndexChanged.connect(lambda _i: self.reload())
        filt.addWidget(self._range)

        self._app = QComboBox()
        self._app.currentIndexChanged.connect(lambda _i: self.reload())
        filt.addWidget(self._app)
        root.addLayout(filt)

        # List + detail
        split = QSplitter(Qt.Orientation.Horizontal)

        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_select)
        split.addWidget(self._list)

        detail_host = QWidget()
        detail_host.setStyleSheet("background: transparent;")
        dcol = QVBoxLayout(detail_host)
        dcol.setContentsMargins(0, 0, 0, 0)
        dcol.setSpacing(8)

        self._meta = QLabel("")
        self._meta.setObjectName("h_meta")
        self._meta.setWordWrap(True)
        dcol.addWidget(self._meta)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        icol = QVBoxLayout(inner)
        icol.setContentsMargins(0, 0, 4, 0)
        icol.setSpacing(8)
        self._q = QLabel("")
        self._q.setObjectName("h_q")
        self._q.setWordWrap(True)
        self._q.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._a = QLabel("")
        self._a.setObjectName("h_a")
        self._a.setWordWrap(True)
        self._a.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        icol.addWidget(self._q)
        icol.addWidget(self._a)
        icol.addStretch(1)
        scroll.setWidget(inner)
        dcol.addWidget(scroll, stretch=1)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        self._ask_btn = QPushButton("Ask again")
        self._ask_btn.setToolTip("Send this question to ScreenGuide again")
        self._ask_btn.clicked.connect(self._ask_again)
        self._copy_btn = QPushButton("Copy answer")
        self._copy_btn.clicked.connect(self._copy)
        self._del_btn = QPushButton("Delete")
        self._del_btn.setObjectName("danger")
        self._del_btn.clicked.connect(self._delete)
        actions.addWidget(self._ask_btn)
        actions.addWidget(self._copy_btn)
        actions.addStretch()
        actions.addWidget(self._del_btn)
        dcol.addLayout(actions)

        split.addWidget(detail_host)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([300, 460])
        root.addWidget(split, stretch=1)

        self._empty = QLabel("")
        self._empty.setObjectName("h_empty")
        self._empty.setVisible(False)
        root.addWidget(self._empty)

        self._set_detail(None)

    # ── Data ──────────────────────────────────────────────────────────────

    def _filters(self) -> tuple[str, float | None, str]:
        return (
            self._search.text(),
            _since_for(self._range.currentData()),
            self._app.currentData() or "",
        )

    def _sync_app_filter(self):
        """Repopulate the app dropdown, preserving the current selection."""
        keep = self._app.currentData() or ""
        self._app.blockSignals(True)
        self._app.clear()
        self._app.addItem("All apps", userData="")
        for key in journal.distinct_apps():
            self._app.addItem(key, userData=key)
        idx = self._app.findData(keep)
        self._app.setCurrentIndex(idx if idx >= 0 else 0)
        self._app.blockSignals(False)

    def reload(self):
        self._sync_app_filter()
        text, since, app_key = self._filters()
        try:
            total = journal.count(text, since, app_key)
            self._rows = journal.search(text, since, app_key, limit=self.PAGE)
        except Exception as e:
            self._rows, total = [], 0
            self._empty.setText(f"Couldn't read the journal: {e}")
            self._empty.setVisible(True)

        self._list.blockSignals(True)
        self._list.clear()
        last_day = None
        for r in self._rows:
            day = _rel_day(r["created_at"])
            if day != last_day:
                sep = QListWidgetItem(day)
                sep.setFlags(Qt.ItemFlag.NoItemFlags)   # header, not selectable
                sep.setData(Qt.ItemDataRole.UserRole, None)
                self._list.addItem(sep)
                last_day = day
            when = datetime.fromtimestamp(r["created_at"]).strftime("%I:%M %p")
            q = " ".join(r["question"].split())
            item = QListWidgetItem(f"{when}   {q[:70]}")
            item.setData(Qt.ItemDataRole.UserRole, r["id"])
            item.setToolTip(q)
            self._list.addItem(item)
        self._list.blockSignals(False)

        shown = len(self._rows)
        if total == 0:
            self._count_lbl.setText("No entries")
            self._empty.setText(
                "Nothing logged yet — ask ScreenGuide something and it'll "
                "show up here."
                if not (text or since or app_key)
                else "No entries match this filter."
            )
            self._empty.setVisible(True)
        else:
            more = f" (showing {shown})" if total > shown else ""
            self._count_lbl.setText(f"{total} entr{'y' if total == 1 else 'ies'}{more}")
            self._empty.setVisible(False)

        self._select_first()

    def _select_first(self):
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) is not None:
                self._list.setCurrentRow(i)
                return
        self._set_detail(None)

    def _on_select(self, row: int):
        if row < 0 or row >= self._list.count():
            self._set_detail(None)
            return
        entry_id = self._list.item(row).data(Qt.ItemDataRole.UserRole)
        if entry_id is None:            # day header
            self._set_detail(None)
            return
        match = next((r for r in self._rows if r["id"] == entry_id), None)
        self._set_detail(match)

    def _set_detail(self, entry: dict | None):
        self._current = entry
        has = entry is not None
        for b in (self._ask_btn, self._copy_btn, self._del_btn):
            b.setEnabled(has)
        if not has:
            self._meta.setText("")
            self._q.setText("")
            self._a.setText("")
            self._q.setVisible(False)
            self._a.setVisible(False)
            return
        self._q.setVisible(True)
        self._a.setVisible(True)
        when = datetime.fromtimestamp(entry["created_at"]).strftime(
            "%a %d %b %Y · %I:%M %p"
        )
        bits = [when]
        if entry.get("window_title"):
            bits.append(entry["window_title"])
        provider = entry.get("provider") or ""
        model = entry.get("model") or ""
        if provider or model:
            bits.append(" ".join(x for x in (provider, model) if x))
        self._meta.setText("   ·   ".join(bits))
        self._q.setText(entry["question"])
        self._a.setText(entry["answer"])

    # ── Actions ───────────────────────────────────────────────────────────

    def _ask_again(self):
        if self._current:
            self.on_ask_again.emit(self._current["question"])

    def _copy(self):
        if not self._current:
            return
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._current["answer"])
        self._copy_btn.setText("Copied")
        QTimer.singleShot(1200, lambda: self._copy_btn.setText("Copy answer"))

    def _delete(self):
        if not self._current:
            return
        q = " ".join(self._current["question"].split())[:70]
        if QMessageBox.question(
            self, "Delete entry",
            f"Delete this journal entry?\n\n{q}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            journal.delete(self._current["id"])
        except Exception as e:
            QMessageBox.warning(self, "Delete failed", str(e))
            return
        self.reload()
