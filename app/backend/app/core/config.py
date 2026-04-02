from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
  app_name: str
  backend_root: Path
  data_dir: Path
  public_dir: Path
  qrcode_dir: Path
  database_path: Path
  cors_origins: tuple[str, ...]
  playwright_install_dir: Path
  request_timeout_seconds: float
  user_agent: str
  search_debug_enabled: bool


def _env_flag(name: str, default: bool = False) -> bool:
  value = os.getenv(name)

  if value is None:
    return default

  return value.strip().lower() in {'1', 'true', 'yes', 'on'}


@lru_cache
def get_settings() -> Settings:
  backend_root = Path(__file__).resolve().parents[2]
  data_dir = backend_root / 'data'
  public_dir = data_dir / 'public'
  qrcode_dir = public_dir / 'qrcodes'
  database_path = data_dir / 'ai-group-discovery.sqlite3'
  playwright_install_dir = Path.home() / 'AppData' / 'Local' / 'ms-playwright'

  return Settings(
    app_name='AI群聊发现器 Local API',
    backend_root=backend_root,
    data_dir=data_dir,
    public_dir=public_dir,
    qrcode_dir=qrcode_dir,
    database_path=database_path,
    cors_origins=('http://127.0.0.1:5173', 'http://localhost:5173'),
    playwright_install_dir=playwright_install_dir,
    request_timeout_seconds=20.0,
    user_agent=(
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
      'AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/124.0.0.0 Safari/537.36'
    ),
    search_debug_enabled=_env_flag('AI_GROUP_SEARCH_DEBUG', False),
  )
