"""
Mock stub for open_webui.utils.access_control.
Provides has_permission for use in plugin tests.
"""

from typing import Any, Dict, Optional


def has_permission(
    user_id: str,
    permission_key: str,
    default_permissions: Dict[str, Any] = {},
    db: Optional[Any] = None,
) -> bool:
    """
    Stub implementation: traverses default_permissions dict using dot-separated
    permission_key. Returns True if the final value is truthy, False otherwise.
    Mirrors the logic of the upstream open_webui.utils.access_control.has_permission.
    """

    def get_permission(permissions: Dict[str, Any], keys: list) -> bool:
        for key in keys:
            if key not in permissions:
                return False
            permissions = permissions[key]
        return bool(permissions)

    keys = permission_key.split(".")
    return get_permission(default_permissions, keys)
