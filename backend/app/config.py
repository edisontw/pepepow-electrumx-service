from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="pepew-light", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8088, alias="APP_PORT")
    app_public_base_url: str = Field(default="http://127.0.0.1:8088", alias="APP_PUBLIC_BASE_URL")

    electrumx_host: str = Field(default="127.0.0.1", alias="ELECTRUMX_HOST")
    electrumx_port: int = Field(default=50001, alias="ELECTRUMX_PORT")
    electrumx_use_ssl: bool = Field(default=False, alias="ELECTRUMX_USE_SSL")
    electrumx_timeout: float = Field(default=5.0, alias="ELECTRUMX_TIMEOUT")

    pepew_decimals: int = Field(default=8, alias="PEPEW_DECIMALS")
    pepew_address_prefix: str = Field(default="P", alias="PEPEW_ADDRESS_PREFIX")
    pepew_min_confirmations: int = Field(default=3, alias="PEPEW_MIN_CONFIRMATIONS")
    pepew_explorer_base_url: str = Field(default="https://explorer.pepepow.net", alias="PEPEW_EXPLORER_BASE_URL")

    cache_status_seconds: int = Field(default=10, alias="CACHE_STATUS_SECONDS")
    cache_balance_seconds: int = Field(default=15, alias="CACHE_BALANCE_SECONDS")
    cache_history_seconds: int = Field(default=30, alias="CACHE_HISTORY_SECONDS")
    cache_tx_seconds: int = Field(default=300, alias="CACHE_TX_SECONDS")
    cache_price_seconds: int = Field(default=120, alias="CACHE_PRICE_SECONDS")
    cache_price_stale_seconds: int = Field(default=900, alias="CACHE_PRICE_STALE_SECONDS")
    price_fetch_timeout_seconds: float = Field(default=5.0, alias="PRICE_FETCH_TIMEOUT_SECONDS")
    nonkyc_ticker_url: str = Field(
        default="https://api.nonkyc.io/api/v2/ticker/PEPEW_USDT",
        alias="NONKYC_TICKER_URL",
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    version: str = "0.1.0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
