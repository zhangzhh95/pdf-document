import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

import main


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = main.GitHubManagerApp()
    window.show()
    QTimer.singleShot(150, lambda: main._cleanup_stray_startup_windows(app, window))
    sys.exit(app.exec())

