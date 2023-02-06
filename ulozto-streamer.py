#!/usr/bin/env python3

import asyncio
import os
import signal
from asyncio import CancelledError, Future
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from os import path
from typing import Optional

from fastapi import FastAPI, Request
from starlette.background import BackgroundTasks

from uldlib import const as uldconst, captcha, utils
from uldlib.captcha import AutoReadCaptcha
from uldlib.downloader import Downloader
from uldlib.torrunner import TorRunner
from uldlib.utils import DownloaderError
from ulslib import const
from ulslib.frontend import WebAppFrontend
from ulslib.segfile import AsyncSegFileReader
from ulslib.responses import error_response, initiated_response, streaming_response

app = FastAPI()

temp_path: str = os.getenv('TEMP_FOLDER', '')
data_folder: str = os.getenv('DATA_FOLDER', '')
download_path: str = os.getenv('DOWNLOAD_FOLDER', '')
default_parts: int = int(os.getenv('PARTS', 10))
auto_delete_downloads: bool = os.getenv('AUTO_DELETE_DOWNLOADS', '0').strip().lower() in ['true', '1', 't', 'y', 'yes']
tor_on_start: bool = os.getenv('TOR_ON_START', '0').strip().lower() in ['true', '1', 't', 'y', 'yes']

model_path = path.join(data_folder, uldconst.MODEL_FILENAME)
frontend: WebAppFrontend = WebAppFrontend()
captcha_solve_fnc: AutoReadCaptcha = captcha.AutoReadCaptcha(
    model_path, uldconst.MODEL_DOWNLOAD_URL, frontend)
executor = ThreadPoolExecutor(max_workers=2)
exception: Exception = None

downloader: Downloader = None
tor: TorRunner = None
global_url: str = None


async def generate_stream(request: Request, background_tasks: BackgroundTasks, stat_file: str, file_path: str,
                          parts: int):
    download_canceled = False
    try:
        for seg_idx in range(parts):
            reader = AsyncSegFileReader(file_path, stat_file, parts, seg_idx)
            stream_generator = reader.read()
            with suppress(CancelledError):
                async for data in stream_generator:
                    if await request.is_disconnected():
                        download_canceled = True
                        frontend.main_log("Client has closed download connection prematurely...",
                                          level=utils.LogLevel.INFO)
                        await stream_generator.aclose()
                        return
                    yield data
    finally:
        if not download_canceled:
            while downloader.success is None:
                await asyncio.sleep(1)
            background_tasks.add_task(cleanup_download, file_path)


def cleanup_download(file_path: str = None):
    cleanup_metadata()
    if auto_delete_downloads:
        frontend.main_log(f"Cleanup of: {file_path}", level=utils.LogLevel.INFO)
        with suppress(FileNotFoundError):
            os.remove(file_path + uldconst.DOWNPOSTFIX)
            os.remove(file_path + uldconst.CACHEPOSTFIX)
            os.remove(file_path)


def cleanup_metadata():
    global exception, global_url, downloader, tor
    if exception is not None:
        exception = None
    if global_url is not None:
        global_url = None
    if downloader is not None:
        downloader.terminate()
        downloader = None
    if tor is not None and not tor_on_start:
        tor.stop()
        tor = None


@app.get("/initiate", responses={
    200: {"content": {const.MEDIA_TYPE_JSON: {}}, },
    429: {"content": {const.MEDIA_TYPE_JSON: {}}, }
})
async def initiate(url: str, parts: Optional[int] = default_parts):
    global downloader, tor, global_url

    if global_url is not None and global_url != url:
        return await error_response(429, url, "Downloader is busy.. Free download is limited to single download.")

    if downloader is None:
        global_url = url
        try:
            if not tor_on_start or tor is None:
                tor = TorRunner(temp_path, frontend.tor_log)
                tor.launch()

            downloader = Downloader(tor, frontend, captcha_solve_fnc)

            # temp_dir: str = "", do_overwrite: bool = False, conn_timeout=DEFAULT_CONN_TIMEOUT
            future = asyncio.get_event_loop() \
                .run_in_executor(executor, downloader.download, url, parts,
                                 download_path, download_path, True, const.DEFAULT_CONN_TIMEOUT)
            future.add_done_callback(downloader_callback)

            while downloader.total_size is None:
                if exception is not None:
                    raise exception
                await asyncio.sleep(0.1)
        except DownloaderError as e:
            frontend.main_log(str(e), level=utils.LogLevel.WARNING)
            cleanup_metadata()
            return await error_response(429, url, "Recoverable Download error.")

    return await initiated_response(url, downloader.filename, downloader.output_filename, downloader.total_size, parts)


@app.get("/download", responses={
    200: {"content": {const.MEDIA_TYPE_STREAM: {}}, },
    400: {"content": {const.MEDIA_TYPE_JSON: {}}, },
    429: {"content": {const.MEDIA_TYPE_JSON: {}}, }
})
async def download_endpoint(request: Request, background_tasks: BackgroundTasks, url: str):
    global downloader

    if downloader is None:
        return await error_response(400, url, "Download not initiated.")
    elif global_url != url:
        return await error_response(429, url, "Another download initiated.")

    try:
        return await streaming_response(
            generate_stream(request,
                            background_tasks,
                            downloader.stat_filename,
                            downloader.output_filename,
                            downloader.parts),
            downloader.filename,
            downloader.total_size)
    except BaseException as e:
        frontend.main_log(str(e), level=utils.LogLevel.ERROR)
        cleanup_metadata()
        return await error_response(429, url, "Recoverable Download error.")


def downloader_callback(future: Future):
    global exception
    if future.exception():
        exception = future.exception()
    else:
        frontend.main_log("Download finished..", level=utils.LogLevel.SUCCESS)


def sigint_handler(sig, frame):
    if tor is not None and tor.torRunning:
        tor.stop()
    if downloader is not None:
        downloader.terminate()


if __name__ == "__main__":
    import uvicorn

    signal.signal(signal.SIGINT, sigint_handler)
    signal.signal(signal.SIGHUP, sigint_handler)
    if tor_on_start:
        tor = TorRunner(temp_path, frontend.tor_log)
        tor.launch()
    uvicorn.run(app, host="0.0.0.0", port=8000)
