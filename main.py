import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from ui.dashboard_window_new import DashboardWindow

ROOT = Path(__file__).resolve().parent
STYLES_PATH = ROOT / "assets" / "styles" / "theme.qss"


def load_stylesheet(app: QApplication) -> None:
    if STYLES_PATH.is_file():
        app.setStyleSheet(STYLES_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    load_stylesheet(app)

    window = DashboardWindow()
    window.show()
    sys.exit(app.exec())