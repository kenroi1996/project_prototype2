import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from ui.pages.login_dialog import LoginDialog

ROOT = Path(__file__).resolve().parent
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
    # Load system config so all pages read live values from the DB
    SystemConfig.load(dialog.db_conn)

    # _launch_for_role handles post-login checks (force pw change + security setup)
    # for ALL login paths (initial login and re-login after sign out)
    from ui.counselor_window import _launch_for_role
    _launch_for_role(dialog.db_conn)
    sys.exit(app.exec())