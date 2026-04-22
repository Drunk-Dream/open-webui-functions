"""
Mock stub for open_webui.utils.access_control.
Provides has_permission for use in plugin tests.
"""

from typing import Any


async def has_permission(
    user_id: str,
    permission_key: str,
    default_permissions: dict[str, Any] | None = None,
    db: Any | None = None,
) -> bool:
    def get_permission(permissions: dict[str, Any], keys: list[str]) -> bool:
        for key in keys:
            if key not in permissions:
                return False
            permissions = permissions[key]
        return bool(permissions)

    _ = (user_id, db)
    keys = permission_key.split(".")
    return get_permission(default_permissions or {}, keys)
