from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "企业制度问答系统"
    database_url: str = "sqlite:///./storage/app.db"
    auth_token_secret: SecretStr = SecretStr("")

    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_timeout_seconds: float = Field(default=120, gt=0)
    deepseek_max_tokens: int = Field(default=4096, ge=1)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
