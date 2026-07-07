"""A simple dialog for picking a playlist out of the logged-in user's own library."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from downloadify.core.models import PlaylistSummary


class PlaylistPickerDialog(QDialog):
    """
    Lets the user choose one of their own Spotify playlists instead of
    pasting a link -- useful for playlists that don't resolve reliably by
    URL (a personalized playlist, or one whose link they don't have handy).
    """

    def __init__(self, playlists: list[PlaylistSummary], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose a playlist")
        self.resize(560, 480)
        self.selected_playlist: PlaylistSummary | None = None

        layout = QVBoxLayout(self)

        if not playlists:
            layout.addWidget(QLabel("No playlists were found in your Spotify account."))
        else:
            layout.addWidget(QLabel(f"{len(playlists)} playlists found:"))

        self.list_widget = QListWidget()
        for playlist in playlists:
            label = f"{playlist.name}  —  {playlist.track_count} tracks"
            if playlist.owner:
                label += f"  (by {playlist.owner})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, playlist)
            self.list_widget.addItem(item)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._accept_selection())
        layout.addWidget(self.list_widget, stretch=1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        choose_btn = QPushButton("Choose")
        choose_btn.setObjectName("downloadButton")
        choose_btn.clicked.connect(self._accept_selection)
        button_row.addWidget(choose_btn)
        layout.addLayout(button_row)

    def _accept_selection(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        self.selected_playlist = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
