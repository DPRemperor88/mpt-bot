"""Пакет handlers: роутеры пользователей, групп и админ-панели."""
from .user import user_router
from .group import group_router
from .admin import admin_router

__all__ = ["user_router", "group_router", "admin_router"]
