"""
Mreža aplikacija — Faza 2 (+ desni klik meni iz Faze 5).

Čita .desktop fajlove koje Waydroid pravi i prikazuje ih kao ikonice.
QFileSystemWatcher osvežava listu čim se nešto instalira ili obriše;
pozicija skrola i izabrana ikonica ostaju gde su bile.
"""

import os

from PyQt6.QtCore import QFileSystemWatcher, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListView, QMenu,
    QListWidget, QListWidgetItem, QStackedLayout, QVBoxLayout, QWidget,
)

import waydroid_api as wd

ICON_SIZE = 64
CELL = QSize(110, 110)
PACKAGE_ROLE = Qt.ItemDataRole.UserRole


class AppGrid(QWidget):
    launch_requested = pyqtSignal(str, str)     # paket, ime
    uninstall_requested = pyqtSignal(str, str)  # paket, ime
    stop_requested = pyqtSignal(str, str)       # paket, ime
    reloaded = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.can_stop = lambda: True                # glavni prozor zna da li Android radi
        self._apps: list[wd.AppInfo] = []
        self._icon_cache: dict[tuple, QIcon] = {}
        self._fallback = self._make_fallback_icon()

        self._build_ui()

        # osluškuj folder; mali debounce jer Waydroid piše više fajlova odjednom
        self._debounce = QTimer(self, singleShot=True, interval=400,
                                timeout=self.reload)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(lambda _: self._debounce.start())
        if wd.APPS_DIR.is_dir():
            self._watcher.addPath(str(wd.APPS_DIR))

        self.reload()

    # ------------------------------------------------------------ UI

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        tools = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Pretraga…", clearButtonEnabled=True)
        self.search.textChanged.connect(self._apply_filter)
        self.show_system = QCheckBox("Sistemske")
        self.show_system.toggled.connect(self._apply_filter)
        self.count_label = QLabel(objectName="muted")
        tools.addWidget(self.search, 1)
        tools.addWidget(self.show_system)
        tools.addWidget(self.count_label)
        layout.addLayout(tools)

        self.list = QListWidget(objectName="grid")
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.list.setGridSize(CELL)
        self.list.setWordWrap(True)
        self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.setSpacing(6)
        self.list.itemActivated.connect(self._on_activated)   # dupli klik / Enter
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)

        self.empty = QLabel(
            "Još nema aplikacija.\n\nPokreni Android i instaliraj nešto iz Play Store-a.",
            objectName="placeholder",
            alignment=Qt.AlignmentFlag.AlignCenter,
        )

        self._stack = QStackedLayout()
        self._stack.addWidget(self.list)
        self._stack.addWidget(self.empty)
        layout.addLayout(self._stack, 1)

    # ------------------------------------------------------------ podaci

    def reload(self):
        # folder možda nije postojao pri startu
        if wd.APPS_DIR.is_dir() and str(wd.APPS_DIR) not in self._watcher.directories():
            self._watcher.addPath(str(wd.APPS_DIR))

        # zapamti gde je korisnik bio
        scroll = self.list.verticalScrollBar().value()
        current = self.list.currentItem()
        selected = current.data(PACKAGE_ROLE) if current else None

        self._apps = wd.list_apps()
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        restore = None
        for app in self._apps:
            item = QListWidgetItem(self._icon_for(app), app.name)
            item.setData(PACKAGE_ROLE, app.package)
            item.setToolTip(f"{app.name}\n{app.package}")
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self.list.addItem(item)
            if app.package == selected:
                restore = item
        self._apply_filter()
        if restore is not None and not restore.isHidden():
            self.list.setCurrentItem(restore)
        self.list.setUpdatesEnabled(True)
        # raspored ikonica se računa tek u sledećem ciklusu
        QTimer.singleShot(0, lambda: self.list.verticalScrollBar().setValue(scroll))
        self.reloaded.emit()

    def _icon_for(self, app: wd.AppInfo) -> QIcon:
        # ključ uključuje vreme izmene: ažurirana aplikacija = nova ikonica
        try:
            key = (app.icon, os.path.getmtime(app.icon)) if app.icon else ("", 0)
        except OSError:
            key = (app.icon, 0)
        if key in self._icon_cache:
            return self._icon_cache[key]
        pix = QPixmap(app.icon) if app.icon else QPixmap()
        icon = QIcon(pix) if not pix.isNull() else self._fallback
        self._icon_cache[key] = icon
        return icon

    @staticmethod
    def _make_fallback_icon() -> QIcon:
        icon = QIcon.fromTheme("application-x-executable")
        return icon if not icon.isNull() else QIcon()

    def _apply_filter(self):
        needle = self.search.text().strip().lower()
        show_sys = self.show_system.isChecked()
        visible = 0

        for i, app in enumerate(self._apps):
            match = (show_sys or not app.system) and (
                not needle or needle in app.name.lower() or needle in app.package.lower()
            )
            self.list.item(i).setHidden(not match)
            visible += match

        self.count_label.setText(f"{visible}")
        self._stack.setCurrentWidget(self.list if visible else self.empty)

    # ------------------------------------------------------------ akcije

    def _on_activated(self, item: QListWidgetItem):
        self.launch_requested.emit(item.data(PACKAGE_ROLE), item.text())

    def _context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        package, name = item.data(PACKAGE_ROLE), item.text()
        is_system = package in wd.SYSTEM_PACKAGES or package == wd.PLAY_STORE_PKG

        menu = QMenu(self)
        menu.addAction("▶  Pokreni", lambda: self.launch_requested.emit(package, name))
        stop = menu.addAction("⏹  Zaustavi aplikaciju",
                              lambda: self.stop_requested.emit(package, name))
        stop.setEnabled(self.can_stop())
        stop.setToolTip("Potpuno gasi aplikaciju; sledeće pokretanje kreće ispočetka")
        menu.setToolTipsVisible(True)
        menu.addSeparator()
        menu.addAction("Kopiraj naziv paketa",
                       lambda: QGuiApplication.clipboard().setText(package))
        menu.addSeparator()
        remove = menu.addAction("🗑  Deinstaliraj…",
                                lambda: self.uninstall_requested.emit(package, name))
        remove.setEnabled(not is_system)          # sistemske ne diramo
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def icon_for_package(self, package: str) -> QIcon:
        for app in self._apps:
            if app.package == package:
                return self._icon_for(app)
        return self._fallback

    def name_for_package(self, package: str) -> str | None:
        for app in self._apps:
            if app.package == package:
                return app.name
        return None
