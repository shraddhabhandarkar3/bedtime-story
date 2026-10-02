"""FastAPI bridge for the Quiet Storyteller web interface."""

import asyncio
import atexit
import json
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

import main as storyteller
from database import (
    get_child_profile,
    get_latest_initial_request,
    get_or_create_user,
    get_recent_stories,
    get_story_history,
    initialize_database,
    new_story_id,
    save_child_profile,
    save_message,
)


app = FastAPI(title="Quiet Storyteller")
initialize_database()
logger = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parent
TTS_URL = "http://127.0.0.1:8768"
TTS_PYTHON = ROOT_DIR / ".venv-tts" / "bin" / "python"
TTS_SERVICE = ROOT_DIR / "tts_service.py"
_tts_process: Optional[subprocess.Popen] = None
ALLOWED_WEBSOCKET_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
}


def tts_is_running() -> bool:
    try:
        with urlopen(f"{TTS_URL}/health", timeout=0.6) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def ensure_tts_service() -> bool:
    """Start the optional local narrator when its isolated environment exists."""
    global _tts_process
    if tts_is_running():
        return True
    if not TTS_PYTHON.exists() or not TTS_SERVICE.exists():
        return False
    if _tts_process is None or _tts_process.poll() is not None:
        environment = os.environ.copy()
        environment.setdefault("HF_HOME", str(ROOT_DIR / ".tts-cache" / "huggingface"))
        log_path = ROOT_DIR / ".tts-cache" / "narrator.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("a", encoding="utf-8")
        _tts_process = subprocess.Popen(
            [str(TTS_PYTHON), str(TTS_SERVICE)],
            cwd=ROOT_DIR,
            env=environment,
            stdout=log_file,
            stderr=log_file,
        )
    # Importing PyTorch can take close to a minute on the first launch. Keep the
    # UI in its "Preparing" state rather than prematurely selecting fallback.
    for _ in range(1200):
        if tts_is_running():
            return True
        if _tts_process.poll() is not None:
            return False
        time.sleep(0.1)
    return False


def stop_tts_service() -> None:
    if _tts_process is not None and _tts_process.poll() is None:
        _tts_process.terminate()


atexit.register(stop_tts_service)


def latest_review(user_id: int, story_id: str) -> Optional[Dict]:
    """Return the most recent stored judge result without exposing story history."""
    for message in reversed(get_story_history(user_id, story_id)):
        if message["role"] == "judge":
            try:
                review = json.loads(message["content"])
            except json.JSONDecodeError:
                return None
            return review if isinstance(review, dict) else None
    return None


async def run_blocking(function, *args, **kwargs):
    return await asyncio.to_thread(function, *args, **kwargs)


async def send_stage(
    websocket: WebSocket,
    stage: str,
    message: str,
    motif: Optional[Dict] = None,
) -> None:
    payload = {"type": "stage", "stage": stage, "message": message}
    if motif:
        payload["motif"] = motif
    await websocket.send_json(payload)


async def start_story(websocket: WebSocket, payload: Dict, session: Dict) -> None:
    user_name = str(payload.get("user_name") or "").strip()
    if not user_name:
        await websocket.send_json({"type": "error", "message": "Please enter a name or nickname."})
        return
    if len(user_name) > 80:
        await websocket.send_json({"type": "error", "message": "Please use a shorter name or nickname."})
        return

    raw_request = str(payload.get("request") or "")
    if len(raw_request) > 2400:
        await websocket.send_json({"type": "error", "message": "Please shorten the story request a little."})
        return
    request, surprise_requested = storyteller.resolve_story_request(raw_request)
    await send_stage(
        websocket,
        "thinking",
        "Hmm… let me think about that for a moment.",
        storyteller.normalize_visual_motif(None),
    )

    user_id = await run_blocking(get_or_create_user, user_name)
    recent_stories = await run_blocking(get_recent_stories, user_id)
    remembered_profile = await run_blocking(get_child_profile, user_id)
    if not storyteller.child_profile_has_memory(remembered_profile):
        previous_request = await run_blocking(get_latest_initial_request, user_id)
        if previous_request:
            previous_classification = await run_blocking(
                storyteller.categorize_request,
                previous_request,
            )
            remembered_profile = previous_classification.get("child_profile", {})
    classification = await run_blocking(storyteller.categorize_request, request)
    classification["child_profile"] = storyteller.merge_child_profiles(
        remembered_profile,
        classification.get("child_profile"),
    )
    acknowledgement = classification.pop("acknowledgement")
    motif = classification.pop("visual_motif")
    category = classification["category"]
    story_id = new_story_id()
    await run_blocking(save_message, user_id, story_id, "user", request, category)
    if storyteller.child_profile_has_memory(classification["child_profile"]):
        await run_blocking(
            save_child_profile,
            user_id,
            story_id,
            classification["child_profile"],
        )

    if surprise_requested:
        acknowledgement = "No details tonight? I’ll surprise you with something new, gentle, and cozy."
    await websocket.send_json(
        {
            "type": "acknowledgement",
            "message": acknowledgement,
            "motif": motif,
            "child_profile": classification.get("child_profile", {}),
            "past_stories": recent_stories,
        }
    )
    await send_stage(
        websocket,
        "cooking",
        "I’m cooking up the characters, a gentle little problem, and a cozy ending…",
        motif,
    )
    first_draft = await run_blocking(
        storyteller.generate_story,
        request,
        classification,
        recent_stories,
    )
    await send_stage(
        websocket,
        "checking",
        "I have a draft. I’m giving it one quiet bedtime check…",
        {**motif, "motion": "settle"},
    )
    story = await run_blocking(
        storyteller.improve_until_acceptable,
        user_id,
        story_id,
        request,
        first_draft,
        classification,
        recent_stories,
    )

    session.clear()
    session.update(
        {
            "user_id": user_id,
            "story_id": story_id,
            "request": request,
            "active_request": request,
            "classification": classification,
            "category": category,
            "recent_stories": recent_stories,
            "story": story,
            "conversation": [],
            "changes_made": 0,
            "motif": motif,
        }
    )
    await websocket.send_json(
        {
            "type": "story",
            "story": story,
            "story_id": story_id,
            "motif": motif,
            "review": latest_review(user_id, story_id),
        }
    )


async def continue_story(websocket: WebSocket, payload: Dict, session: Dict) -> None:
    if not session:
        await websocket.send_json({"type": "error", "message": "Start a story first."})
        return
    message = str(payload.get("message") or "").strip()
    if not message:
        return
    if len(message) > 1000:
        await websocket.send_json({"type": "error", "message": "Please send that change in a shorter message."})
        return

    await run_blocking(
        save_message,
        session["user_id"],
        session["story_id"],
        "user",
        message,
        session["category"],
    )
    await send_stage(
        websocket,
        "thinking",
        "Let me think about how that should shape the story…",
        session["motif"],
    )
    turn = await run_blocking(
        storyteller.understand_conversation_turn,
        session["request"],
        session["story"],
        session["conversation"],
        message,
        session["classification"].get("child_profile"),
    )
    if turn["profile_update"] is not None:
        merged_profile = storyteller.merge_child_profiles(
            session["classification"].get("child_profile"),
            turn["profile_update"],
        )
        session["classification"]["child_profile"] = merged_profile
        await run_blocking(
            save_child_profile,
            session["user_id"],
            session["story_id"],
            merged_profile,
        )
    assistant_reply = turn["assistant_reply"].strip()
    await run_blocking(
        save_message,
        session["user_id"],
        session["story_id"],
        "assistant",
        assistant_reply,
        "conversation",
    )
    session["conversation"].extend(
        [
            {"role": "parent", "content": message},
            {"role": "assistant", "content": assistant_reply},
        ]
    )
    await websocket.send_json(
        {"type": "conversation", "message": assistant_reply, "intent": turn["intent"]}
    )

    if turn["intent"] == "accept":
        await websocket.send_json({"type": "complete"})
        return
    if turn["intent"] in {"discuss", "clarify"}:
        return
    if session["changes_made"] >= storyteller.MAX_USER_CHANGES:
        await websocket.send_json(
            {"type": "error", "message": "Let’s start a fresh story for the next big change."}
        )
        return

    feedback = turn["revision_request"].strip()
    if turn["adaptation"] is not None:
        feedback += "\nRuntime creative adaptation brief:\n" + json.dumps(
            turn["adaptation"], ensure_ascii=False, indent=2
        )
    session["changes_made"] += 1
    session["active_request"] += (
        f"\nParent's exact feedback: {message}"
        f"\nContextual revision instruction: {feedback}"
        f"\nRemembered child profile: "
        f"{json.dumps(session['classification'].get('child_profile', {}), ensure_ascii=False)}"
    )
    await send_stage(
        websocket,
        "revising",
        "I’m reshaping the story around that idea…",
        session["motif"],
    )
    revised = await run_blocking(
        storyteller.revise_story,
        session["active_request"],
        session["story"],
        session["classification"],
        [],
        feedback,
    )
    await send_stage(
        websocket,
        "checking",
        "The new draft is ready. I’m checking that it still feels gentle and complete…",
        {**session["motif"], "motion": "settle"},
    )
    session["story"] = await run_blocking(
        storyteller.improve_until_acceptable,
        session["user_id"],
        session["story_id"],
        session["active_request"],
        revised,
        session["classification"],
        session["recent_stories"],
    )
    await websocket.send_json(
        {
            "type": "story",
            "story": session["story"],
            "story_id": session["story_id"],
            "motif": session["motif"],
            "review": latest_review(session["user_id"], session["story_id"]),
        }
    )


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/narration/status")
async def narration_status() -> Dict[str, object]:
    available = await run_blocking(ensure_tts_service)
    return {
        "available": available,
        "narrator": "chatterbox-nano" if available else "browser-fallback",
    }


def request_local_narration(text: str) -> bytes:
    request = Request(
        f"{TTS_URL}/narrate",
        data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=900) as response:
        return response.read()


@app.post("/api/narration")
async def create_narration(payload: Dict) -> Response:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Story text is required.")
    if len(text) > 15_000:
        raise HTTPException(status_code=400, detail="Story is too long to narrate.")
    if not await run_blocking(ensure_tts_service):
        raise HTTPException(status_code=503, detail="Local narrator is unavailable.")
    try:
        audio = await run_blocking(request_local_narration, text)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        logger.warning("Local narration failed; browser fallback will be used: %s", error)
        raise HTTPException(status_code=503, detail="Local narrator could not finish.") from error
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"X-Narrator": "chatterbox-nano", "Cache-Control": "private, max-age=86400"},
    )


@app.websocket("/ws/story")
async def story_socket(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if origin and origin not in ALLOWED_WEBSOCKET_ORIGINS:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    session: Dict = {}
    try:
        while True:
            payload = await websocket.receive_json()
            action = payload.get("action")
            try:
                if action == "start":
                    await start_story(websocket, payload, session)
                elif action == "feedback":
                    await continue_story(websocket, payload, session)
                else:
                    await websocket.send_json({"type": "error", "message": "Unknown action."})
            except Exception:
                logger.exception("Story WebSocket turn failed")
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "I couldn’t finish that turn. Check the server, API key, and quota, then try again.",
                    }
                )
    except WebSocketDisconnect:
        return


DIST_DIR = Path(__file__).resolve().parent / "frontend" / "dist"
if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="frontend")
