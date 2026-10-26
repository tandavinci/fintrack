import { useState } from "react";
import { Activity, Bot, FlaskConical, LineChart, MessagesSquare, Server } from "lucide-react";
import { GOLDEN_TICKERS } from "@/lib/api";
import { cn } from "@/lib/utils";
import { SignalsPage } from "@/pages/SignalsPage";
import { ModelStatusPage } from "@/pages/ModelStatusPage";
import { AgentDebatePage } from "@/pages/AgentDebatePage";
import { BacktestLabPage } from "@/pages/BacktestLabPage";
import { AdvisorPage } from "@/pages/AdvisorPage";

/** The views the nav shell switches between (no router — a simple state switch). */
type View = "signals" | "advisor" | "model" | "debate" | "backtest";

const NAV: { id: View; label: string; Icon: typeof Activity }[] = [
  { id: "signals", label: "Signals", Icon: LineChart },
  { id: "advisor", label: "Advisor", Icon: Bot },
  { id: "model", label: "Model Status", Icon: Server },
  { id: "debate", label: "Agent Debate", Icon: MessagesSquare },
  { id: "backtest", label: "Backtest Lab", Icon: FlaskConical },
];

export default function App() {
  const [view, setView] = useState<View>("signals");
  // Ticker lives in the shell so switching pages keeps the same symbol. The
  // pages that need it (Signals, Agent Debate, Backtest) receive it; Model
  // Status is global and ignores it.
  const [ticker, setTicker] = useState<string>(GOLDEN_TICKERS[0]);

  return (
    <div className="mx-auto min-h-full w-full max-w-6xl px-4 py-8 sm:px-6 lg:py-12">
      <Header view={view} onView={setView} />

      <main className="mt-8">
        {view === "signals" && (
          <SignalsPage ticker={ticker} onTicker={setTicker} />
        )}
        {view === "advisor" && <AdvisorPage />}
        {view === "model" && <ModelStatusPage />}
        {view === "debate" && (
          <AgentDebatePage ticker={ticker} onTicker={setTicker} />
        )}
        {view === "backtest" && (
          <BacktestLabPage ticker={ticker} onTicker={setTicker} />
        )}
      </main>

      <footer className="mt-12 text-center text-xs text-muted">
        FinTrack — decision support only, not financial advice. Paper positions
        only.
      </footer>
    </div>
  );
}

function Header({
  view,
  onView,
}: {
  view: View;
  onView: (v: View) => void;
}) {
  return (
    <header className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand/15 text-brand">
          <Activity className="h-6 w-6" />
        </span>
        <div>
          <h1 className="text-xl font-extrabold tracking-tight text-white">
            FinTrack
          </h1>
          <p className="text-xs text-muted">Local trading intelligence</p>
        </div>
      </div>

      <nav className="flex flex-wrap gap-1 rounded-xl border border-border bg-surface-2/40 p-1">
        {NAV.map(({ id, label, Icon }) => {
          const active = view === id;
          return (
            <button
              key={id}
              onClick={() => onView(id)}
              className={cn(
                "flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-brand text-white shadow-glow"
                  : "text-muted hover:text-white"
              )}
              aria-current={active ? "page" : undefined}
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          );
        })}
      </nav>
    </header>
  );
}
