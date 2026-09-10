"""The Downloadify desktop GUI: a single window built with PyQt6."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFontDatabase, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from downloadify import config
from downloadify.core import spotify_auth
from downloadify.core.models import PlaylistDownloadSummary, PlaylistSummary, TrackResult, TrackStatus
from downloadify.gui.playlist_picker import PlaylistPickerDialog
from downloadify.gui.worker import DownloadWorker, PlaylistListWorker, SpotifyLoginWorker

_STYLESHEET_PATH = Path(__file__).resolve().parent / "style.qss"
# Same font file embedded (as woff2) in the website's <h1> -- extracted to a
# .ttf once so the desktop title reads as the identical typeface, not just a
# similarly-bold system font standing in for it.
_DISPLAY_FONT_PATH = Path(__file__).resolve().parent / "fonts" / "BigShouldersDisplay-Black.ttf"

_STATUS_COLOR_SUCCESS = "#1db954"
_STATUS_COLOR_WARNING = "#e0a030"
_STATUS_COLOR_ERROR = "#e64b4b"

_BUTTON_ICON_SIZE = QSize(20, 20)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Downloadify")
        self.resize(1200, 860)
        self.setMinimumSize(780, 580)

        self._worker: DownloadWorker | None = None
        self._login_worker: SpotifyLoginWorker | None = None
        self._playlist_list_worker: PlaylistListWorker | None = None

        self._build_ui()
        self._update_login_state()

    # -- UI construction ---------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(36, 32, 36, 28)
        root.setSpacing(24)

        root.addLayout(self._build_header())

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_controls_panel())
        splitter.addWidget(self._build_log_panel())
        # The controls panel keeps its natural size; the log panel absorbs
        # any extra space, so shrinking/maximizing the window mostly just
        # grows or shrinks the log -- the part most worth resizing.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 500])
        root.addWidget(splitter, stretch=1)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()

        text_col = QVBoxLayout()
        text_col.setSpacing(6)
        title = QLabel()
        title.setObjectName("HeaderTitle")
        # Matches the website wordmark: a small green "signal" dot beside
        # the name, rather than coloring the whole title green. The dot
        # explicitly pins a normal font since the display font's glyph set
        # isn't guaranteed to include a bullet character.
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setText(
            '<span style="color: #1db954; font-family: \'Segoe UI\';">●</span>'
            "&nbsp;&nbsp;DOWNLOADIFY"
        )
        text_col.addWidget(title)
        subtitle = QLabel("Paste a public Spotify playlist link to download it as MP3s.")
        subtitle.setObjectName("HeaderSubtitle")
        text_col.addWidget(subtitle)
        header.addLayout(text_col)

        header.addStretch(1)

        login_col = QVBoxLayout()
        login_col.setSpacing(8)
        login_col.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        self.spotify_login_status = QLabel()
        self.spotify_login_status.setObjectName("HeaderSubtitle")
        self.spotify_login_status.setAlignment(Qt.AlignmentFlag.AlignRight)
        login_col.addWidget(self.spotify_login_status)
        self.spotify_login_btn = QPushButton()
        self.spotify_login_btn.setObjectName("browseButton")
        self.spotify_login_btn.clicked.connect(self._on_spotify_login_clicked)
        login_col.addWidget(self.spotify_login_btn)
        self.browse_playlists_btn = QPushButton("Browse my playlists…")
        self.browse_playlists_btn.setObjectName("browseButton")
        self.browse_playlists_btn.clicked.connect(self._on_browse_playlists_clicked)
        login_col.addWidget(self.browse_playlists_btn)
        self.spotify_key_btn = QPushButton("Change API key")
        self.spotify_key_btn.setObjectName("browseButton")
        self.spotify_key_btn.clicked.connect(self._prompt_for_spotify_key)
        login_col.addWidget(self.spotify_key_btn)
        header.addLayout(login_col)

        return header

    def _build_controls_panel(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(8)

        layout.addWidget(self._field_label("PLAYLIST URL"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        )
        layout.addWidget(self.url_input)

        layout.addSpacing(12)
        layout.addWidget(self._field_label("OUTPUT FOLDER"))
        folder_row = QHBoxLayout()
        folder_row.setSpacing(12)
        self.folder_input = QLineEdit(str(config.DEFAULT_DOWNLOAD_DIR))
        folder_row.addWidget(self.folder_input, stretch=1)
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("browseButton")
        browse_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        browse_btn.setIconSize(_BUTTON_ICON_SIZE)
        browse_btn.setMinimumWidth(130)
        browse_btn.clicked.connect(self._choose_folder)
        folder_row.addWidget(browse_btn)
        layout.addLayout(folder_row)

        layout.addSpacing(16)
        action_row = QHBoxLayout()
        action_row.setSpacing(14)
        self.download_btn = QPushButton("Download Playlist")
        self.download_btn.setObjectName("downloadButton")
        self.download_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        self.download_btn.setIconSize(_BUTTON_ICON_SIZE)
        self.download_btn.clicked.connect(self._start_download)
        action_row.addWidget(self.download_btn, stretch=1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancelButton")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setMinimumWidth(130)
        self.cancel_btn.clicked.connect(self._cancel_download)
        action_row.addWidget(self.cancel_btn)
        layout.addLayout(action_row)

        layout.addSpacing(18)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Idle.")
        self.status_label.setObjectName("StatusLabel")
        layout.addWidget(self.status_label)

        return card

    def _build_log_panel(self) -> QWidget:
        panel = QWidget()
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        log_header = QLabel("Progress log")
        log_header.setObjectName("LogHeader")
        header_row.addWidget(log_header)
        header_row.addStretch(1)
        self.full_logs_checkbox = QCheckBox("Show full logs")
        self.full_logs_checkbox.setChecked(False)
        header_row.addWidget(self.full_logs_checkbox)
        layout.addLayout(header_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.log_view, stretch=1)

        return panel

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    # -- UI actions ----------------------------------------------------

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose output folder", self.folder_input.text()
        )
        if folder:
            self.folder_input.setText(folder)

    def _update_login_state(self) -> None:
        logged_in = spotify_auth.is_logged_in()
        has_key = bool(config.SPOTIFY_CLIENT_ID)

        if logged_in:
            self.spotify_login_status.setText("Logged into Spotify")
            self.spotify_login_btn.setText("Log out")
        elif has_key:
            self.spotify_login_status.setText("Not logged into Spotify")
            self.spotify_login_btn.setText("Log in with Spotify")
        else:
            self.spotify_login_status.setText("Spotify not set up")
            self.spotify_login_btn.setText("Set up Spotify")

        self.spotify_login_btn.setEnabled(True)
        self.browse_playlists_btn.setEnabled(logged_in)
        # Nothing to change until a key has actually been entered.
        self.spotify_key_btn.setVisible(has_key)

    def _prompt_for_spotify_key(self) -> bool:
        """Open the Client ID dialog. Returns True once a key is set."""
        from downloadify.gui.spotify_key_dialog import SpotifyKeyDialog

        SpotifyKeyDialog(self).exec()
        self._update_login_state()
        return bool(config.SPOTIFY_CLIENT_ID)

    def _on_spotify_login_clicked(self) -> None:
        if spotify_auth.is_logged_in():
            spotify_auth.logout()
            self._update_login_state()
            return

        if not config.SPOTIFY_CLIENT_ID and not self._prompt_for_spotify_key():
            return  # user closed the setup dialog without entering a key

        self.spotify_login_btn.setEnabled(False)
        self.spotify_login_status.setText("Waiting for login in your browser…")

        self._login_worker = SpotifyLoginWorker()
        self._login_worker.succeeded.connect(self._on_login_succeeded)
        self._login_worker.failed.connect(self._on_login_failed)
        self._login_worker.start()

    def _on_login_succeeded(self) -> None:
        self._update_login_state()

    def _on_login_failed(self, error_message: str) -> None:
        self._update_login_state()
        QMessageBox.warning(self, "Spotify login failed", error_message)

    def _on_browse_playlists_clicked(self) -> None:
        self.browse_playlists_btn.setEnabled(False)
        self.browse_playlists_btn.setText("Loading…")

        self._playlist_list_worker = PlaylistListWorker()
        self._playlist_list_worker.succeeded.connect(self._on_playlists_listed)
        self._playlist_list_worker.failed.connect(self._on_playlists_list_failed)
        self._playlist_list_worker.start()

    def _on_playlists_listed(self, playlists: list[PlaylistSummary]) -> None:
        self.browse_playlists_btn.setText("Browse my playlists…")
        self.browse_playlists_btn.setEnabled(True)

        dialog = PlaylistPickerDialog(playlists, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_playlist:
            self.url_input.setText(dialog.selected_playlist.url)

    def _on_playlists_list_failed(self, error_message: str) -> None:
        self.browse_playlists_btn.setText("Browse my playlists…")
        self.browse_playlists_btn.setEnabled(True)
        QMessageBox.warning(self, "Couldn't list playlists", error_message)

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def _on_log_message(self, message: str, verbose: bool) -> None:
        if verbose and not self.full_logs_checkbox.isChecked():
            return
        self._append_log(message)

    def _set_status(self, text: str, color: str | None = None) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;" if color else "")

    def _start_download(self) -> None:
        playlist_url = self.url_input.text().strip()
        if not playlist_url:
            QMessageBox.warning(self, "Missing URL", "Please paste a Spotify playlist URL.")
            return

        output_dir = Path(self.folder_input.text().strip() or config.DEFAULT_DOWNLOAD_DIR)

        self.log_view.clear()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self._set_status("Starting…")
        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

        self._worker = DownloadWorker(playlist_url, output_dir)
        self._worker.log_message.connect(self._on_log_message)
        self._worker.progress_updated.connect(self._on_progress)
        self._worker.track_finished.connect(self._on_track_finished)
        self._worker.finished_ok.connect(self._on_finished_ok)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _cancel_download(self) -> None:
        if self._worker:
            self._worker.cancel()
            self._set_status("Cancelling…")
            self.cancel_btn.setEnabled(False)

    # -- Worker signal handlers ---------------------------------------

    def _on_progress(self, done: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)
        self.status_label.setText(f"Downloading… {done}/{total} tracks")

    def _on_track_finished(self, result: TrackResult) -> None:
        icon = "SUCCESS" if result.status == TrackStatus.DONE else "FAIL"
        line = f"[{icon}] {result.track.display_name}"
        if result.status != TrackStatus.DONE and result.error:
            line += f" -- {result.error}"
        self._append_log(line)

    def _on_finished_ok(self, summary: PlaylistDownloadSummary) -> None:
        # The log already has the per-track breakdown (and the list of any
        # failures, logged by the pipeline itself) -- a blocking popup on
        # top of that for a normal, successful run is more interruption
        # than the moment warrants, so this just updates the status line.
        self._set_status(
            f"Finished: {summary.succeeded}/{summary.total} downloaded.",
            _STATUS_COLOR_SUCCESS,
        )
        self._reset_ui_after_run()

    def _on_cancelled(self, summary: PlaylistDownloadSummary) -> None:
        self._set_status(
            f"Cancelled: {summary.succeeded}/{summary.total} downloaded before stopping.",
            _STATUS_COLOR_WARNING,
        )
        self._reset_ui_after_run()

    def _on_failed(self, error_message: str) -> None:
        # Unlike a normal finish, this means the run never really got going
        # (bad URL, playlist unreachable, etc.) -- a clear, blocking signal
        # is warranted here since there's no per-track log to fall back on.
        self._set_status("Failed.", _STATUS_COLOR_ERROR)
        self._append_log(f"ERROR: {error_message}")
        self._reset_ui_after_run()
        QMessageBox.critical(self, "Download failed", error_message)

    def _reset_ui_after_run(self) -> None:
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)


def _load_display_font() -> str | None:
    """Registers the bundled display font and returns its usable family
    name, or None if it's missing/unreadable (falls back to a bold system
    font rather than failing to launch)."""
    if not _DISPLAY_FONT_PATH.exists():
        return None
    font_id = QFontDatabase.addApplicationFont(str(_DISPLAY_FONT_PATH))
    if font_id == -1:
        return None
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        return None
    # This file only bakes in the single Black (900) weight used by the
    # website's <h1>/<h2>, but Qt can report more than one family name for
    # it (a quirk of how Google Fonts names static instances cut from a
    # variable font) -- prefer whichever name actually says "Black" so a
    # lighter-weight alias isn't picked by accident.
    for name in families:
        if "black" in name.lower():
            return name
    return families[-1]


def run_gui() -> None:
    """Entrypoint used by main.py to launch the desktop app."""
    app = QApplication(sys.argv)
    display_font_family = _load_display_font()

    stylesheet = ""
    try:
        stylesheet = _STYLESHEET_PATH.read_text(encoding="utf-8")
    except OSError:
        pass  # Fall back to the default Qt look rather than fail to launch.

    # Qt's stylesheet cascade overrides a plain QWidget.setFont() call for
    # any property the stylesheet already touches -- including this file's
    # blanket `QWidget { font-family; font-size }` rule -- so the title's
    # font has to be injected into the cascade itself (as a more-specific
    # #HeaderTitle rule) rather than relied on via setFont() alone.
    title_family = display_font_family or "Segoe UI"
    stylesheet += (
        f'\nQLabel#HeaderTitle {{ font-family: "{title_family}"; '
        "font-size: 48pt; font-weight: 900; }\n"
    )
    app.setStyleSheet(stylesheet)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
