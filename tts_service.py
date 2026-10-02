"""Optional local Chatterbox Nano service for expressive story narration.

Run this file with the dedicated Python 3.11 environment. The main FastAPI app
starts it on demand when that environment is present, and otherwise lets the
frontend fall back to the browser's speech synthesizer.
"""

import hashlib
import io
import re
import threading
from pathlib import Path
from typing import List

import soundfile as sf
import torch
from chatterbox.tts_turbo import ChatterboxTurboTTS
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent
AUDIO_CACHE = ROOT / ".tts-cache" / "audio"
CACHE_VERSION = "v2-trimmed-silence"
AUDIO_CACHE.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Quiet Storyteller Narrator")
_model = None
_model_lock = threading.Lock()


class NarrationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=15_000)


def split_for_narration(text: str, limit: int = 280) -> List[str]:
    """Split prose at paragraph and sentence boundaries for stable synthesis."""
    sentences = []
    for paragraph in re.split(r"\n+", text):
        sentences.extend(re.findall(r"[^.!?]+[.!?]+[\"'’”]?|[^.!?]+$", paragraph))

    chunks: List[str] = []
    current = ""
    for sentence in (part.strip() for part in sentences if part.strip()):
        if len(sentence) > limit:
            words = sentence.split()
            for word in words:
                candidate = f"{current} {word}".strip()
                if current and len(candidate) > limit:
                    chunks.append(current)
                    current = word
                else:
                    current = candidate
        else:
            candidate = f"{current} {sentence}".strip()
            if current and len(candidate) > limit:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def get_model():
    global _model
    if _model is None:
        # Nano is designed for constrained machines and officially supports CPU.
        # CPU is more predictable than MPS across PyTorch/Chatterbox versions.
        _model = ChatterboxTurboTTS.from_pretrained(device="cpu", nano=True)
    return _model


def trim_trailing_silence(clip: torch.Tensor, sample_rate: int) -> torch.Tensor:
    """Remove model-generated silent tails while preserving a natural pause."""
    level = clip.abs().amax(dim=0)
    active = torch.nonzero(level > 0.0015, as_tuple=False).flatten()
    if active.numel() == 0:
        return clip
    natural_tail = int(sample_rate * 0.35)
    end = min(int(active[-1]) + natural_tail, clip.shape[-1])
    return clip[..., :end]


def synthesize(text: str) -> bytes:
    cache_key = hashlib.sha256(f"{CACHE_VERSION}\n{text}".encode("utf-8")).hexdigest()
    cache_path = AUDIO_CACHE / f"{cache_key}.wav"
    if cache_path.exists():
        return cache_path.read_bytes()

    with _model_lock:
        if cache_path.exists():
            return cache_path.read_bytes()
        model = get_model()
        clips = []
        for chunk in split_for_narration(text):
            clip = model.generate(
                chunk,
                temperature=0.75,
                top_p=0.92,
            ).detach().cpu()
            clip = trim_trailing_silence(clip, model.sr)
            clips.append(clip)
            clips.append(torch.zeros((clip.shape[0], int(model.sr * 0.24))))

        if not clips:
            raise ValueError("The story did not contain narratable text.")
        audio = torch.cat(clips[:-1], dim=-1).squeeze().numpy()
        output = io.BytesIO()
        sf.write(output, audio, model.sr, format="WAV", subtype="PCM_16")
        wav_bytes = output.getvalue()
        cache_path.write_bytes(wav_bytes)
        return wav_bytes


@app.get("/health")
def health():
    return {"status": "ok", "narrator": "chatterbox-nano", "model_loaded": _model is not None}


@app.post("/narrate")
def narrate(request: NarrationRequest):
    try:
        wav_bytes = synthesize(request.text.strip())
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Narration failed: {error}") from error
    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"X-Narrator": "chatterbox-nano", "Cache-Control": "private, max-age=86400"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8768, log_level="warning")
