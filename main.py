import os
import sys

if sys.platform == "win32":
    try:
        import PyQt6 as _p
        _qt_bin = os.path.join(os.path.dirname(_p.__file__), "Qt6", "bin")
        if os.path.isdir(_qt_bin):
            os.environ["PATH"] = _qt_bin + os.pathsep + os.environ.get("PATH", "")
            os.add_dll_directory(_qt_bin)
            print(f"[main] Qt6 DLL directory registered: {_qt_bin}")
    except Exception as _e:
        print(f"[main] Qt6 PATH fix failed: {_e}")

from pathlib import Path
from PyQt6.QtWidgets import QApplication
from ui.pages.login_dialog import LoginDialog

ROOT        = Path(__file__).resolve().parent
STYLES_PATH = ROOT / "assets" / "styles" / "theme.qss"


def load_stylesheet(app: QApplication) -> None:
    if STYLES_PATH.is_file():
        app.setStyleSheet(STYLES_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    load_stylesheet(app)

    dialog = LoginDialog()
    if dialog.exec() != LoginDialog.DialogCode.Accepted:
        sys.exit(0)

    from services.data_store import DataStore
    from services.system_config import SystemConfig
    DataStore.get().set_db_conn(dialog.db_conn)
    SystemConfig.load(dialog.db_conn)

    from ui.counselor_window import _launch_for_role
    _launch_for_role(dialog.db_conn)
    sys.exit(app.exec())