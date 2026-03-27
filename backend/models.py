from pydantic import BaseModel, Field, TypeAdapter, field_validator


class Settings(BaseModel):
    config_path: str | None = None
    excluded_games: list[int] = Field(default_factory=list)
    included_games: list[int] = Field(default_factory=list)
    show_debug: bool = False
    count: int = 10
    auto_sync: bool = True
    run_at_startup: bool = True


class SettingsPatch(BaseModel):
    config_path: str | None = None
    excluded_games: list[int] | None = None
    included_games: list[int] | None = None
    show_debug: bool | None = None
    count: int | None = None
    auto_sync: bool | None = None
    run_at_startup: bool | None = None

    @field_validator("config_path")
    @classmethod
    def _strip_config_path(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("config_path cannot be empty")
        return v

    @field_validator("count")
    @classmethod
    def _clamp_count(cls, v: int | None) -> int | None:
        return max(1, v) if v is not None else None


class SyncLogEntry(BaseModel):
    timestamp: float
    kind: str   # "manual" | "auto"
    success: bool
    message: str
    detail: str = ""


_log_adapter = TypeAdapter(list[SyncLogEntry])
_LOG_MAX = 100
