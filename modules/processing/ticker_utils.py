"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Ticker symbol utilities
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Handles data quality issues documented in Spec section 7.2:
  - Issue 1: Trailing whitespace in company_static symbols
  - Issue 2: No currency column - inferred from exchange suffix
  - Issue 3: Swiss tickers use .S in DB but Yahoo Finance requires .SW

"""

from ift_global.utils.string_utils import trim_string

# Default currency mapping by exchange suffix
DEFAULT_CURRENCY_MAP = {
    ".L": "GBP",
    ".PA": "EUR",
    ".AS": "EUR",
    ".DE": "EUR",
    ".MC": "EUR",
    ".MI": "EUR",
    ".TO": "CAD",
    ".S": "CHF",
}


def clean_ticker(symbol: str) -> str:
    """Remove leading and trailing whitespace from ticker symbol.

    Addresses Spec section 7.2 Issue 1: equity_static symbols have
    trailing whitespace that must be stripped before use.

    :param symbol: Raw ticker symbol from database
    :type symbol: str
    :return: Cleaned ticker symbol
    :rtype: str
    """
    if not symbol:
        return symbol
    return trim_string(symbol, what="trailing").strip()


def get_exchange_suffix(symbol: str) -> str:
    """Extract the exchange suffix from a ticker symbol.

    :param symbol: Ticker symbol (e.g. 'VOD.L', 'AAPL')
    :type symbol: str
    :return: Exchange suffix including dot (e.g. '.L') or empty string for US
    :rtype: str

    :example:
        >>> get_exchange_suffix('VOD.L')
        '.L'
        >>> get_exchange_suffix('AAPL')
        ''
    """
    if not symbol or "." not in symbol:
        return ""
    last_dot = symbol.rfind(".")
    return symbol[last_dot:]


def infer_currency(symbol: str, currency_map: dict = None) -> str:
    """Infer the trading currency from the exchange suffix.

    Addresses Spec section 7.2 Issue 2: no currency column exists
    in company_static so currency must be derived from the ticker suffix.

    :param symbol: Ticker symbol
    :type symbol: str
    :param currency_map: Optional override for suffix-to-currency mapping
    :type currency_map: dict or None
    :return: 3-letter ISO currency code
    :rtype: str

    :example:
        >>> infer_currency('VOD.L')
        'GBP'
        >>> infer_currency('AAPL')
        'USD'
    """
    mapping = currency_map or DEFAULT_CURRENCY_MAP
    suffix = get_exchange_suffix(symbol)
    return mapping.get(suffix, "USD")


def remap_swiss_ticker(symbol: str) -> str:
    """Remap Swiss .S suffix to .SW for Yahoo Finance compatibility.

    Addresses Spec section 7.2 Issue 3: Swiss tickers are stored as
    .S in the database but Yahoo Finance requires .SW format.

    :param symbol: Ticker symbol
    :type symbol: str
    :return: Remapped ticker for Yahoo Finance
    :rtype: str

    :example:
        >>> remap_swiss_ticker('NOVN.S')
        'NOVN.SW'
        >>> remap_swiss_ticker('AAPL')
        'AAPL'
    """
    if symbol and symbol.endswith(".S"):
        return symbol[:-2] + ".SW"
    return symbol


def remap_share_class_ticker(symbol: str) -> str:
    """Remap share-class dot notation to hyphen for Yahoo Finance.

    Yahoo Finance uses hyphens for share class distinction (e.g. BRK-B)
    while company_static uses dots (e.g. BRK.B).

    Only applies when the suffix is NOT a recognized exchange suffix.

    :param symbol: Ticker symbol
    :type symbol: str
    :return: Remapped ticker for Yahoo Finance
    :rtype: str

    :example:
        >>> remap_share_class_ticker('BRK.B')
        'BRK-B'
        >>> remap_share_class_ticker('VOD.L')
        'VOD.L'
    """
    if not symbol or "." not in symbol:
        return symbol
    suffix = get_exchange_suffix(symbol)
    known_suffixes = set(DEFAULT_CURRENCY_MAP.keys()) | {".SW"}
    if suffix and suffix not in known_suffixes:
        last_dot = symbol.rfind(".")
        return symbol[:last_dot] + "-" + symbol[last_dot + 1 :]
    return symbol


def prepare_yfinance_ticker(raw_symbol: str, currency_map: dict = None) -> tuple:
    """Full pipeline to prepare a ticker for Yahoo Finance download.

    Applies: clean -> infer currency -> remap Swiss -> remap share class.
    Returns the database symbol, Yahoo Finance ticker, and inferred currency.

    :param raw_symbol: Raw symbol from equity_static
    :type raw_symbol: str
    :param currency_map: Optional currency mapping override
    :type currency_map: dict or None
    :return: Tuple of (db_symbol, yf_ticker, currency)
    :rtype: tuple[str, str, str]

    :example:
        >>> prepare_yfinance_ticker('NOVN.S      ')
        ('NOVN.S', 'NOVN.SW', 'CHF')
        >>> prepare_yfinance_ticker('AAPL  ')
        ('AAPL', 'AAPL', 'USD')
        >>> prepare_yfinance_ticker('BRK.B')
        ('BRK.B', 'BRK-B', 'USD')
    """
    db_symbol = clean_ticker(raw_symbol)
    currency = infer_currency(db_symbol, currency_map)
    yf_ticker = remap_swiss_ticker(db_symbol)
    yf_ticker = remap_share_class_ticker(yf_ticker)
    return db_symbol, yf_ticker, currency
