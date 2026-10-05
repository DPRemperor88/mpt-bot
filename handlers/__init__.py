"""Пакет handlers: роутеры пользователей и админ-панели."""
from .user import user_router
from .admin import admin_router

__all__ = ["user_router", "admin_router"]
