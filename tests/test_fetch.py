"""fetch.py のテスト。手元に立てた HTTP サーバーに実際に要求を送って確かめる。

実行: python -m unittest discover -s tests
"""

import http.server
import io
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch  # noqa: E402

FAST = {"backoff": 0, "timeout": 5}


class _Handler(http.server.BaseHTTPRequestHandler):
    # パスごとの振る舞いを、テストが server.routes に入れる
    def log_message(self, *args):
        pass

    def _route(self):
        return self.server.routes[self.path]

    def do_GET(self):
        self.server.hits[self.path] = self.server.hits.get(self.path, 0) + 1
        self._route()(self)

    def do_HEAD(self):
        self.server.hits[self.path] = self.server.hits.get(self.path, 0) + 1
        self._route()(self)


def _send(handler, status, body=b"", headers=None):
    handler.send_response(status)
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)


def _fail_then(status_sequence, body):
    """呼ばれるたびに status_sequence の順で返し、尽きたら 200 と body を返す。"""
    remaining = list(status_sequence)

    def route(handler):
        if remaining:
            _send(handler, remaining.pop(0))
        else:
            _send(handler, 200, body)

    return route


def _truncated_then(body):
    """1回目は Content-Length より短い本文で接続を切り、2回目は全部返す。"""
    state = {"calls": 0}

    def route(handler):
        state["calls"] += 1
        if state["calls"] == 1:
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body[: len(body) // 2])
            handler.wfile.flush()
            handler.close_connection = True
        else:
            _send(handler, 200, body)

    return route


def _ranged(body, honor_range=True):
    def route(handler):
        if handler.command == "HEAD":
            _send(handler, 200, body)
            return
        header = handler.headers.get("Range")
        if not honor_range or not header:
            _send(handler, 200, body)
            return
        start, end = (int(x) for x in header.removeprefix("bytes=").split("-"))
        chunk = body[start : end + 1]
        _send(handler, 206, chunk, {"Content-Range": f"bytes {start}-{end}/{len(body)}"})

    return route


class FetchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.server.routes = {}
        cls.server.hits = {}
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def route(self, path, handler):
        self.server.routes[path] = handler
        self.server.hits[path] = 0
        return self.base + path

    def test_retries_server_errors(self):
        url = self.route("/flaky", _fail_then([500, 503, 429], b"ok"))
        self.assertEqual(fetch.fetch(url, **FAST), b"ok")
        self.assertEqual(self.server.hits["/flaky"], 4)

    def test_gives_up_after_retries(self):
        url = self.route("/down", _fail_then([500] * 10, b"never"))
        with self.assertRaises(HTTPError):
            fetch.fetch(url, retries=2, **FAST)
        self.assertEqual(self.server.hits["/down"], 3)

    def test_does_not_retry_client_errors(self):
        url = self.route("/forbidden", _fail_then([403] * 10, b"never"))
        with self.assertRaises(HTTPError):
            fetch.fetch(url, **FAST)
        self.assertEqual(self.server.hits["/forbidden"], 1)

    def test_not_found(self):
        url = self.route("/missing", _fail_then([404] * 10, b"never"))
        self.assertIsNone(fetch.fetch(url, not_found_ok=True, **FAST))
        with self.assertRaises(HTTPError):
            fetch.fetch(url, **FAST)

    def test_retries_truncated_body(self):
        body = b"x" * 10_000
        url = self.route("/truncated", _truncated_then(body))
        self.assertEqual(fetch.fetch(url, **FAST), body)
        self.assertEqual(self.server.hits["/truncated"], 2)

    def test_retries_connection_refused(self):
        # 何も待ち受けていないポート。再試行したことをログで確かめる
        with self.assertLogs("pipelines", "WARNING") as logs, self.assertRaises(OSError):
            fetch.fetch("http://127.0.0.1:9/", retries=2, **FAST)
        self.assertEqual(len(logs.records), 2)

    def test_content_length(self):
        url = self.route("/sized", _ranged(b"abcdef"))
        self.assertEqual(fetch.content_length(url, **FAST), 6)

    def test_range_file_reads_zip_member(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("a.txt", "first")
            archive.writestr("b.txt", "second" * 1000)
        url = self.route("/archive.zip", _ranged(buffer.getvalue()))
        reader = io.BufferedReader(fetch.RangeFile(url, **FAST), 1 << 10)
        with zipfile.ZipFile(reader) as archive:
            self.assertEqual(archive.read("b.txt"), b"second" * 1000)

    def test_range_file_stops_when_range_is_ignored(self):
        url = self.route("/no-range.zip", _ranged(b"z" * 5000, honor_range=False))
        file = fetch.RangeFile(url, **FAST)
        with self.assertRaises(fetch.RangeNotSupported):
            file.read(10)
        # 一時的な失敗ではないので再試行しない（HEAD 1回 + GET 1回）
        self.assertEqual(self.server.hits["/no-range.zip"], 2)

    def test_download(self):
        body = b"y" * 300_000
        url = self.route("/big.zip", _ranged(body, honor_range=False))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "big.zip"
            self.assertEqual(fetch.download(url, path, chunk_size=1 << 12, **FAST), len(body))
            self.assertEqual(path.read_bytes(), body)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_download_retries_truncated_body(self):
        body = b"w" * 50_000
        url = self.route("/cut.zip", _truncated_then(body))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cut.zip"
            fetch.download(url, path, **FAST)
            self.assertEqual(path.read_bytes(), body)
        self.assertEqual(self.server.hits["/cut.zip"], 2)


class ReplacingTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "out.ndjson"
        self.path.write_text("previous\n")

    def tearDown(self):
        self.dir.cleanup()

    def test_replaces_on_success(self):
        with fetch.replacing(self.path) as partial:
            partial.write_text("new\n")
        self.assertEqual(self.path.read_text(), "new\n")
        self.assertFalse(partial.exists())

    def test_keeps_previous_when_block_raises(self):
        with self.assertRaises(RuntimeError):
            with fetch.replacing(self.path) as partial:
                partial.write_text("")
                raise RuntimeError("0 rows")
        self.assertEqual(self.path.read_text(), "previous\n")
        self.assertFalse(partial.exists())

    def test_write_atomic(self):
        fetch.write_atomic(self.path, "text")
        self.assertEqual(self.path.read_text(), "text")
        fetch.write_atomic(self.path, b"bytes")
        self.assertEqual(self.path.read_bytes(), b"bytes")
        self.assertEqual(list(Path(self.dir.name).iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
