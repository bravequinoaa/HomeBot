"""
StorageFactory and StorageManager.

StorageFactory is the ONLY place where storage instances are created.
It maintains a registry of (class, kwargs) pairs keyed by name and passes
the _FACTORY_TOKEN sentinel into each constructor so that direct
construction of JsonFileStorage subclasses is blocked at runtime.

StorageManager wraps the factory and provides lazy, cached access to
instances by name.  The same instance is returned on every subsequent
call to get(), making each named storage behave like a singleton within
a given manager.

Typical setup in main.py:

    factory = StorageFactory()
    factory.register("bills",    BillsStorage,    path=..., collection_key="bills")
    factory.register("calendar", CalendarStorage, path=..., collection_key="events")
    manager = StorageManager(factory)
    bot.storage_manager = manager

Cogs then access their store via:

    self.storage = bot.storage_manager.get("bills")
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseStorage
from .json_storage import JsonFileStorage

log = logging.getLogger("homebot.storage")


class StorageFactory:
    """Registers storage types and creates instances via the factory token."""

    def __init__(self) -> None:
        self._registry: dict[str, tuple[type, dict[str, Any]]] = {}
        log.debug("[%s] StorageFactory.__init__ addr=0x%x", hex(id(self)), id(self))

    def register(self, name: str, cls: type, **kwargs: Any) -> None:
        """
        Register a storage class under *name*.

        Raises ValueError if *name* is already registered.
        """
        if name in self._registry:
            raise ValueError(
                f"Storage name {name!r} is already registered.  "
                "Use a unique name for each storage instance."
            )
        self._registry[name] = (cls, kwargs)
        log.debug(
            "[%s] StorageFactory.register name=%r cls=%s kwargs_keys=%s",
            hex(id(self)), name, cls.__name__, list(kwargs.keys()),
        )

    def create(self, name: str) -> BaseStorage:
        """
        Instantiate and return the storage registered under *name*.

        Raises ValueError if *name* has not been registered.
        The _FACTORY_TOKEN sentinel is injected automatically so that
        subclasses of JsonFileStorage can enforce factory-only construction.
        """
        if name not in self._registry:
            raise ValueError(
                f"No storage registered under name {name!r}.  "
                f"Registered names: {list(self._registry)}"
            )
        cls, kwargs = self._registry[name]
        instance = cls(JsonFileStorage._FACTORY_TOKEN, **kwargs)
        log.debug(
            "[%s] StorageFactory.create name=%r → %s addr=0x%x",
            hex(id(self)), name, type(instance).__name__, id(instance),
        )
        return instance


class StorageManager:
    """
    Manages a collection of named storage instances.

    Instances are created lazily on first access and cached thereafter,
    so each name maps to exactly one object for the lifetime of the manager.
    The factory is the only route to adding new instances.
    """

    def __init__(self, factory: StorageFactory) -> None:
        self._factory = factory
        self._instances: dict[str, BaseStorage] = {}
        log.debug(
            "[%s] StorageManager.__init__ factory_addr=0x%x",
            hex(id(self)), id(factory),
        )

    @property
    def factory(self) -> StorageFactory:
        """Read-only access to the underlying factory."""
        return self._factory

    def get(self, name: str) -> BaseStorage:
        """
        Return the storage instance for *name*, creating it if needed.

        Repeated calls with the same name return the identical object
        (same memory address).
        """
        if name not in self._instances:
            self._instances[name] = self._factory.create(name)
            log.debug(
                "[%s] StorageManager.get name=%r → created new instance 0x%x",
                hex(id(self)), name, id(self._instances[name]),
            )
        else:
            log.debug(
                "[%s] StorageManager.get name=%r → cached instance 0x%x",
                hex(id(self)), name, id(self._instances[name]),
            )
        return self._instances[name]

    def get_all(self) -> dict[str, BaseStorage]:
        """Return a snapshot dict of all currently instantiated storages."""
        return dict(self._instances)
