"""Exercise the real relay and decoder with a synthetic scrcpy 4.1 stream."""
import socket
import struct
import threading
import time
import unittest
from io import BytesIO

import av
from PIL import Image
from scrcpy_preview import BrowserPreview


def video_stream():
    encoder = av.CodecContext.create('libx264', 'w')
    encoder.width, encoder.height = 64, 48
    encoder.pix_fmt = 'yuv420p'
    encoder.options = {'preset':'ultrafast', 'tune':'zerolatency'}
    frame = av.VideoFrame.from_image(Image.new('RGB', (64,48), 'blue'))
    packets = encoder.encode(frame) + encoder.encode(None)
    return b'Test phone'.ljust(64,b'\0') + b'h264' + struct.pack('>III', 0x80000000,64,48) + b''.join(
        struct.pack('>QI', 1<<61, packet.size) + bytes(packet) for packet in packets)


class PreviewTests(unittest.TestCase):
    def wait_frame(self, preview):
        deadline=time.monotonic()+5
        while not preview.frame() and not preview.error and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertIsNone(preview.error)
        self.assertIsNotNone(preview.frame())
        self.assertEqual(Image.open(BytesIO(preview.frame())).size,(64,48))

    def test_fragmented_protocol_decodes_and_close_ends_threads(self):
        preview=BrowserPreview()
        self.addCleanup(preview.close)
        data=video_stream()
        for i in range(0,len(data),17):
            preview._copy(data[i:i+17])
        self.wait_frame(preview)
        self.assertTrue(preview.status()['live'])
        preview.close()
        self.assertFalse(preview.status()['live'])
        self.assertFalse(any(t.is_alive() for t in preview.threads))

    def test_relay_forwards_video_unchanged_and_reverse_traffic(self):
        preview=BrowserPreview()
        self.addCleanup(preview.close)
        upstream=socket.socket()
        self.addCleanup(upstream.close)
        upstream.bind(('127.0.0.1',0));upstream.listen()
        upstream.settimeout(5)
        preview.adb_port=upstream.getsockname()[1]
        data=b'\0'+video_stream()
        received=[]
        def phone():
            with upstream.accept()[0] as conn:
                conn.settimeout(5)
                conn.sendall(data)
                received.append(conn.recv(100))
        worker=threading.Thread(target=phone)
        worker.start()
        with socket.create_connection(('127.0.0.1',preview.port),timeout=5) as client:
            actual=b''
            while len(actual)<len(data):
                actual+=client.recv(len(data)-len(actual))
            self.assertEqual(actual,data)
            client.sendall(b'control')
        worker.join(5)
        self.assertEqual(received,[b'control'])
        self.wait_frame(preview)

    def test_decoder_failure_does_not_stop_relay(self):
        preview=BrowserPreview()
        self.addCleanup(preview.close)
        preview._copy(b'\0'*64+b'bad!')
        deadline=time.monotonic()+3
        while not preview.error and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertIn('Unsupported',preview.error)
        self.assertFalse(preview.closed.is_set())
        self.assertTrue(preview.threads[1].is_alive())


if __name__=='__main__':
    unittest.main()
