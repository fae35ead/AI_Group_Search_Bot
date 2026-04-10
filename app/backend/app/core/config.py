from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import shutil
import sys

from dotenv import load_dotenv

APP_DATA_DIRNAME = 'AIGroupDiscovery'
PLAYWRIGHT_BROWSERS_DIRNAME = 'ms-playwright'


def _is_frozen() -> bool:
  return bool(getattr(sys, 'frozen', False))


def _project_backend_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _bundle_root() -> Path:
  if _is_frozen():
    return Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent))
  return _project_backend_root()


def _executable_dir() -> Path:
  if _is_frozen():
    return Path(sys.executable).resolve().parent
  return _project_backend_root()


def _local_appdata_root() -> Path:
  local_appdata = os.getenv('LOCALAPPDATA')
  if local_appdata:
    return Path(local_appdata)
  return Path.home() / 'AppData' / 'Local'


def _runtime_app_root() -> Path:
  return _local_appdata_root() / APP_DATA_DIRNAME


def _load_environment_files() -> None:
  for candidate in (
    _runtime_app_root() / '.env',
    _executable_dir() / '.env',
    _project_backend_root() / '.env',
  ):
    if candidate.exists():
      load_dotenv(candidate, override=False)


_load_environment_files()


@dataclass(frozen=True)
class Settings:
  app_name: str
  is_frozen: bool
  bundle_root: Path
  executable_dir: Path
  backend_root: Path
  frontend_dist_dir: Path
  frontend_assets_dir: Path
  frontend_index_path: Path
  app_data_root: Path
  data_dir: Path
  public_dir: Path
  qrcode_dir: Path
  viewed_dir: Path
  viewed_qrcode_dir: Path
  viewed_links_csv_path: Path
  database_path: Path
  legacy_data_dirs: tuple[Path, ...]
  cors_origins: tuple[str, ...]
  playwright_install_dir: Path
  request_timeout_seconds: float
  user_agent: str
  github_token: str | None
  search_debug_enabled: bool
  enable_opencv_qr_decode: bool


def _env_flag(name: str, default: bool = False) -> bool:
  value = os.getenv(name)

  if value is None:
    return default

  return value.strip().lower() in {'1', 'true', 'yes', 'on'}


@lru_cache
def get_settings() -> Settings:
  is_frozen = _is_frozen()
  backend_root = _project_backend_root()
  bundle_root = _bundle_root()
  executable_dir = _executable_dir()
  app_data_root = _runtime_app_root()
  data_dir = app_data_root / 'data' if is_frozen else backend_root / 'data'
  public_dir = data_dir / 'public'
  qrcode_dir = public_dir / 'qrcodes'
  viewed_dir = data_dir / 'viewed'
  viewed_qrcode_dir = viewed_dir / 'qrcodes'
  viewed_links_csv_path = viewed_dir / 'viewed_links.csv'
  database_path = data_dir / 'ai-group-discovery.sqlite3'
  frontend_dist_dir = bundle_root / 'frontend_dist' if is_frozen else backend_root.parent / 'frontend' / 'dist'
  frontend_assets_dir = frontend_dist_dir / 'assets'
  frontend_index_path = frontend_dist_dir / 'index.html'

  playwright_install_dir = bundle_root / PLAYWRIGHT_BROWSERS_DIRNAME
  if not playwright_install_dir.exists():
    playwright_install_dir = _local_appdata_root() / PLAYWRIGHT_BROWSERS_DIRNAME
  os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(playwright_install_dir))

  legacy_data_dirs: list[Path] = []
  for candidate in (
    executable_dir / 'data',
    backend_root / 'data',
  ):
    if candidate != data_dir and candidate not in legacy_data_dirs:
      legacy_data_dirs.append(candidate)

  return Settings(
    app_name='AI \u7fa4\u804a\u53d1\u73b0\u5668 Local API',
    is_frozen=is_frozen,
    bundle_root=bundle_root,
    executable_dir=executable_dir,
    backend_root=backend_root,
    frontend_dist_dir=frontend_dist_dir,
    frontend_assets_dir=frontend_assets_dir,
    frontend_index_path=frontend_index_path,
    app_data_root=app_data_root,
    data_dir=data_dir,
    public_dir=public_dir,
    qrcode_dir=qrcode_dir,
    viewed_dir=viewed_dir,
    viewed_qrcode_dir=viewed_qrcode_dir,
    viewed_links_csv_path=viewed_links_csv_path,
    database_path=database_path,
    legacy_data_dirs=tuple(legacy_data_dirs),
    cors_origins=('http://127.0.0.1:5173', 'http://localhost:5173'),
    playwright_install_dir=playwright_install_dir,
    request_timeout_seconds=8.0,
    user_agent=(
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
      'AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/124.0.0.0 Safari/537.36'
    ),
    search_debug_enabled=_env_flag('AI_GROUP_SEARCH_DEBUG', False),
    enable_opencv_qr_decode=_env_flag('AI_GROUP_ENABLE_OPENCV_QR_DECODE', True),
    github_token=os.getenv('GITHUB_TOKEN'),
  )


def migrate_legacy_data_if_needed(settings: Settings) -> None:
  if not settings.is_frozen:
    return

  if settings.database_path.exists():
    return

  if settings.data_dir.exists() and any(settings.data_dir.iterdir()):
    return

  for candidate in settings.legacy_data_dirs:
    legacy_database = candidate / settings.database_path.name
    if not legacy_database.exists():
      continue

    settings.data_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(candidate, settings.data_dir, dirs_exist_ok=True)
    return
