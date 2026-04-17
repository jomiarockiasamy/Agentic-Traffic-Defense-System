import os

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))

from .runtime import client_ip_for_defense, defender

__all__ = ["PACKAGE_DIR", "client_ip_for_defense", "defender"]

