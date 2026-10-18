"""PySide6 / PyQt6 兼容层：优先 PySide6，找不到再用 PyQt6。"""
try:
    from PySide6 import QtCore, QtGui, QtWidgets, QtSvg  # noqa: F401
    from PySide6.QtCore import Signal, Property  # noqa: F401
    BINDING = "PySide6"
except ImportError:  # pragma: no cover
    from PyQt6 import QtCore, QtGui, QtWidgets, QtSvg  # noqa: F401
    from PyQt6.QtCore import pyqtSignal as Signal, pyqtProperty as Property  # noqa: F401
    BINDING = "PyQt6"

Qt = QtCore.Qt
