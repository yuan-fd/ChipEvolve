"""Negative fixture for G11: every defensive anti-pattern at once."""
import sys

sys.path.insert(0, "/somewhere/else")

def load(use_legacy=True):
    try:
        return _legacy_loader()
    except Exception:
        pass

def _legacy_loader():
    return None
