import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_STATEMENTS = (
  '''
  CREATE TABLE IF NOT EXISTS added_groups (
    group_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    group_type TEXT NOT NULL,
    added_at TEXT NOT NULL
  )
  ''',
  '''
  CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    query_type TEXT NOT NULL,
    searched_at TEXT NOT NULL
  )
  ''',
  '''
  CREATE TABLE IF NOT EXISTS crawl_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    raw_html_path TEXT NOT NULL
  )
  ''',
  '''
  CREATE TABLE IF NOT EXISTS search_cache (
    query_key TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
  )
  ''',
  '''
  CREATE TABLE IF NOT EXISTS viewed_groups (
    view_key TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    app_name TEXT NOT NULL,
    platform TEXT NOT NULL,
    group_type TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    entry_url TEXT,
    image_path TEXT,
    fallback_url TEXT,
    viewed_at TEXT NOT NULL,
    is_joined INTEGER NOT NULL DEFAULT 0,
    is_ignored INTEGER NOT NULL DEFAULT 0
  )
  ''',
  '''
  CREATE TABLE IF NOT EXISTS recommendation_pool (
    full_name TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    stars INTEGER NOT NULL,
    description TEXT,
    topics_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
  )
  ''',
  '''
  CREATE TABLE IF NOT EXISTS manual_uploads (
    view_key TEXT PRIMARY KEY,
    app_name TEXT NOT NULL,
    description TEXT,
    created_at TEXT,
    github_stars INTEGER,
    platform TEXT NOT NULL,
    group_type TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    entry_url TEXT,
    image_path TEXT,
    fallback_url TEXT,
    uploaded_at TEXT NOT NULL
  )
  ''',
)

POST_SCHEMA_MIGRATIONS = (
  ('viewed_groups', 'is_joined', 'ALTER TABLE viewed_groups ADD COLUMN is_joined INTEGER NOT NULL DEFAULT 0'),
  ('viewed_groups', 'is_ignored', 'ALTER TABLE viewed_groups ADD COLUMN is_ignored INTEGER NOT NULL DEFAULT 0'),
)


def _has_column(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
  rows = connection.execute(f'PRAGMA table_info({table_name})').fetchall()
  return any(str(row[1]) == column_name for row in rows)


def initialize_database(database_path: Path) -> None:
  database_path.parent.mkdir(parents=True, exist_ok=True)

  with sqlite3.connect(database_path) as connection:
    connection.execute('PRAGMA journal_mode = WAL;')
    connection.execute('PRAGMA foreign_keys = ON;')

    for statement in SCHEMA_STATEMENTS:
      connection.execute(statement)

    for table_name, column_name, statement in POST_SCHEMA_MIGRATIONS:
      if not _has_column(connection, table_name, column_name):
        connection.execute(statement)

    connection.commit()


@contextmanager
def get_connection(database_path: Path) -> Iterator[sqlite3.Connection]:
  connection = sqlite3.connect(database_path)
  connection.row_factory = sqlite3.Row

  try:
    yield connection
  finally:
    connection.close()
