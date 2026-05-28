# -*- coding: utf-8 -*-
"""ParcelFlux — block-to-parcel subdivision plugin.

Dock-style plugin: toolbar + menu + togglable dock widget.
"""
from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMenu, QToolBar, QMessageBox


class ParcelFluxPlugin:
    MENU_NAME = "&ParcelFlux"
    TOOLBAR_NAME = "ParcelFlux Toolbar"

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.icon_dir = os.path.join(self.plugin_dir, "icons")

        self.actions: list[QAction] = []
        self.menu: QMenu | None = None
        self.toolbar: QToolBar | None = None
        self._dock = None

    # -- QGIS lifecycle --

    def initGui(self) -> None:
        self.menu = QMenu(self.MENU_NAME)
        self.iface.mainWindow().menuBar().addMenu(self.menu)

        self.toolbar = QToolBar(self.TOOLBAR_NAME)
        self.toolbar.setObjectName("ParcelFluxToolbar")
        self.iface.addToolBar(self.toolbar)

        self.panel_action = self._add_action(
            os.path.join(self.icon_dir, "parcelflux.png"),
            "Toggle ParcelFlux panel",
            self._toggle_dock,
            checkable=True,
            status_tip="Show/hide the ParcelFlux dock panel",
        )

        self.menu.addSeparator()
        self._add_action(
            ":/images/themes/default/mActionHelpContents.svg",
            "About",
            self._show_about,
            add_to_toolbar=False,
        )
        self._add_action(
            ":/images/themes/default/mActionHelpContents.svg",
            "Documentation",
            self._show_docs,
            add_to_toolbar=False,
        )

    def unload(self) -> None:
        if self._dock:
            self.iface.removeDockWidget(self._dock)
            self._dock.deleteLater()
            self._dock = None
        for action in self.actions:
            self.iface.removePluginMenu(self.MENU_NAME, action)
        if self.toolbar:
            del self.toolbar
        if self.menu:
            self.menu.deleteLater()

    # -- Dock widget --

    def _toggle_dock(self) -> None:
        if self._dock is None:
            try:
                from .dialogs.dock import ParcelFluxDock

                self._dock = ParcelFluxDock(self.iface, self.icon_dir, self.iface.mainWindow())
                self._dock.setObjectName("ParcelFluxDock")
                self._dock.visibilityChanged.connect(self.panel_action.setChecked)
                self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._dock)
            except Exception as exc:
                QMessageBox.critical(self.iface.mainWindow(), "Error", f"Could not create panel:\n{exc}")
                self.panel_action.setChecked(False)
                return
        self._dock.setVisible(not self._dock.isVisible())
        if self._dock.isVisible():
            self._dock.raise_()

    # -- Helpers --

    def _add_action(self, icon_path, text, callback, *, add_to_toolbar=True,
                    add_to_menu=True, checkable=False, status_tip=None) -> QAction:
        action = QAction(QIcon(icon_path), text, self.iface.mainWindow())
        action.triggered.connect(callback)
        action.setCheckable(checkable)
        if status_tip:
            action.setStatusTip(status_tip)
        if add_to_toolbar and self.toolbar:
            self.toolbar.addAction(action)
        if add_to_menu and self.menu:
            self.menu.addAction(action)
        self.actions.append(action)
        return action

    def _show_about(self) -> None:
        QMessageBox.about(
            self.iface.mainWindow(),
            "ParcelFlux",
            "<h3>ParcelFlux</h3>"
            "<p>Block-to-parcel subdivision engine with configurable width, "
            "row asymmetry, fishbone offset, and 3-pass residual merging.</p>"
            "<p>Developer: Yusuf Eminoglu<br>"
            "Dokuz Eylul University, Department of City and Regional Planning</p>"
            "<p><a href='https://github.com/YusufEminoglu/parcelflux'>"
            "github.com/YusufEminoglu/parcelflux</a></p>",
        )

    def _show_docs(self) -> None:
        import webbrowser
        docs_path = os.path.join(self.plugin_dir, "docs", "guide.html")
        webbrowser.open(f"file:///{docs_path.replace(os.sep, '/')}")
