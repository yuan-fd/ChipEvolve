"""Negative fixture for G1: the kernel knows a concrete plugin id."""
SPECIAL = {"orfs-agent", "a2-orfo"}

def dispatch(plugin_id):
    if plugin_id in SPECIAL:
        return "special-cased"
    return "generic"
