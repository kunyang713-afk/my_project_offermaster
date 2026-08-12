import asyncio
import json
import sys

# Windows 本地开发：psycopg 异步连接不兼容 ProactorEventLoop，需切换 Selector 策略。
# Linux（Docker）下无此问题，本段为空操作。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .graph import answer_session, start_session, stream_answer, stream_start
from .schemas import AnswerRequest, StartRequest

app = FastAPI(title="OfferMaster AI Brain", version="0.1.0")


def _sse_frame(event_type: str, data) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ai/interviews/{session_id}/start")
def start(session_id: str, payload: StartRequest):
    try:
        return start_session(session_id, payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"start failed: {exc}") from exc


@app.post("/ai/interviews/{session_id}/answer")
def answer(session_id: str, payload: AnswerRequest):
    try:
        return answer_session(session_id, payload.answer)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"answer failed: {exc}") from exc


# ---------- SSE 流式端点 ----------

_STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@app.post("/ai/interviews/{session_id}/start/stream")
async def start_stream(session_id: str, payload: StartRequest):
    async def gen():
        try:
            async for ev in stream_start(session_id, payload):
                yield _sse_frame(ev["type"], ev["data"])
        except Exception as exc:  # noqa: BLE001
            yield _sse_frame("error", {"detail": f"start stream failed: {exc}"})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_STREAM_HEADERS)


@app.post("/ai/interviews/{session_id}/answer/stream")
async def answer_stream(session_id: str, payload: AnswerRequest):
    async def gen():
        try:
            async for ev in stream_answer(session_id, payload.answer):
                yield _sse_frame(ev["type"], ev["data"])
        except Exception as exc:  # noqa: BLE001
            yield _sse_frame("error", {"detail": f"answer stream failed: {exc}"})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_STREAM_HEADERS)
