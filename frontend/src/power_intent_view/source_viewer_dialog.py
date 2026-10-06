"""'Inspect Source File' viewer: a read-only, non-modal window showing
the full content of an RTL hierarchy's own .v/.sv/.vhd file, or a
macro's own .lib file -- triggered from the Power Intent tab's Diagram
right-click "Inspect Source File" action (see power_intent_diagram_view.py's
_extra_context_menu_actions()), positioned directly below "Hide".

Non-modal (`.show()`, never `.exec()`) -- deliberately, unlike every
other dialog in this module (AddPowerSupplyDialog, CreateSupplySetDialog,
CreatePowerDomainDialog): those are all one-shot form-entry flows
(name/colour, Ok/Cancel) that make sense to block on. This is pure,
possibly-long-lived inspection -- scroll around, compare against the
diagram, maybe open a second file alongside -- and must never prevent
continuing to interact with the rest of the app. No existing dock
(Hierarchy Tree/Properties -- persistent, tied to the whole design) or
modal-dialog convention in this codebase matches this exact "read-only,
scrollable, transient, independent-of-app-state" need, so this is a new
top-level, non-modal QDialog instead.

QPlainTextEdit (not QTextEdit) per explicit request -- better
performance for a long source file, and this viewer never needs rich
text beyond the syntax highlighter's own QTextCharFormat ranges, which
both widgets support identically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase, QKeySequence, QShortcut, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget

from frontend.src.power_intent_view.source_syntax_highlighters import create_highlighter_for_path

_FIND_NOT_FOUND_STYLE = "background-color: #5a1d1d; color: white;"
_MATCH_HIGHLIGHT_COLOR = QColor("#515c00")


class SourceViewerDialog(QDialog):
    def __init__(self, source_path: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setSizeGripEnabled(True)  # explicit request (Bug 5): every dialog here must be user-resizable
        self.setModal(False)
        self.setWindowTitle(f"Inspect Source File -- {Path(source_path).name}")
        self.resize(900, 700)
        self.source_path = source_path

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text_edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        self._highlighter = None
        self._load_content(source_path)

        self.find_bar = _FindBar(self.text_edit)
        self.find_bar.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.find_bar)
        layout.addWidget(self.text_edit)

        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        find_shortcut.activated.connect(self._toggle_find_bar)

    def _load_content(self, source_path: str) -> None:
        try:
            content = Path(source_path).read_text()
        except OSError as exc:
            # A real, possible failure -- the file could have moved/been
            # deleted since elaboration, or be unreadable for permission
            # reasons -- shown IN the viewer itself (not a separate
            # QMessageBox) so it's visible in the same place the content
            # would otherwise be, and so this method has one return path
            # for both outcomes rather than a raise the caller must
            # remember to catch.
            self.text_edit.setPlainText(f"Could not read source file:\n{source_path}\n\n{exc}")
            return
        self.text_edit.setPlainText(content)
        self._highlighter = create_highlighter_for_path(source_path, self.text_edit.document())
        if self._highlighter is not None:
            # QSyntaxHighlighter's own automatic highlighting on attach
            # is deferred to the event loop's own processing of the
            # document's contentsChange signal -- confirmed empirically
            # (a bare setDocument()/construction alone left every block's
            # own format list empty until either the event loop actually
            # ran or rehighlight() was called explicitly). Calling it
            # here guarantees the file is fully highlighted the instant
            # this dialog becomes visible, not on whatever the next
            # incidental event-loop tick happens to be.
            self._highlighter.rehighlight()

    def _toggle_find_bar(self) -> None:
        if self.find_bar.isVisible():
            self.find_bar.close_bar()
        else:
            self.find_bar.open_bar()


class _FindBar(QWidget):
    """Ctrl+F find bar -- QLineEdit + Previous/Next, docked above the
    text view. Highlights EVERY occurrence via QTextEdit.ExtraSelection
    (the standard Qt pattern for highlighting without touching the
    actual document content -- confirmed QPlainTextEdit supports both
    `.find()` and `.setExtraSelections()` despite `ExtraSelection` itself
    only being reachable via the QTextEdit class, not QPlainTextEdit)."""

    def __init__(self, text_edit: QPlainTextEdit, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._text_edit = text_edit

        self.line_edit = QLineEdit()
        self.line_edit.setPlaceholderText("Find...")
        self.line_edit.returnPressed.connect(lambda: self._find(forward=True))
        self.line_edit.textChanged.connect(self._on_text_changed)

        self.prev_button = QPushButton("Previous")
        self.prev_button.clicked.connect(lambda: self._find(forward=False))
        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(lambda: self._find(forward=True))

        self.status_label = QLabel("")

        close_button = QPushButton("✕")
        close_button.setFixedWidth(24)
        close_button.setToolTip("Close (Esc)")
        close_button.clicked.connect(self.close_bar)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.line_edit)
        layout.addWidget(self.prev_button)
        layout.addWidget(self.next_button)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        layout.addWidget(close_button)

        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.activated.connect(self.close_bar)

    def open_bar(self) -> None:
        self.setVisible(True)
        self.line_edit.setFocus()
        self.line_edit.selectAll()
        if self.line_edit.text():
            self._highlight_all(self.line_edit.text())

    def close_bar(self) -> None:
        self.setVisible(False)
        self._clear_highlights()
        self._text_edit.setFocus()

    def _on_text_changed(self, text: str) -> None:
        self._highlight_all(text)
        if not text:
            self.status_label.setText("")
            self.line_edit.setStyleSheet("")
            return
        # Typing changes the search term -- re-search from the START of
        # the document each time rather than from wherever the cursor
        # happens to already be, so the first match found always matches
        # what a user typing a fresh query expects to land on.
        cursor = self._text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self._text_edit.setTextCursor(cursor)
        self._find(forward=True)

    def _find(self, forward: bool) -> None:
        text = self.line_edit.text()
        if not text:
            return
        flags = QTextDocument.FindFlag(0) if forward else QTextDocument.FindFlag.FindBackward
        found = self._text_edit.find(text, flags)
        if not found:
            # Wrap around: a forward search that ran off the END of the
            # document with no match must restart scanning FROM THE
            # START to keep searching forward (and symmetrically for a
            # backward search restarting from the end) -- real, confirmed
            # bug found writing this dialog's own smoke test: this was
            # originally inverted (jumping to End for a forward search),
            # which searches forward from a position with nothing after
            # it, so it could never find even a single, real, present
            # match on the retry.
            cursor = self._text_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start if forward else QTextCursor.MoveOperation.End)
            self._text_edit.setTextCursor(cursor)
            found = self._text_edit.find(text, flags)
        if found:
            self.line_edit.setStyleSheet("")
            self.status_label.setText("")
        else:
            # Genuinely absent from the whole document, not just "not
            # found from here" -- the wrap-around retry above already
            # covered the rest of the document.
            self.line_edit.setStyleSheet(_FIND_NOT_FOUND_STYLE)
            self.status_label.setText("Not found")

    def _highlight_all(self, text: str) -> None:
        if not text:
            self._clear_highlights()
            return
        fmt = QTextCharFormat()
        fmt.setBackground(_MATCH_HIGHLIGHT_COLOR)
        selections: list[QTextEdit.ExtraSelection] = []
        doc = self._text_edit.document()
        cursor = QTextCursor(doc)
        while True:
            cursor = doc.find(text, cursor)
            if cursor.isNull():
                break
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = fmt
            selections.append(selection)
        self._text_edit.setExtraSelections(selections)

    def _clear_highlights(self) -> None:
        self._text_edit.setExtraSelections([])
