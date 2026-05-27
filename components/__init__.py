__all__ = ["theme", "state", "cards", "charts", "market_data"]


def __getattr__(name: str):
    if name in __all__:
        from importlib import import_module
        return import_module(f"components.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
