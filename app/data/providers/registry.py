from __future__ import annotations

from app.data.providers.alpaca import AlpacaProvider
from app.data.providers.base import Provider
from app.data.providers.binance import BinanceProvider
from app.data.providers.twelvedata import TwelveDataProvider

_providers: dict[str, Provider] = {}


def get_provider(name: str) -> Provider:
    if name not in _providers:
        cls = {"binance": BinanceProvider, "twelvedata": TwelveDataProvider, "alpaca": AlpacaProvider}.get(name)
        if cls is None:
            raise KeyError(f"unknown provider {name}")
        _providers[name] = cls()
    return _providers[name]


def set_provider(name: str, provider: Provider) -> None:
    """Used by tests to inject a provider with a mocked HTTP transport."""
    _providers[name] = provider


def all_provider_names() -> list[str]:
    return ["binance", "twelvedata", "alpaca"]
