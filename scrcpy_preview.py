"""scrcpy 4.1 loopback relay: untouched recorder traffic, bounded preview copy."""
from __future__ import annotations

import io
import queue
import socket
import struct
import threading
import time

MAX_BROWSER_PREVIEW_FPS = 15
# This is intentionally much smaller than the camera stream. It keeps the
# browser preview responsive on laptops while leaving the source/recording
# quality untouched.
MAX_BROWSER_PREVIEW_EDGE = 240


class BrowserPreview:
    def __init__(self):
        import av
        from PIL import Image  # Check dependencies before launching scrcpy.
        self.av = av
        self.closed = threading.Event()
        self.chunks = queue.Queue(maxsize=128)  # At most 8 MiB of queued stream data.
        self.lock = threading.Lock()
        self.sockets = set()
        self.jpeg = None
        self.sequence = 0
        self.updated = None
        self.error = None
        self.codec_name = None
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(4)
        self.listener.settimeout(.25)
        self.port = self.listener.getsockname()[1]
        # scrcpy owns this separate ADB forwarding port and removes it itself.
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.adb_port = probe.getsockname()[1]
        self.threads = []
        self._thread(self._decode)
        self._thread(self._accept)

    def _thread(self, fn, *args):
        thread = threading.Thread(target=fn, args=args, daemon=True, name='scrcpy-preview')
        self.threads.append(thread)
        thread.start()

    def arguments(self):
        return [f'--port={self.adb_port}', '--force-adb-forward',
                '--tunnel-host=127.0.0.1', f'--tunnel-port={self.port}']

    def _copy(self, data):
        if self.error or self.closed.is_set():
            return
        try:
            self.chunks.put_nowait(data)
        except queue.Full:
            self.error = 'Preview decoding could not keep up. Recording continues; try a lower resolution next time.'

    def _accept(self):
        first = True
        while not self.closed.is_set():
            downstream = upstream = None
            try:
                downstream, _ = self.listener.accept()
                upstream = socket.create_connection(('127.0.0.1', self.adb_port), timeout=2)
                with self.lock:
                    self.sockets.update((upstream, downstream))
                if first:
                    # Failed scrcpy startup probes must not consume the video slot.
                    dummy = upstream.recv(1)
                    if dummy != b'\0':
                        self._close_socket(upstream)
                        self._close_socket(downstream)
                        continue
                    downstream.sendall(dummy)
                    first = False
                    video = True
                else:
                    video = False
                upstream.settimeout(None)
                self._thread(self._pump, upstream, downstream, video)
                self._thread(self._pump, downstream, upstream, False)
            except socket.timeout:
                if downstream:
                    self._close_socket(downstream)
                if upstream:
                    self._close_socket(upstream)
            except OSError:
                if downstream:
                    self._close_socket(downstream)
                if upstream:
                    self._close_socket(upstream)

    def _close_socket(self, sock):
        with self.lock:
            self.sockets.discard(sock)
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    def _pump(self, source, destination, video):
        try:
            while not self.closed.is_set():
                data = source.recv(65536)
                if not data:
                    break
                destination.sendall(data)
                if video:
                    self._copy(data)
        except OSError:
            pass
        finally:
            self._close_socket(source)
            self._close_socket(destination)

    def _decode(self):
        buffer = bytearray()
        initial = True
        decoder = None
        config = b''
        last_image = 0.0
        try:
            while not self.closed.is_set() and not self.error:
                try:
                    buffer.extend(self.chunks.get(timeout=.25))
                except queue.Empty:
                    continue
                if initial:
                    # The relay already forwarded the dummy byte. Then name[64], codec[4].
                    if len(buffer) < 68:
                        continue
                    codec_id = bytes(buffer[64:68])
                    self.codec_name = {b'h264':'h264', b'h265':'hevc', b'\0av1':'av1',
                                       b'\0vp8':'vp8', b'\0vp9':'vp9'}.get(codec_id)
                    if not self.codec_name:
                        raise ValueError(f'Unsupported video stream header: {codec_id!r}')
                    del buffer[:68]
                    initial = False
                while len(buffer) >= 12:
                    header = bytes(buffer[:12])
                    if header[0] & 0x80:
                        _, width, height = struct.unpack('>III', header)
                        if not width or not height:
                            raise ValueError('Invalid video dimensions')
                        decoder = self.av.CodecContext.create(self.codec_name, 'r')
                        decoder.width, decoder.height = width, height
                        decoder.thread_count = 2
                        decoder.flags |= 0x80000  # AV_CODEC_FLAG_LOW_DELAY
                        config = b''
                        del buffer[:12]
                        continue
                    flags, size = struct.unpack('>QI', header)
                    if not 0 < size <= 32 * 1024 * 1024:
                        raise ValueError('Invalid scrcpy packet size')
                    if len(buffer) < 12 + size:
                        break
                    packet = bytes(buffer[12:12+size])
                    del buffer[:12+size]
                    if decoder is None:
                        raise ValueError('Missing scrcpy 4.1 video session header')
                    if flags & (1 << 62) and self.codec_name in ('h264', 'hevc'):
                        config += packet
                        continue
                    frames = decoder.decode(self.av.Packet(config + packet))
                    config = b''
                    for frame in frames:
                        now = time.monotonic()
                        if now - last_image < 1 / MAX_BROWSER_PREVIEW_FPS:
                            continue
                        scale = min(1, MAX_BROWSER_PREVIEW_EDGE / max(frame.width, frame.height))
                        small = frame.reformat(width=max(1, int(frame.width*scale)),
                                               height=max(1, int(frame.height*scale)), format='rgb24')
                        output = io.BytesIO()
                        small.to_image().save(output, format='JPEG', quality=60)
                        with self.lock:
                            self.jpeg = output.getvalue()
                            self.sequence += 1
                            self.updated = now
                        last_image = now
        except Exception as exc:
            self.error = f'Browser preview unavailable: {exc}. Recording continues.'
        finally:
            # Discard queued copies on failure; the relay continues forwarding.
            while True:
                try:
                    self.chunks.get_nowait()
                except queue.Empty:
                    break

    def status(self):
        with self.lock:
            age = None if self.updated is None else time.monotonic() - self.updated
            return {'enabled':True, 'sequence':self.sequence, 'error':self.error,
                    'age':age, 'live':not self.closed.is_set() and not self.error and age is not None and age < 2}

    def frame(self):
        with self.lock:
            return self.jpeg

    def close(self):
        self.closed.set()
        self.listener.close()
        with self.lock:
            sockets = list(self.sockets)
        for sock in sockets:
            self._close_socket(sock)
        for thread in self.threads:
            if thread is not threading.current_thread():
                thread.join(timeout=.5)
