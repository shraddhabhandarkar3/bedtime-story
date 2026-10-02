import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";

type Theme = "day" | "night";
type MotionName = "fly" | "float" | "bob" | "walk" | "swim" | "twinkle" | "sway" | "settle";

type Motif = {
  glyphs: string[];
  motion: MotionName;
  mood: "gentle" | "playful" | "dreamy" | "cozy";
  accessible_label: string;
};

type Review = {
  safe?: boolean;
  overall_score?: number;
  word_count?: number;
  estimated_read_aloud_minutes?: number;
  scores?: Record<string, number>;
  preserve?: string[];
  changes?: string[];
  story_visual?: Motif;
};

type ChatMessage = {
  role: "parent" | "storyteller";
  content: string;
};

type PastStory = {
  story_id: string;
  content: string;
  category?: string;
  created_at?: string;
};

type NarrationSpeed = "calm" | "normal";
type NarrationState = "idle" | "preparing" | "speaking" | "paused";
type NarrationEngine = "chatterbox" | "browser" | null;

const defaultMotif: Motif = {
  glyphs: ["✨", "🌙", "✨"],
  motion: "float",
  mood: "dreamy",
  accessible_label: "Golden sparks floating around a quiet moon",
};

function socketUrl() {
  const configured = import.meta.env.VITE_WS_URL as string | undefined;
  if (configured) return configured;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/story`;
}

function GlyphStage({ motif, status, compact = false }: { motif: Motif; status: string; compact?: boolean }) {
  const glyphs = motif.glyphs.length ? motif.glyphs : defaultMotif.glyphs;
  return (
    <div className={`glyph-stage mood-${motif.mood} ${compact ? "compact" : ""}`}>
      <div className="pixel-moon" aria-hidden="true" />
      <div className="pixel-cloud cloud-one" aria-hidden="true" />
      <div className="pixel-cloud cloud-two" aria-hidden="true" />
      <div className="glyph-field" aria-label={motif.accessible_label} role="img">
        {glyphs.map((glyph, index) => (
          <span
            className={`glyph glyph-${motif.motion} glyph-${index + 1}`}
            style={{ animationDelay: `${index * -0.65}s` }}
            aria-hidden="true"
            key={`${glyph}-${index}`}
          >
            {glyph}
          </span>
        ))}
      </div>
      <div className="horizon" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      {status && <p className="stage-status" role="status">{status}</p>}
    </div>
  );
}

function StoryVignette({ motif }: { motif: Motif }) {
  const glyphs = motif.glyphs.length ? motif.glyphs : defaultMotif.glyphs;
  return (
    <div
      className={`story-vignette mood-${motif.mood}`}
      aria-label={motif.accessible_label}
      role="img"
    >
      <span className="vignette-star star-one" aria-hidden="true">✦</span>
      <span className="vignette-star star-two" aria-hidden="true">·</span>
      <span className="vignette-star star-three" aria-hidden="true">✦</span>
      <div className="vignette-characters" aria-hidden="true">
        {glyphs.slice(0, 4).map((glyph, index) => (
          <span
            className={`vignette-character vignette-motion-${motif.motion} vignette-character-${index + 1}`}
            style={{ animationDelay: `${index * -0.7}s` }}
            key={`${glyph}-${index}`}
          >
            {glyph}
          </span>
        ))}
      </div>
      <div className="vignette-landscape" aria-hidden="true">
        <span />
        <span />
      </div>
    </div>
  );
}

function SkyBackdrop() {
  return (
    <div className="sky-backdrop" aria-hidden="true">
      <div className="sun" />
      <div className="moon" />
      <div className="stars">
        {Array.from({ length: 12 }, (_, index) => <i key={index} />)}
      </div>
      <div className="sky-cloud cloud-a"><i /><i /></div>
      <div className="sky-cloud cloud-b"><i /><i /></div>
      <div className="sky-cloud cloud-c"><i /><i /></div>
      <div className="day-birds">
        <i /><i /><i />
      </div>
      <div className="tree-line">
        {Array.from({ length: 13 }, (_, index) => <i key={index} />)}
      </div>
    </div>
  );
}

function JudgePanel({ review }: { review: Review | null }) {
  if (!review) return null;
  const labels: Record<string, string> = {
    age_fit: "Age fit",
    personalization: "Personalization",
    learning_through_action: "Learning through action",
    story_arc: "Story arc",
    bedtime_pacing: "Bedtime pacing",
    originality: "Originality",
  };
  return (
    <details className="judge-panel">
      <summary>
        <span>Behind the story</span>
        <span className="score-pill">{review.overall_score ?? "–"}/10</span>
      </summary>
      <div className="judge-content">
        <div className="story-metadata">
          <span>{review.word_count ?? "–"} words</span>
          <span>≈ {review.estimated_read_aloud_minutes ?? "–"} min aloud</span>
          <span>{review.safe ? "Safety check passed" : "Needs safety review"}</span>
        </div>
        <div className="score-grid">
          {Object.entries(review.scores ?? {}).map(([name, score]) => (
            <div className="score-row" key={name}>
              <span>{labels[name] ?? name}</span>
              <div className="score-track"><i style={{ width: `${score * 20}%` }} /></div>
              <strong>{score}/5</strong>
            </div>
          ))}
        </div>
        {review.preserve && review.preserve.length > 0 && (
          <div className="editor-note">
            <h3>What the editor kept</h3>
            <ul>{review.preserve.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
        )}
      </div>
    </details>
  );
}

function PastStories({ stories }: { stories: PastStory[] }) {
  if (!stories.length) return null;
  return (
    <section className="past-stories" aria-labelledby="past-stories-title">
      <div className="past-stories-heading">
        <h2 id="past-stories-title">Past stories</h2>
        <span>{stories.length}</span>
      </div>
      <div className="past-story-list">
        {stories.map((pastStory) => {
          const lines = pastStory.content.trim().split("\n");
          const title = lines[0]?.replace(/^#+\s*/, "").replace(/^Title:\s*/i, "") || "A bedtime story";
          const body = lines.slice(1).join("\n").trim() || pastStory.content;
          return (
            <details className="past-story" key={pastStory.story_id}>
              <summary>
                <span>{title}</span>
                <i aria-hidden="true">⌄</i>
              </summary>
              <div className="past-story-body">{body}</div>
            </details>
          );
        })}
      </div>
    </section>
  );
}

function narrationChunks(title: string, body: string) {
  const pieces = [title, ...body.split(/\n+/)]
    .flatMap((part) => part.match(/[^.!?]+[.!?]+[”'\"]?|[^.!?]+$/g) ?? [])
    .map((part) => part.trim())
    .filter(Boolean);

  return pieces.flatMap((piece) => {
    if (piece.length <= 220) return [piece];
    const words = piece.split(/\s+/);
    const chunks: string[] = [];
    let current = "";
    words.forEach((word) => {
      if (current && `${current} ${word}`.length > 220) {
        chunks.push(current);
        current = word;
      } else {
        current = current ? `${current} ${word}` : word;
      }
    });
    if (current) chunks.push(current);
    return chunks;
  });
}

function preferredEnglishVoice(voices: SpeechSynthesisVoice[]) {
  const english = voices.filter((voice) => voice.lang.toLowerCase().startsWith("en"));
  const candidates = english.length ? english : voices;
  const preferences = ["samantha", "ava", "natural", "enhanced", "premium", "google us english"];
  return [...candidates].sort((a, b) => {
    const rank = (voice: SpeechSynthesisVoice) => {
      const name = voice.name.toLowerCase();
      const index = preferences.findIndex((preference) => name.includes(preference));
      return index === -1 ? preferences.length : index;
    };
    return rank(a) - rank(b);
  })[0];
}

function NarrationBar({ title, body }: { title: string; body: string }) {
  const browserVoiceSupported = typeof window !== "undefined" && "speechSynthesis" in window;
  const [state, setState] = useState<NarrationState>("idle");
  const [speed, setSpeed] = useState<NarrationSpeed>("calm");
  const [status, setStatus] = useState("Expressive narration when available");
  const chunksRef = useRef<string[]>([]);
  const indexRef = useRef(0);
  const runRef = useRef(0);
  const engineRef = useRef<NarrationEngine>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  function cancelPlayback() {
    runRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    if (browserVoiceSupported) window.speechSynthesis.cancel();
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
      audioRef.current = null;
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
    chunksRef.current = [];
    indexRef.current = 0;
    engineRef.current = null;
  }

  function stopNarration(nextStatus = "Ready to read") {
    cancelPlayback();
    setState("idle");
    setStatus(nextStatus);
  }

  function speakNext(run: number) {
    if (!browserVoiceSupported || run !== runRef.current) return;
    const text = chunksRef.current[indexRef.current];
    if (!text) {
      engineRef.current = null;
      setState("idle");
      setStatus("Finished");
      return;
    }

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = speed === "calm" ? 0.84 : 1;
    utterance.pitch = 1;
    utterance.lang = "en-US";
    const voice = preferredEnglishVoice(window.speechSynthesis.getVoices());
    if (voice) utterance.voice = voice;
    utterance.onend = () => {
      if (run !== runRef.current) return;
      indexRef.current += 1;
      speakNext(run);
    };
    utterance.onerror = (event) => {
      if (run !== runRef.current || event.error === "canceled" || event.error === "interrupted") return;
      setState("idle");
      setStatus("Narration stopped unexpectedly");
    };
    window.speechSynthesis.speak(utterance);
  }

  function startBrowserFallback(run: number) {
    if (!browserVoiceSupported || run !== runRef.current) {
      setState("idle");
      setStatus("Narration is unavailable in this browser");
      return;
    }
    engineRef.current = "browser";
    chunksRef.current = narrationChunks(title, body);
    indexRef.current = 0;
    setState("speaking");
    setStatus(`Browser voice fallback · ${speed === "calm" ? "Reading calmly…" : "Reading aloud…"}`);
    speakNext(run);
  }

  async function startNarration() {
    cancelPlayback();
    const run = runRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    setState("preparing");
    setStatus("Preparing Chatterbox Nano…");

    try {
      const response = await fetch("/api/narration", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: `${title}\n\n${body}` }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`Narrator returned ${response.status}`);
      const audioBlob = await response.blob();
      if (run !== runRef.current) return;

      const audioUrl = URL.createObjectURL(audioBlob);
      const audio = new Audio(audioUrl);
      audio.preload = "auto";
      audio.playbackRate = speed === "calm" ? 0.9 : 1;
      audio.preservesPitch = true;
      audioRef.current = audio;
      audioUrlRef.current = audioUrl;
      engineRef.current = "chatterbox";
      audio.onended = () => {
        if (run !== runRef.current) return;
        engineRef.current = null;
        setState("idle");
        setStatus("Chatterbox Nano · Finished");
      };
      audio.onerror = () => {
        if (run !== runRef.current) return;
        startBrowserFallback(run);
      };
      try {
        await audio.play();
        if (run !== runRef.current) return;
        setState("speaking");
        setStatus(`Chatterbox Nano · ${speed === "calm" ? "Reading calmly…" : "Reading aloud…"}`);
      } catch {
        if (run !== runRef.current) return;
        setState("paused");
        setStatus("Chatterbox Nano is ready · Press Resume");
      }
    } catch (error) {
      if (controller.signal.aborted || run !== runRef.current) return;
      startBrowserFallback(run);
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }

  function togglePause() {
    if (state === "speaking") {
      if (engineRef.current === "chatterbox") audioRef.current?.pause();
      else if (browserVoiceSupported) window.speechSynthesis.pause();
      setState("paused");
      setStatus("Paused");
    } else if (state === "paused") {
      if (engineRef.current === "chatterbox") {
        audioRef.current?.play().then(() => {
          setState("speaking");
          setStatus(`Chatterbox Nano · ${speed === "calm" ? "Reading calmly…" : "Reading aloud…"}`);
        }).catch(() => setStatus("Press Resume to allow audio playback"));
      } else if (browserVoiceSupported) {
        window.speechSynthesis.resume();
        setState("speaking");
        setStatus(`Browser voice fallback · ${speed === "calm" ? "Reading calmly…" : "Reading aloud…"}`);
      }
    }
  }

  function changeSpeed(nextSpeed: NarrationSpeed) {
    if (state !== "idle") stopNarration(`Speed set to ${nextSpeed}`);
    setSpeed(nextSpeed);
  }

  useEffect(() => {
    cancelPlayback();
    setState("idle");
    setStatus("Expressive narration when available");
    return () => {
      cancelPlayback();
    };
  }, [browserVoiceSupported, title, body]);

  return (
    <section className="narration-bar" aria-label="Story narration" aria-busy={state === "preparing"}>
      <div className="narration-controls">
        <button className="narration-primary" type="button" onClick={startNarration} disabled={state === "preparing"}>
          {state === "preparing" ? "…" : "▶"} <span>{state === "preparing" ? "Preparing" : "Read aloud"}</span>
        </button>
        <button type="button" onClick={togglePause} disabled={state !== "speaking" && state !== "paused"}>
          {state === "paused" ? "▶" : "⏸"} <span>{state === "paused" ? "Resume" : "Pause"}</span>
        </button>
        <button type="button" onClick={() => stopNarration()} disabled={state === "idle"}>
          ■ <span>Stop</span>
        </button>
      </div>
      <fieldset className="speed-control">
        <legend>Speed</legend>
        <button
          type="button"
          className={speed === "calm" ? "active" : ""}
          aria-pressed={speed === "calm"}
          onClick={() => changeSpeed("calm")}
        >
          Calm
        </button>
        <button
          type="button"
          className={speed === "normal" ? "active" : ""}
          aria-pressed={speed === "normal"}
          onClick={() => changeSpeed("normal")}
        >
          Normal
        </button>
      </fieldset>
      <span className="narration-status" role="status" aria-live="polite">{status}</span>
    </section>
  );
}

function App() {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem("quiet-theme");
    if (saved === "day" || saved === "night") return saved;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "night" : "day";
  });
  const [userName, setUserName] = useState("");
  const [request, setRequest] = useState("");
  const [feedback, setFeedback] = useState("");
  const [story, setStory] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [motif, setMotif] = useState<Motif>(defaultMotif);
  const [stageMessage, setStageMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [pastStories, setPastStories] = useState<PastStory[]>([]);
  const [presentation, setPresentation] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const hasDeliveredStoryRef = useRef(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("quiet-theme", theme);
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      "content",
      theme === "night" ? "#193747" : "#e8f6f5",
    );
  }, [theme]);

  useEffect(() => () => socketRef.current?.close(), []);

  const titleAndBody = useMemo(() => {
    const lines = story.trim().split("\n");
    const title = lines[0]?.replace(/^#+\s*/, "") || "Tonight’s story";
    return { title, body: lines.slice(1).join("\n").trim() || story };
  }, [story]);

  function applyServerMessage(payload: Record<string, unknown>) {
    const nextMotif = payload.motif as Motif | undefined;
    if (nextMotif) setMotif(nextMotif);
    if (payload.type === "stage") {
      setBusy(true);
      setStageMessage(String(payload.message ?? ""));
    } else if (payload.type === "acknowledgement") {
      const content = String(payload.message ?? "");
      if (Array.isArray(payload.past_stories)) {
        setPastStories(payload.past_stories as PastStory[]);
      }
      setChat((current) => [...current, { role: "storyteller", content }]);
    } else if (payload.type === "conversation") {
      const content = String(payload.message ?? "");
      setChat((current) => [...current, { role: "storyteller", content }]);
      if (payload.intent === "discuss" || payload.intent === "clarify" || payload.intent === "accept") {
        setBusy(false);
        setStageMessage("");
      }
    } else if (payload.type === "story") {
      setStory(String(payload.story ?? ""));
      setReview((payload.review as Review | null) ?? null);
      if (!hasDeliveredStoryRef.current) {
        setChat([
          {
            role: "storyteller",
            content: "Here it is. Would you like to change anything, or does it feel right as it is?",
          },
        ]);
        hasDeliveredStoryRef.current = true;
      }
      setBusy(false);
      setStageMessage("");
    } else if (payload.type === "complete") {
      setBusy(false);
      setStageMessage("");
    } else if (payload.type === "error") {
      setError(String(payload.message ?? "Something went wrong."));
      setBusy(false);
      setStageMessage("");
    }
  }

  function connectAndStart(event: FormEvent) {
    event.preventDefault();
    const cleanName = userName.trim();
    if (!cleanName) {
      setError("Please add a name or nickname so I can remember your stories.");
      return;
    }
    setError("");
    setStory("");
    setReview(null);
    setPastStories([]);
    setChat(request.trim() ? [{ role: "parent", content: request.trim() }] : []);
    hasDeliveredStoryRef.current = false;
    setBusy(true);
    setStageMessage("Opening the storybook…");
    const socket = new WebSocket(socketUrl());
    socketRef.current = socket;
    socket.onopen = () => socket.send(JSON.stringify({ action: "start", user_name: cleanName, request }));
    socket.onmessage = (message) => applyServerMessage(JSON.parse(message.data));
    socket.onerror = () => {
      setError("I couldn’t reach the storyteller server. Make sure FastAPI is running.");
      setBusy(false);
    };
    socket.onclose = () => {
      if (busy && !story) setBusy(false);
    };
  }

  function sendFeedback(event: FormEvent) {
    event.preventDefault();
    const message = feedback.trim();
    if (!message || busy) return;
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setError("The story session closed. Start a new story to reconnect.");
      return;
    }
    setChat((current) => [...current, { role: "parent", content: message }]);
    setFeedback("");
    setBusy(true);
    setStageMessage("Listening closely…");
    socket.send(JSON.stringify({ action: "feedback", message }));
  }

  function startOver() {
    socketRef.current?.close();
    socketRef.current = null;
    setStory("");
    setReview(null);
    setPastStories([]);
    setChat([]);
    hasDeliveredStoryRef.current = false;
    setRequest("");
    setFeedback("");
    setBusy(false);
    setError("");
    setMotif(defaultMotif);
    setPresentation(false);
  }

  return (
    <main className={presentation ? "app presentation" : "app"}>
      <header className="topbar">
        <button className="brand" onClick={startOver} aria-label="Start a new story">
          <span className="brand-mark" aria-hidden="true">Q</span>
          <span><strong>Quiet</strong><small>Storyteller</small></span>
        </button>
        <div className="header-actions">
          {story && (
            <button className="icon-button text-button" onClick={() => setPresentation(!presentation)}>
              {presentation ? "Exit focus" : "Focus mode"}
            </button>
          )}
          <button
            className="icon-button"
            onClick={() => setTheme(theme === "day" ? "night" : "day")}
            aria-label={`Switch to ${theme === "day" ? "night" : "day"} mode`}
          >
            {theme === "day" ? "☾" : "☀"}
          </button>
        </div>
      </header>

      <SkyBackdrop />

      {!story && !busy && (
        <motion.section className="welcome-layout" initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }}>
          <div className="welcome-copy">
            <span className="eyebrow">Quiet stories for curious kids</span>
            <h1>A little story.<br /><em>Made for tonight.</em></h1>
            <p>Personal, gentle, and checked before bedtime.</p>
            <div className="trust-row">
              <span>✦ Ages 5–10</span>
              <span>☾ Calm read-aloud</span>
            </div>
          </div>
          <form className="story-form" onSubmit={connectAndStart}>
            <div className="form-heading">
              <div><h2>Let’s make tonight’s story.</h2><p>A few details are enough.</p></div>
            </div>
            <label htmlFor="parent-name">
              Your name or nickname
              <input id="parent-name" value={userName} onChange={(event) => setUserName(event.target.value)} autoComplete="name" />
            </label>
            <label htmlFor="story-request">
              Tell me about the child or tonight’s story
              <span className="field-hint" id="story-request-help">
                Share their name, age, interests, how their day felt, or something they’re learning. Leave this blank for a surprise.
              </span>
              <textarea
                id="story-request"
                aria-describedby="story-request-help"
                value={request}
                onChange={(event) => setRequest(event.target.value)}
                rows={5}
              />
            </label>
            <button className="primary-button" type="submit">
              Make a story <span aria-hidden="true">→</span>
            </button>
          </form>
        </motion.section>
      )}

      {busy && !story && (
        <motion.section className="waiting-layout" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <GlyphStage motif={motif} status={stageMessage} />
          {chat.length > 0 && <p className="acknowledgement">{chat[chat.length - 1].content}</p>}
        </motion.section>
      )}

      {story && (
        <motion.section className="reader-layout" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          {!presentation && (
            <aside className="conversation-rail">
              <div className="chat-list" aria-live="polite">
                <AnimatePresence initial={false}>
                  {chat.slice(-6).map((message, index) => (
                    <motion.div
                      className={`chat-bubble ${message.role}`}
                      key={`${message.role}-${index}-${message.content}`}
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                    >
                      {message.content}
                    </motion.div>
                  ))}
                </AnimatePresence>
              </div>
              <form className="feedback-form" onSubmit={sendFeedback}>
                <label htmlFor="feedback">Talk to the storyteller</label>
                <textarea
                  id="feedback"
                  value={feedback}
                  onChange={(event) => setFeedback(event.target.value)}
                  rows={3}
                  disabled={busy}
                />
                <button className="primary-button" type="submit" disabled={busy || !feedback.trim()}>
                  {busy ? "Thinking…" : "Send"}
                </button>
              </form>
              <PastStories stories={pastStories} />
            </aside>
          )}

          <article className="story-card">
            <div className="story-kicker"><span /> Tonight’s story <span /></div>
            <StoryVignette motif={review?.story_visual ?? motif} />
            <h1>{titleAndBody.title}</h1>
            <NarrationBar title={titleAndBody.title} body={titleAndBody.body} />
            <div className="story-prose">{titleAndBody.body}</div>
            <div className="story-end" aria-label="The end"><span>✦</span><span>☾</span><span>✦</span></div>
            {!presentation && <JudgePanel review={review} />}
          </article>
        </motion.section>
      )}

      {error && (
        <div className="error-toast" role="alert">
          <span>{error}</span><button onClick={() => setError("")} aria-label="Dismiss">×</button>
        </div>
      )}
    </main>
  );
}

export default App;
