from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://polymarket:polymarket@localhost:5432/polymarket_bot"

    # Anthropic
    anthropic_api_key: str = ""

    # Detection thresholds
    min_trade_usd: float = 10_000
    min_score_to_trade: int = 5
    max_ai_calls_per_day: int = 50

    # Paper trading
    initial_balance: float = 10_000
    max_position_pct: float = 20
    max_positions: int = 10
    stop_loss_pct_high: float = 30  # for scores >= 8
    stop_loss_pct_low: float = 50  # for scores < 8
    take_profit_pct: float = 80
    trailing_stop_trigger_pct: float = 40
    circuit_breaker_pct: float = 5
    category_concentration_max_pct: float = 40

    # Polymarket endpoints
    clob_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    clob_api_url: str = "https://clob.polymarket.com"
    data_api_url: str = "https://data-api.polymarket.com"

    # Logging
    log_level: str = "INFO"

    model_config = {"env_file": ("../.env", ".env"), "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
