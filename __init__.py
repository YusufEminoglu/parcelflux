"""QGIS plugin entry point — ParcelFlux dock-style plugin."""
from .main_plugin import ParcelFluxPlugin


def classFactory(iface):
    return ParcelFluxPlugin(iface)
