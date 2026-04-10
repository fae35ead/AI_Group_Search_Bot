import contextlib
import socket
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

import uvicorn

from main import app

HOST = '127.0.0.1'
HEALTHCHECK_TIMEOUT_SECONDS = 30.0


def _wait_for_healthcheck(url: str, timeout_seconds: float) -> bool:
  deadline = time.time() + timeout_seconds
  while time.time() < deadline:
    try:
      with urlopen(url, timeout=2) as response:
        if 200 <= response.status < 300:
          return True
    except URLError:
      time.sleep(0.25)
      continue
    except OSError:
      time.sleep(0.25)
      continue
    time.sleep(0.1)
  return False


def main() -> int:
  config = uvicorn.Config(
    app,
    host=HOST,
    port=0,
    log_level='info',
    access_log=False,
  )
  server_socket = config.bind_socket()
  port = int(server_socket.getsockname()[1])
  server = uvicorn.Server(config)
  server.install_signal_handlers = lambda: None

  server_thread = threading.Thread(
    target=lambda: server.run(sockets=[server_socket]),
    name='ai-group-discovery-server',
    daemon=True,
  )
  server_thread.start()

  app_url = f'http://{HOST}:{port}/'
  health_url = f'http://{HOST}:{port}/api/health'

  print(f'AI Group Discovery is starting on {app_url}')
  if not _wait_for_healthcheck(health_url, HEALTHCHECK_TIMEOUT_SECONDS):
    print('Server failed to become healthy within the expected time window.')
    server.should_exit = True
    server_thread.join(timeout=5)
    return 1

  print('Browser is opening. Close this window to stop the local server.')
  webbrowser.open(app_url, new=1)

  try:
    while server_thread.is_alive():
      server_thread.join(timeout=0.5)
  except KeyboardInterrupt:
    print('Stopping local server...')
    server.should_exit = True
    server_thread.join(timeout=10)
  finally:
    with contextlib.suppress(OSError):
      server_socket.close()

  return 0


if __name__ == '__main__':
  raise SystemExit(main())
