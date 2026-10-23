"""データセットの取得処理で使う共通部品。標準ライブラリだけで書く。

各データセットリポからは submodule の置き場所（shared）経由で読む:

    from shared.fetch import fetch, replacing

- fetch: URL を取る。一時的な失敗（5xx・429・接続断・タイムアウト・本文の途中切れ・
  TLS の失敗）は、間隔を延ばしながら再試行する
- content_length: HEAD で大きさを取る。fetch と同じく再試行する
- RangeFile: HTTP の Range で読むシーク可能なファイル。zipfile などに渡して、
  大きな配布物の一部だけを読む。Range が効かずに全体が返ってきたら止める
- write_atomic / replacing: 一時ファイルに書いてから置き換える。途中で失敗したときや、
  呼び出し側が例外を投げたとき（0件だった、など）は前回の出力を残す
"""

import contextlib
import http.client
import io
import logging
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TypeVar
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger("pipelines")

T = TypeVar("T")

DEFAULT_TIMEOUT = 60
# 待ち時間は 5, 10, 20, 40 秒。合わせて1分強待ってだめなら諦める
DEFAULT_RETRIES = 4
DEFAULT_BACKOFF = 5.0


def _is_transient(error: Exception) -> bool:
    if isinstance(error, HTTPError):
        return error.code >= 500 or error.code == 429
    # 接続断・タイムアウト・TLS の失敗は OSError、本文の途中切れ（IncompleteRead）は
    # HTTPException の仲間で届く
    return isinstance(error, (OSError, http.client.HTTPException))


def request(
    req: Request,
    read: Callable[[http.client.HTTPResponse], T],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
) -> T:
    """req を送り、応答を read に渡した結果を返す。一時的な失敗は再試行する。

    read の中で投げた例外のうち一時的でないもの（Range が効かない、など）は
    再試行せずにそのまま上げる。
    """
    for attempt in range(retries + 1):
        try:
            with urlopen(req, timeout=timeout) as response:
                return read(response)
        except Exception as error:
            if not _is_transient(error) or attempt == retries:
                raise
            wait = backoff * 2**attempt
            logger.warning("retry in %.0fs: %s (%s)", wait, req.full_url, error)
            time.sleep(wait)
    raise AssertionError("unreachable")


def fetch(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    user_agent: str | None = None,
    not_found_ok: bool = False,
    **options,
) -> bytes | None:
    """URL の本文を返す。not_found_ok なら 404 は None を返す。

    options は request に渡す（timeout / retries / backoff）。
    """
    all_headers = dict(headers or {})
    if user_agent:
        all_headers["User-Agent"] = user_agent
    try:
        return request(Request(url, headers=all_headers), lambda r: r.read(), **options)
    except HTTPError as error:
        if not_found_ok and error.code == 404:
            return None
        raise


def content_length(url: str, *, user_agent: str | None = None, **options) -> int:
    """HEAD で Content-Length を取る。"""
    headers = {"User-Agent": user_agent} if user_agent else {}
    req = Request(url, headers=headers, method="HEAD")
    return request(req, lambda r: int(r.headers["Content-Length"]), **options)


class RangeFile(io.RawIOBase):
    """HTTP の Range で読む、シーク可能な読み取り専用ファイル。

    zipfile.ZipFile(io.BufferedReader(RangeFile(url), 1 << 16)) のように使う。
    バッファは読むたびに先読みされるので、小さいメンバーを多く読むなら小さくする。
    """

    def __init__(
        self,
        url: str,
        size: int | None = None,
        *,
        user_agent: str | None = None,
        **options,
    ) -> None:
        self.url = url
        self.user_agent = user_agent
        self.options = options
        self.size = size if size is not None else content_length(url, user_agent=user_agent, **options)
        self.position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self.position = offset
        elif whence == io.SEEK_CUR:
            self.position += offset
        else:
            self.position = self.size + offset
        return self.position

    def readinto(self, buffer) -> int:
        if self.position >= self.size or len(buffer) == 0:
            return 0
        end = min(self.size, self.position + len(buffer)) - 1
        headers = {"Range": f"bytes={self.position}-{end}"}
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        data = request(Request(self.url, headers=headers), self._read_partial, **self.options)
        buffer[: len(data)] = data
        self.position += len(data)
        return len(data)

    def _read_partial(self, response) -> bytes:
        # Range を無視して 200 で全体が返ってきたら、配布物を丸ごと読むことになる
        if response.status != 206:
            raise RuntimeError(f"{self.url}: Range が効かない (HTTP {response.status})")
        return response.read()


@contextlib.contextmanager
def replacing(path: Path) -> Iterator[Path]:
    """path を置き換えるための一時ファイルの場所を渡す。

    with ブロックを抜けたときに一時ファイルで path を置き換える。ブロックの中で
    例外が出たら一時ファイルを消し、path は前回のまま残す。0件だったときは
    ブロックの中で例外を投げれば、前回の出力を空のファイルで上書きしない。
    """
    partial = path.with_name(path.name + ".part")
    try:
        yield partial
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(path)


def write_atomic(path: Path, data: bytes | str, encoding: str = "utf-8") -> None:
    """一時ファイルに書いてから置き換える。書きかけのファイルを残さない。"""
    with replacing(path) as partial:
        if isinstance(data, str):
            partial.write_text(data, encoding=encoding)
        else:
            partial.write_bytes(data)
