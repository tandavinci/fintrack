import { useRef, useState } from "react";
import { Bot, Loader2, LogIn, Send, TriangleAlert, User } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import {
  askAdvisor,
  type AdvisorFailureKind,
  type AdvisorTurn,
  type GroundedOn,
} from "@/lib/api";

/**
 * Advisor chat — a grounded conversation about the stored portfolio. Every
 * answer comes from the backend advisor, which feeds the local model ONLY the
 * user's real holdings + stored quant/deep recommendations (models explain,
 * code computes). Each answer shows the provenance it was grounded on.
 *
 * The endpoint is auth-guarded; the demo frontend has no auth UI yet, so a
 * 401/403 degrades to a friendly "sign in" note and a 503 to an amber
 * "model unavailable" note — the chat never hard-errors.
 */

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  grounded_on?: GroundedOn;
}

const SUGGESTIONS = [
  "What is in my portfolio?",
  "What's the latest recommendation for my holdings?",
  "Which of my holdings has a SELL signal?",
];

export function AdvisorPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<AdvisorFailureKind | null>(null);
  const [failureDetail, setFailureDetail] = useState<string | null>(null);
  const listEndRef = useRef<HTMLDivElement>(null);

  async function send(question: string) {
    const q = question.trim();
    if (!q || loading) return;

    setFailure(null);
    setFailureDetail(null);
    const nextMessages: ChatMessage[] = [
      ...messages,
      { role: "user", content: q },
    ];
    setMessages(nextMessages);
    setInput("");
    setLoading(true);

    // Replay prior turns (exclude the just-added question) for continuity.
    const history: AdvisorTurn[] = messages.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    const result = await askAdvisor(q, history);
    setLoading(false);

    if (result.ok) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.data.answer || "(no answer returned)",
          grounded_on: result.data.grounded_on,
        },
      ]);
    } else {
      setFailure(result.kind);
      setFailureDetail(result.detail ?? null);
    }

    // Scroll the newest message into view after the DOM updates.
    requestAnimationFrame(() =>
      listEndRef.current?.scrollIntoView({ behavior: "smooth" })
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-white">Advisor</h2>
          <p className="text-xs text-muted">
            Grounded in your stored holdings &amp; recommendations
          </p>
        </div>
      </div>

      <Card className="mt-5 flex flex-col gap-4">
        <div className="flex max-h-[28rem] min-h-[16rem] flex-col gap-4 overflow-y-auto pr-1">
          {messages.length === 0 && !loading && (
            <EmptyState onPick={send} />
          )}

          {messages.map((m, i) => (
            <MessageBubble key={i} message={m} />
          ))}

          {loading && (
            <div className="flex items-center gap-2 text-sm text-muted">
              <Loader2 className="h-4 w-4 animate-spin text-brand" />
              Thinking…
            </div>
          )}

          <div ref={listEndRef} />
        </div>

        {failure && (
          <FailureNote kind={failure} detail={failureDetail} />
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
          className="flex items-center gap-2"
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about your portfolio…"
            disabled={loading}
            className="h-11 flex-1 rounded-xl border border-border bg-surface-2 px-4 text-sm text-white placeholder:text-muted focus:border-brand focus:outline-none disabled:opacity-50"
            aria-label="Ask the advisor a question"
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand text-white transition-colors hover:bg-brand/90 disabled:opacity-50"
            title="Send"
            aria-label="Send"
          >
            <Send className="h-4 w-4" />
          </button>
        </form>
      </Card>

      <p className="mt-3 text-center text-xs text-muted">
        The advisor only uses your stored data and will say so when it lacks
        the facts. Decision support only, not financial advice.
      </p>
    </div>
  );
}

function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-10 text-center text-muted">
      <Bot className="h-8 w-8 text-muted" />
      <p className="max-w-md text-sm">
        Ask about your holdings and their latest stored recommendations.
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => onPick(s)}
            className="rounded-full border border-border bg-surface-2/60 px-3 py-1.5 text-xs text-slate-200 transition-colors hover:border-brand hover:text-white"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={cn("flex gap-3", isUser && "flex-row-reverse")}>
      <span
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
          isUser ? "bg-surface-2 text-muted" : "bg-brand/15 text-brand"
        )}
      >
        {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
      </span>
      <div className={cn("flex max-w-[80%] flex-col gap-1", isUser && "items-end")}>
        <div
          className={cn(
            "rounded-xl px-4 py-2.5 text-sm leading-relaxed",
            isUser
              ? "bg-brand text-white"
              : "whitespace-pre-wrap bg-surface-2 text-slate-200"
          )}
        >
          {message.content}
        </div>
        {!isUser && message.grounded_on && (
          <span className="text-[11px] text-muted">
            based on {message.grounded_on.holdings} holding
            {message.grounded_on.holdings === 1 ? "" : "s"},{" "}
            {message.grounded_on.recommendations} recommendation
            {message.grounded_on.recommendations === 1 ? "" : "s"}
          </span>
        )}
      </div>
    </div>
  );
}

function FailureNote({
  kind,
  detail,
}: {
  kind: AdvisorFailureKind;
  detail: string | null;
}) {
  if (kind === "unauthenticated") {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-border bg-surface-2/60 px-4 py-3 text-sm text-muted">
        <LogIn className="h-4 w-4 shrink-0" />
        Sign in to use the advisor — it reads your own holdings and
        recommendations, so it needs an authenticated session.
      </div>
    );
  }
  if (kind === "model_unavailable") {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-hold/30 bg-hold/10 px-4 py-3 text-sm text-hold">
        <TriangleAlert className="h-4 w-4 shrink-0" />
        The local model is unavailable right now. Start Ollama and the gateway,
        then try again.
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 rounded-xl border border-sell/30 bg-sell/10 px-4 py-3 text-sm text-sell">
      <TriangleAlert className="h-4 w-4 shrink-0" />
      {detail ?? "Something went wrong. Please try again."}
    </div>
  );
}
