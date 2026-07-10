"""Application entry point."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv

    # Lightweight CLI before Qt
    if len(argv) > 1 and argv[1] in {"list-streams", "--list-streams"}:
        return _cli_list_streams()

    from PySide6.QtWidgets import QApplication

    from gaitlab.ui.context import LabContext
    from gaitlab.ui.main_window import MainWindow

    app = QApplication(argv)
    app.setApplicationName("GaitLab")
    app.setOrganizationName("GaitLab")
    ctx = LabContext()
    win = MainWindow(ctx)
    win.show()
    return app.exec()


def _cli_list_streams() -> int:
    from gaitlab.hub import DataHub
    from gaitlab.lsl.manager import LslManager

    if not LslManager.available():
        print("pylsl is not installed. pip install pylsl")
        return 1
    mgr = LslManager(DataHub())
    streams = mgr.list_streams(timeout=2.0)
    if not streams:
        print("No LSL streams found.")
        return 0
    for s in streams:
        print(f"{s.name!r}  type={s.type!r}  channels={s.channel_count}  Hz={s.nominal_srate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
