from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    BOT_TOKEN: str
    DATABASE_URL: str
    MANAGER_IDS: str = ""  # comma-separated: "123,456"

    @property
    def manager_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.MANAGER_IDS.split(",") if x.strip()}


settings = Settings()
