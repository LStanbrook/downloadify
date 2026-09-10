"""
Dialog for entering a Spotify Client ID from inside the app, so people
don't have to hand-edit a .env file. Backs the "Set up Spotify" flow in
the main window.
"""

from __future__ import annotations

import webbrowser

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from downloadify import config

_GUIDE_URL = "https://downloadify.co.uk/setup.html"


class SpotifyKeyDialog(QDialog):
    """One text field for the Client ID, plus a link to the full guide."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set up Spotify")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "Paste the Client ID from your own free Spotify app to turn on "
            "login (for private and personalized playlists) and playlists "
            "over 100 tracks. It takes about two minutes to create one, it's "
            "free, and it doesn't need Spotify Premium."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        guide_btn = QPushButton("Open the step-by-step guide")
        guide_btn.setObjectName("browseButton")
        guide_btn.clicked.connect(lambda: webbrowser.open(_GUIDE_URL))
        layout.addWidget(guide_btn)

        layout.addSpacing(6)
        client_id_label = QLabel("CLIENT ID")
        client_id_label.setObjectName("FieldLabel")
        layout.addWidget(client_id_label)

        self.client_id_input = QLineEdit(config.SPOTIFY_CLIENT_ID)
        self.client_id_input.setPlaceholderText("e.g. 21da1c94f3954d13b770830b0b7b9f26")
        layout.addWidget(self.client_id_input)

        layout.addSpacing(8)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("downloadButton")
        save_btn.clicked.connect(self._save)
        button_row.addWidget(save_btn)
        layout.addLayout(button_row)

    def _save(self) -> None:
        client_id = self.client_id_input.text().strip()
        if not client_id:
            QMessageBox.warning(
                self, "Missing Client ID", "Paste your Spotify Client ID first."
            )
            self.client_id_input.setFocus()
            return
        try:
            config.save_spotify_client_id(client_id)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Couldn't save",
                "Downloadify couldn't write its settings file next to the "
                f"app ({exc}). Try moving Downloadify to a folder you can "
                "write to, like your Desktop or Downloads.",
            )
            return
        self.accept()
