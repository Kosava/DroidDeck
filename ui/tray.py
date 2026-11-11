"""
Tray ikonica — Faza 5.

Levi klik: prikaži/sakrij prozor.
Meni: poslednje pokretane igre, Pokreni/Ugasi Android, Play Store, Izlaz.
Tačka na ikonici pokazuje status (zelena = radi, crvena = ugašen, žuta = radi nešto).
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from ui import theme
from ui.icon import app_icon


class Tray(QSystemTrayIcon):
    toggle_window = pyqtSignal()
    toggle_power = pyqtSignal()
    open_store = pyqtSignal()
    launch = pyqtSignal(str, str)        # paket, ime
    quit_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._icons = {
            "on": app_icon(theme.GREEN),
            "off": app_icon(theme.RED),
            "busy": app_icon(theme.YELLOW),
        }
        self._state = None
        self.set_state("off", "Android je ugašen")

        self.menu = QMenu()
        self.show_action = self.menu.addAction("Prikaži DroidDeck", self.toggle_window.emit)
        self.menu.addSeparator()

        self.recent_menu = self.menu.addMenu("Poslednje pokretano")
        self.menu.addSeparator()

        self.power_action = self.menu.addAction("Pokreni Android", self.toggle_power.emit)
        self.store_action = self.menu.addAction("Play Store", self.open_store.emit)
        self.menu.addSeparator()
        self.menu.addAction("Izlaz", self.quit_requested.emit)

        self.setContextMenu(self.menu)
        self.activated.connect(self._on_activated)
        self.set_recent([])

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:   # levi klik
            self.toggle_window.emit()

    # ------------------------------------------------------------ osvežavanje

    def set_state(self, state: str, tooltip: str):
        if state != self._state:
            self.setIcon(self._icons[state])
            self._state = state
        self.setToolTip(f"DroidDeck — {tooltip}")

    def set_power(self, running: bool, enabled: bool):
        self.power_action.setText("Ugasi Android" if running else "Pokreni Android")
        self.power_action.setEnabled(enabled)

    def set_window_visible(self, visible: bool):
        self.show_action.setText("Sakrij DroidDeck" if visible else "Prikaži DroidDeck")

    def set_recent(self, items: list[tuple[str, str, object]]):
        """items: (paket, ime, QIcon)"""
        self.recent_menu.clear()
        if not items:
            empty = QAction("(ništa još)", self.recent_menu)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for package, name, icon in items:
            act = self.recent_menu.addAction(icon, name)
            act.triggered.connect(lambda _=False, p=package, n=name: self.launch.emit(p, n))
