from urllib import parse

from starlette.responses import JSONResponse, StreamingResponse, ContentStream

from ulslib import const


async def streaming_response(content: ContentStream, filename: str, size: int) -> StreamingResponse:
    filename_encoded = parse.quote_plus(filename)
    return StreamingResponse(
        content,
        headers={
            "Content-Length": str(size),
            "Content-Disposition": f"attachment; filename=\"{filename_encoded}\"",
        }, media_type=const.MEDIA_TYPE_STREAM)


async def initiated_response(url: str, filename: str, file_path: str, size: int, parts: int) -> JSONResponse:
    return JSONResponse(
        content={"url": f"{url}",
                 "filename": f"{filename}",
                 "file_path": f"{file_path}",
                 "size": f"{size}",
                 "parts": f"{parts}",
                 "message": "Downloader has started.."},
        status_code=200
    )


async def error_response(status: int, url: str, msg: str) -> JSONResponse:
    return JSONResponse(
        content={"url": f"{url}",
                 "message": f"{msg}"},
        status_code=status
    )
