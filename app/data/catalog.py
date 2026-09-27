"""Default market catalogue. Admins can add/disable markets; symbols follow each provider's format."""
from __future__ import annotations

DEFAULT_ASSETS: list[dict] = [
    # crypto - Binance (public data, no key)
    {"symbol": "BTC/USDT", "name": "Bitcoin", "asset_class": "crypto", "provider": "binance", "provider_symbol": "BTCUSDT", "calendar": "24x7", "price_precision": 2},
    {"symbol": "ETH/USDT", "name": "Ethereum", "asset_class": "crypto", "provider": "binance", "provider_symbol": "ETHUSDT", "calendar": "24x7", "price_precision": 2},
    {"symbol": "SOL/USDT", "name": "Solana", "asset_class": "crypto", "provider": "binance", "provider_symbol": "SOLUSDT", "calendar": "24x7", "price_precision": 3},
    {"symbol": "BNB/USDT", "name": "BNB", "asset_class": "crypto", "provider": "binance", "provider_symbol": "BNBUSDT", "calendar": "24x7", "price_precision": 2},
    {"symbol": "XRP/USDT", "name": "XRP", "asset_class": "crypto", "provider": "binance", "provider_symbol": "XRPUSDT", "calendar": "24x7", "price_precision": 4},
    # forex - Twelve Data
    {"symbol": "EUR/USD", "name": "Euro / US Dollar", "asset_class": "forex", "provider": "twelvedata", "provider_symbol": "EUR/USD", "calendar": "fx", "price_precision": 5},
    {"symbol": "GBP/USD", "name": "British Pound / US Dollar", "asset_class": "forex", "provider": "twelvedata", "provider_symbol": "GBP/USD", "calendar": "fx", "price_precision": 5},
    {"symbol": "USD/JPY", "name": "US Dollar / Japanese Yen", "asset_class": "forex", "provider": "twelvedata", "provider_symbol": "USD/JPY", "calendar": "fx", "price_precision": 3},
    # commodities - Twelve Data (spot metals)
    {"symbol": "XAU/USD", "name": "Gold spot", "asset_class": "commodity", "provider": "twelvedata", "provider_symbol": "XAU/USD", "calendar": "fx", "price_precision": 2},
    {"symbol": "XAG/USD", "name": "Silver spot", "asset_class": "commodity", "provider": "twelvedata", "provider_symbol": "XAG/USD", "calendar": "fx", "price_precision": 3},
    # indices - Twelve Data (index availability depends on your Twelve Data plan)
    {"symbol": "SPX", "name": "S&P 500 Index", "asset_class": "index", "provider": "twelvedata", "provider_symbol": "SPX", "calendar": "equity", "price_precision": 2},
    {"symbol": "NDX", "name": "Nasdaq-100 Index", "asset_class": "index", "provider": "twelvedata", "provider_symbol": "NDX", "calendar": "equity", "price_precision": 2},
    # stocks / ETFs - Alpaca
    {"symbol": "AAPL", "name": "Apple Inc.", "asset_class": "stock", "provider": "alpaca", "provider_symbol": "AAPL", "calendar": "equity", "price_precision": 2, "meta": {"volume_note": "IEX-only volume on the free feed"}},
    {"symbol": "MSFT", "name": "Microsoft Corp.", "asset_class": "stock", "provider": "alpaca", "provider_symbol": "MSFT", "calendar": "equity", "price_precision": 2, "meta": {"volume_note": "IEX-only volume on the free feed"}},
    {"symbol": "NVDA", "name": "NVIDIA Corp.", "asset_class": "stock", "provider": "alpaca", "provider_symbol": "NVDA", "calendar": "equity", "price_precision": 2, "meta": {"volume_note": "IEX-only volume on the free feed"}},
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF", "asset_class": "stock", "provider": "alpaca", "provider_symbol": "SPY", "calendar": "equity", "price_precision": 2, "meta": {"volume_note": "IEX-only volume on the free feed"}},
]
