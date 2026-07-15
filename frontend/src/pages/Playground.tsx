/**
 * Playground — a real chat, not a form.
 *
 * Modelled on Claude's UI because that's what a chat should feel like:
 *
 *   • empty state = centered greeting + one big composer, with the model picker
 *     INSIDE the composer (bottom-right) and starter prompt chips;
 *   • once you send, it becomes a message thread with the composer pinned below;
 *   • responses STREAM token by token — the whole reason chatStream exists;
 *   • Enter sends, Shift+Enter is a newline; Stop aborts a stream mid-flight;
 *   • multi-turn: the FULL history is sent each time, so follow-ups have context.
 *
 * Every call goes through the gateway with the user's own keys — this page is
 * also still the fastest way to confirm a new key actually works.
 */

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { api, ApiError } from "../lib/api";
import { Empty, Spinner } from "../components/ui";

interface Msg {
  role: "user" | "assistant";
  content: string;
  model?: string;      // which model answered (assistant only)
  error?: boolean;
}

const STARTERS = [
  "In one sentence: what is a rate limit?",
  "Write a haiku about load balancing",
  "Explain fallback routing like I'm five",
];

export default function Playground() {
  const models = useQuery({ queryKey: ["my-models"], queryFn: api.myModels });

  // "Try" links on the Models page arrive as /playground?model=<name>.
  const [params] = useSearchParams();
  const requested = params.get("model") ?? "";

  const [model, setModel] = useState("");
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [streaming, setStreaming] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);
  const textRef = useRef<HTMLTextAreaElement | null>(null);

  // Only chat models can answer here — embedding/image/audio models belong
  // to their own /v1 surfaces.
  const usable = (models.data?.models ?? []).filter(
    (m) => m.is_usable && m.mode === "chat",
  );

  // Default once models load: the ?model= from a "Try" link if it's actually
  // usable, otherwise the first usable model.
  useEffect(() => {
    if (model || usable.length === 0) return;
    const match = requested && usable.find((m) => m.model === requested);
    setModel(match ? match.model : usable[0].model);
  }, [model, usable, requested]);

  // Keep the newest tokens in view while a response streams in.
  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [messages]);

  async function send(text?: string) {
    const content = (text ?? input).trim();
    if (!content || streaming || !model) return;

    const history = [...messages, { role: "user" as const, content }];
    setMessages([...history, { role: "assistant", content: "", model }]);
    setInput("");
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await api.chatStream(
        {
          model,
          // Full history → multi-turn context. Error bubbles are UI-only and
          // must not be replayed to the model.
          messages: history
            .filter((m) => !m.error)
            .map(({ role, content }) => ({ role, content })),
        },
        (delta) =>
          setMessages((cur) => {
            const next = [...cur];
            next[next.length - 1] = {
              ...next[next.length - 1],
              content: next[next.length - 1].content + delta,
            };
            return next;
          }),
        controller.signal,
      );
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        // User hit Stop: keep whatever already streamed in.
      } else {
        const msg = e instanceof ApiError ? e.message : String(e);
        setMessages((cur) => {
          const next = [...cur];
          next[next.length - 1] = { role: "assistant", content: msg, model, error: true };
          return next;
        });
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
      textRef.current?.focus();
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  if (models.isLoading) return <Spinner />;

  if (usable.length === 0) {
    return (
      <Empty
        title="No live models"
        body="You need at least one working provider key before you can chat."
        action={<Link to="/providers" className="btn-pri">Add a provider key</Link>}
      />
    );
  }

  const empty = messages.length === 0;

  const composer = (
    <div className="rounded-2xl border border-neutral-700 bg-neutral-900/70 p-3 shadow-lg">
      <textarea
        ref={textRef}
        className="max-h-48 min-h-[3.25rem] w-full resize-none bg-transparent px-2 pt-1 text-sm
                   text-neutral-100 outline-none placeholder:text-neutral-600"
        placeholder="How can I help you today?"
        value={input}
        autoFocus
        rows={empty ? 2 : 1}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className="mt-2 flex items-center justify-between">
        <span className="px-2 text-[11px] text-neutral-600">
          Enter to send · Shift+Enter for a new line
        </span>
        <div className="flex items-center gap-2">
          {/* The model picker lives IN the composer, like Claude's — switching
              models mid-conversation is a normal move, not a settings trip. */}
          <select
            className="max-w-56 rounded-lg border border-transparent bg-transparent px-2 py-1.5
                       text-xs text-neutral-400 outline-none hover:border-neutral-700
                       hover:text-neutral-200"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            title="Which of your models answers"
          >
            {usable.map((m) => (
              <option key={m.model} value={m.model} className="bg-neutral-900">
                {m.model} · {m.providers.join(", ")}
              </option>
            ))}
          </select>
          {streaming ? (
            <button className="btn-sec" onClick={stop}>■ Stop</button>
          ) : (
            <button
              className="btn-pri disabled:opacity-40"
              disabled={!input.trim()}
              onClick={() => send()}
            >
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  );

  // ── EMPTY: centered greeting + composer, Claude-style ──
  if (empty) {
    return (
      <div className="flex min-h-[70vh] flex-col items-center justify-center">
        <div className="w-full max-w-2xl">
          <h1 className="mb-8 text-center font-serif text-3xl text-neutral-200">
            <span className="mr-3 text-emerald-500">✳</span>
            What shall we try?
          </h1>
          {composer}
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {STARTERS.map((s) => (
              <button
                key={s}
                className="rounded-full border border-neutral-800 px-3 py-1.5 text-xs
                           text-neutral-400 hover:border-neutral-600 hover:text-neutral-200"
                onClick={() => send(s)}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ── CHATTING: thread + pinned composer ──
  return (
    <div className="mx-auto flex h-[calc(100vh-8.5rem)] max-w-3xl flex-col">
      <div className="mb-3 flex items-center justify-between">
        <h1 className="text-sm font-medium text-neutral-400">Playground</h1>
        <button
          className="text-xs text-neutral-500 hover:text-neutral-300"
          onClick={() => { stop(); setMessages([]); }}
        >
          + New chat
        </button>
      </div>

      <div ref={threadRef} className="flex-1 space-y-5 overflow-y-auto pb-4 pr-1">
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
            {m.role === "user" ? (
              <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-sm
                              bg-neutral-800 px-4 py-2.5 text-sm text-neutral-100">
                {m.content}
              </div>
            ) : (
              <div className="max-w-[92%]">
                <div className="mb-1 text-[11px] text-neutral-600">
                  {m.error ? "error" : m.model}
                </div>
                <div
                  className={`whitespace-pre-wrap text-sm leading-relaxed ${
                    m.error
                      ? "rounded-xl border border-red-900/60 bg-red-950/30 px-4 py-2.5 text-red-400"
                      : "text-neutral-200"
                  }`}
                >
                  {m.content || (
                    <span className="inline-flex gap-1 text-neutral-600">
                      <span className="animate-pulse">●</span>
                      <span className="animate-pulse [animation-delay:150ms]">●</span>
                      <span className="animate-pulse [animation-delay:300ms]">●</span>
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {composer}
    </div>
  );
}
