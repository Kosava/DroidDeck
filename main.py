#!/usr/bin/env python3
"""DroidDeck — lagani pokretač Android aplikacija preko Waydroid-a."""

import os
import sys
import traceback

from PyQt6.QtCore import QLockFile, QSettings, QStandardPaths
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMessageBox

from ui.icon import app_icon
from ui.main_window import K_DARK, MainWindow
from ui.theme import qss_for

_in_hook = False
SERVER_NAME = f"droiddeck-{os.getuid()}"


def _excepthook(exc_type, value, tb):
    """
    PyQt6 bez ovoga GASI aplikaciju na svaki neuhvaćen izuzetak u slotu.
    Sa njim: greška ide u terminal + poruka, a DroidDeck radi dalje.
    """
    global _in_hook
    traceback.print_exception(exc_type, value, tb)
    if _in_hook or QApplication.instance() is None:
        return
    _in_hook = True
    try:
        QMessageBox.critical(None, "DroidDeck",
                             f"Neočekivana greška:\n\n{exc_type.__name__}: {value}\n\n"
                             "Detalji su ispisani u terminalu (pokreni: droiddeck).")
    finally:
        _in_hook = False


def single_instance_lock() -> QLockFile | None:
    """Samo jedan DroidDeck: dve kopije bi se otimale oko Androida i tray-a."""
    folder = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.RuntimeLocation) or "/tmp"
    lock = QLockFile(os.path.join(folder, "droiddeck.lock"))
    lock.setStaleLockTime(0)        # bez isteka po vremenu; lock mrtvog procesa se čisti po PID-u
    return lock if lock.tryLock(100) else None


def activate_running_instance() -> bool:
    """Javi DroidDeck-u koji već radi da prikaže prozor. True = uspelo."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(1000):
        return False
    sock.write(b"show\n")
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def start_activation_server(win: MainWindow) -> QLocalServer:
    QLocalServer.removeServer(SERVER_NAME)        # zaostao socket posle pada
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)

    def on_connection():
        conn = server.nextPendingConnection()
        if conn is None:
            return
        conn.readyRead.connect(lambda: (conn.readAll(), win.show_window()))
        conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(on_connection)
    server.listen(SERVER_NAME)
    return server


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("DroidDeck")
    app.setOrganizationName("DroidDeck")      # QSettings -> ~/.config/DroidDeck/
    app.setDesktopFileName("droiddeck")
    app.setWindowIcon(app_icon())
    sys.excepthook = _excepthook

    lock = single_instance_lock()
    if lock is None:
        if activate_running_instance():
            return 0                          # prozor prve kopije je prikazan
        QMessageBox.information(
            None, "DroidDeck",
            "DroidDeck je već pokrenut.\n\nPotraži ikonicu u tray-u (dole desno).",
        )
        return 0

    dark = QSettings().value(K_DARK, True, type=bool)
    app.setStyleSheet(qss_for(dark))

    # sa tray-om aplikacija živi i kad se prozor zatvori; Izlaz je u meniju
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    if win.tray is None:
        app.setQuitOnLastWindowClosed(True)   # nema tray-a: zatvaranje = izlaz
    server = start_activation_server(win)     # noqa: F841 (mora da živi)
    win.show()
    code = app.exec()
    server.close()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
