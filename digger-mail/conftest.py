"""Rend les modules de digger-mail importables par pytest.

pytest ne met pas la racine du projet dans sys.path quand les tests sont dans
un sous-dossier tests/. Ce conftest (à la racine de digger-mail/) est chargé
avant la collecte et ajoute le dossier courant au chemin.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))