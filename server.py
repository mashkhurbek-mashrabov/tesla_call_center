"""FastAPI bridge: browser mic <-> Gemini 3.8 Live, with RAG as a tool call.

Server-to-server topology: the browser never talks to Google directly. The API key
stays here, and so do the RAG search and the topic guard -- which is the whole point,
since a guard the client could skip is not a guard.
"""

import asyncio
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types

import config
import guard
import rag

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
)
log = logging.getLogger("callcenter")

app = FastAPI(title="AI Call Center MVP")
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "static"), name="static")


def pick_voice(requested: str | None) -> str:
    """Allow-list the voice. The name reaches Google, so an unknown string is dropped
    rather than forwarded -- an invalid voice fails the whole session, not just audio."""
    return requested if requested in config.VOICES else config.VOICE_NAME


def live_config(voice: str) -> types.LiveConnectConfig:
    """No thinking_config and no enable_affective_dialog -- gemini-3.8-live rejects
    the first and the API removed the second."""
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        # Voice is fixed for the session: switching means reconnecting, which is why
        # the UI picks it before the call rather than during.
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
            )
        ),
        system_instruction=types.Content(
            parts=[types.Part(text=config.SYSTEM_INSTRUCTION)]
        ),
        tools=[config.SEARCH_TOOL],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(config.BASE_DIR / "static" / "index.html")


@app.get("/config")
async def ui_config() -> dict:
    """What the UI needs to label itself, so the agent name lives in one place."""
    return {
        "agent_name": config.AGENT_NAME,
        "company": config.COMPANY_NAME,
        "voices": list(config.VOICES),
        "default_voice": config.VOICE_NAME,
    }


@app.get("/health")
async def health() -> dict:
    try:
        index = rag.load()
        chunks = len(index["chunks"])
    except Exception as exc:  # index not built yet
        return {"ok": False, "error": str(exc)}
    return {"ok": bool(config.API_KEY), "model": config.LIVE_MODEL, "kb_chunks": chunks}


def run_tool(name: str, args: dict) -> dict:
    """Dispatch a model tool call. Guard runs here, before the KB is touched."""
    if name != "search_tesla_kb":
        return {"error": f"unknown tool {name}"}

    query = (args or {}).get("query", "")
    allowed, reason = guard.is_on_topic(query)
    if not allowed:
        log.warning("GUARD blocked query=%r (%s)", query, reason)
        return {"found": False, "off_topic": True, "instruction": config.OFF_TOPIC_RESPONSE}

    results = rag.search(query)
    log.info(
        "KB query=%r -> %s",
        query,
        [f"{r['doc']}:{r['section']} ({r['score']})" for r in results] or "no match",
    )
    return rag.format_for_model(results)


async def handle_tool_call(session, tool_call) -> None:
    responses = []
    for fc in tool_call.function_calls:
        result = await asyncio.to_thread(run_tool, fc.name, dict(fc.args or {}))
        responses.append(
            types.FunctionResponse(
                id=fc.id,
                name=fc.name,
                response=result,
                # Land the answer in the current turn rather than waiting for idle.
                scheduling="INTERRUPT",
            )
        )
    if responses:
        await session.send_tool_response(function_responses=responses)


async def browser_to_gemini(ws: WebSocket, session) -> None:
    """Forward raw PCM16 16kHz frames from the browser into the Live session."""
    while True:
        message = await ws.receive()
        if message.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect()

        if (audio := message.get("bytes")) is not None:
            await session.send_realtime_input(
                audio=types.Blob(data=audio, mime_type="audio/pcm;rate=16000")
            )
        elif (text := message.get("text")) is not None:
            # Control frames from the UI (end of call, etc).
            try:
                frame = json.loads(text)
            except json.JSONDecodeError:
                continue
            if frame.get("type") == "hangup":
                raise WebSocketDisconnect()
            # Typed input: no mic needed, so the agent is testable headlessly.
            if frame.get("type") == "say" and frame.get("text"):
                log.info("CALLER (typed): %s", frame["text"])
                await session.send_realtime_input(text=frame["text"])


async def gemini_to_browser(ws: WebSocket, session) -> None:
    """Forward model audio out, dispatch tool calls, log transcripts.

    session.receive() yields ONE turn and then the generator ends -- see the SDK
    docstring, "the returned responses will represent a complete model turn". The
    outer loop reopens it so the call lasts the whole session instead of hanging up
    after the greeting.
    """
    caller_turn: list[str] = []

    while True:
        got_any = False

        async for response in session.receive():
            got_any = True

            # Tool calls: the docs show response.tool_call at the top level; some SDK
            # builds nest it under server_content. Check both rather than guess.
            tool_call = getattr(response, "tool_call", None)
            if tool_call is None and response.server_content is not None:
                tool_call = getattr(response.server_content, "tool_call", None)
            if tool_call:
                await handle_tool_call(session, tool_call)

            if getattr(response, "go_away", None):
                log.warning("session go_away: %s", response.go_away)
                await ws.send_text(
                    json.dumps({"type": "error", "message": "session expiring"})
                )

            content = response.server_content
            if not content:
                continue

            if content.model_turn:
                for part in content.model_turn.parts:
                    if part.inline_data and part.inline_data.data:
                        await ws.send_bytes(part.inline_data.data)

            if content.input_transcription and content.input_transcription.text:
                caller_turn.append(content.input_transcription.text)

            if content.output_transcription and content.output_transcription.text:
                log.info("AGENT: %s", content.output_transcription.text.strip())

            if content.interrupted:
                # Barge-in: tell the browser to dump queued audio, or the agent keeps
                # talking over the caller for as long as the buffer is deep.
                await ws.send_text(json.dumps({"type": "interrupted"}))

            if content.turn_complete:
                if caller_turn:
                    said = "".join(caller_turn).strip()
                    # ponytail: observability only -- run_tool() is the enforcing
                    # layer. By turn_complete the model has already spoken, so
                    # rejecting here would be theater. This log is how the word
                    # lists get tuned against real calls.
                    allowed, reason = guard.is_on_topic(said)
                    log.info("CALLER: %s  [guard: %s]", said, reason)
                    if not allowed:
                        log.warning("GUARD flagged caller turn (%s)", reason)
                    caller_turn.clear()
                await ws.send_text(json.dumps({"type": "turn_complete"}))

        # An immediately-empty generator means the socket to Google is gone, not an
        # idle caller. Without this the loop would spin hot on a dead connection.
        if not got_any:
            log.info("live session closed by server")
            return


@app.websocket("/ws/call")
async def call(ws: WebSocket) -> None:
    await ws.accept()

    if not config.API_KEY:
        await ws.send_text(json.dumps({"type": "error", "message": "GEMINI_API_KEY not set"}))
        await ws.close()
        return

    voice = pick_voice(ws.query_params.get("voice"))
    client = genai.Client(api_key=config.API_KEY)
    log.info("call started (model=%s voice=%s)", config.LIVE_MODEL, voice)

    try:
        async with client.aio.live.connect(
            model=config.LIVE_MODEL, config=live_config(voice)
        ) as session:
            await ws.send_text(json.dumps({"type": "ready"}))
            # Nudge the model to speak first, the way a support agent answers a call.
            await session.send_realtime_input(text="(The caller has just connected.)")

            up = asyncio.create_task(browser_to_gemini(ws, session))
            down = asyncio.create_task(gemini_to_browser(ws, session))
            done, pending = await asyncio.wait(
                [up, down], return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                if (exc := task.exception()) and not isinstance(exc, WebSocketDisconnect):
                    raise exc

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("call failed")
        try:
            await ws.send_text(json.dumps({"type": "error", "message": str(exc)}))
        except Exception:
            pass
    finally:
        log.info("call ended")
        try:
            await ws.close()
        except Exception:
            pass
