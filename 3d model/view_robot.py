#!/usr/bin/env python3
"""Open a GLB/GLTF file in an interactive browser-based 3D viewer."""

from __future__ import annotations

import argparse
import contextlib
import http.server
import socket
import threading
import urllib.parse
import webbrowser
from pathlib import Path


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Robot 3D Viewer</title>
  <script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.1.0/model-viewer.min.js"></script>
  <style>
    * { box-sizing: border-box; }
    html, body { width: 100%; height: 100%; margin: 0; overflow: hidden; }
    body { font: 14px system-ui, sans-serif; background: #11151b; color: #f4f7fb; }
    model-viewer { width: 100%; height: 100%; background: radial-gradient(circle, #35404d, #11151b 70%); }
    .toolbar { position: fixed; z-index: 2; top: 16px; left: 16px; display: flex; gap: 8px; }
    button { border: 1px solid #ffffff30; border-radius: 8px; padding: 8px 12px; color: white;
             background: #10151dcc; cursor: pointer; backdrop-filter: blur(8px); }
    button:hover { background: #273344; }
    .help { position: fixed; z-index: 2; bottom: 14px; left: 50%; transform: translateX(-50%);
            padding: 7px 12px; border-radius: 8px; background: #10151dcc; white-space: nowrap; }
  </style>
</head>
<body>
  <model-viewer id="viewer" src="/__MODEL__" alt="Interactive robot CAD model"
    camera-controls touch-action="pan-y" shadow-intensity="1" exposure="1"
    environment-image="neutral" interaction-prompt="auto">
  </model-viewer>
  <div class="toolbar">
    <button id="rotate">Auto-rotate: off</button>
    <button id="reset">Reset view</button>
    <button id="fullscreen">Fullscreen</button>
  </div>
  <div class="help">Drag to orbit · right-drag to pan · wheel to zoom · drop another GLB/GLTF to view it</div>
  <script>
    const viewer = document.querySelector('#viewer');
    const rotate = document.querySelector('#rotate');
    rotate.onclick = () => {
      viewer.autoRotate = !viewer.autoRotate;
      rotate.textContent = `Auto-rotate: ${viewer.autoRotate ? 'on' : 'off'}`;
    };
    document.querySelector('#reset').onclick = () => {
      viewer.cameraOrbit = 'auto auto auto';
      viewer.cameraTarget = 'auto auto auto';
      viewer.fieldOfView = 'auto';
      viewer.jumpCameraToGoal();
    };
    document.querySelector('#fullscreen').onclick = () => document.documentElement.requestFullscreen();
    document.addEventListener('dragover', event => event.preventDefault());
    document.addEventListener('drop', event => {
      event.preventDefault();
      const file = event.dataTransfer.files[0];
      if (!file || !/\.(glb|gltf)$/i.test(file.name)) return;
      if (viewer.dataset.objectUrl) URL.revokeObjectURL(viewer.dataset.objectUrl);
      viewer.dataset.objectUrl = URL.createObjectURL(file);
      viewer.src = viewer.dataset.objectUrl;
    });
  </script>
</body>
</html>
"""


class ViewerHandler(http.server.SimpleHTTPRequestHandler):
    """Serve the viewer page and one selected model file."""

    model_path: Path

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(self.model_path.parent), **kwargs)

    def do_GET(self) -> None:
        route = urllib.parse.urlsplit(self.path).path
        if route == "/":
            model_url = "/model/" + urllib.parse.quote(self.model_path.name)
            page = HTML.replace("/__MODEL__", model_url).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        if route == "/model/" + urllib.parse.quote(self.model_path.name):
            self.path = "/" + urllib.parse.quote(self.model_path.name)
            return super().do_GET()
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        pass


def free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", nargs="?", type=Path, default=Path(__file__).with_name("robot.glb"))
    parser.add_argument("--no-browser", action="store_true", help="print the URL without opening it")
    args = parser.parse_args()
    model = args.model.expanduser().resolve()
    if not model.is_file():
        parser.error(f"model not found: {model}")
    if model.suffix.lower() not in {".glb", ".gltf"}:
        parser.error("model must be a .glb or .gltf file")

    handler = type("ConfiguredViewerHandler", (ViewerHandler,), {"model_path": model})
    port = free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Serving {model.name} at {url}")
    print("Press Ctrl+C to close the viewer.")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nViewer closed.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
