import { useState, useEffect } from "react";

const RULE_NAMES = [
  "COORDINATED_WALLETS", "WHALE_NEW_ACCOUNT", "VOLUME_SPIKE",
  "PRE_ANNOUNCEMENT", "IMPROBABLE_BET", "PRICE_REVERSAL_AFTER_SPIKE",
  "BET_AGAINST_CONSENSUS", "HIGH_WIN_RATE_WHALE",
];

function timeAgo(iso) {
  if (!iso) return "";
  const d = Date.now() - new Date(iso).getTime();
  const m = Math.floor(d / 60000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export default function Monitor() {
  const [trades, setTrades] = useState([]);
  const [pipeline, setPipeline] = useState([]);
  const [loading, setLoading] = useState(true);

  async function fetchAll() {
    try {
      const [trRes, pipRes] = await Promise.all([
        fetch("/api/activity/recent-trades?limit=50"),
        fetch("/api/activity/feed?limit=100"),
      ]);
      setTrades(await trRes.json());
      const allEvents = await pipRes.json();
      setPipeline(
        allEvents.filter((e) =>
          ["large_trade", "trade_evaluated", "trade_flagged", "ai_analysis", "ai_error", "trade_skipped", "signal_detected"].includes(e.event_type)
        )
      );
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchAll();
    const i = setInterval(fetchAll, 15000);
    return () => clearInterval(i);
  }, []);

  if (loading) return <div className="text-gray-500 py-12 text-center">Loading...</div>;

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-bold text-gray-100">Monitor</h1>

      {/* Recent Trades Table */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-sm font-medium text-gray-400 mb-3">
          Trades in DB ({trades.length} most recent, all &gt;= MIN_TRADE_USD)
        </h2>
        {trades.length === 0 ? (
          <div className="text-gray-500 text-sm py-4 text-center">No trades yet</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-gray-500 uppercase border-b border-gray-800">
                  <th className="pb-2 pr-3">Time</th>
                  <th className="pb-2 pr-3">Market</th>
                  <th className="pb-2 pr-3">Side</th>
                  <th className="pb-2 pr-3">Outcome</th>
                  <th className="pb-2 pr-3">Price</th>
                  <th className="pb-2 pr-3">Size</th>
                  <th className="pb-2 pr-3">USD</th>
                  <th className="pb-2">Wallet</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => (
                  <tr key={t.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="py-1.5 pr-3 text-gray-500 whitespace-nowrap">{timeAgo(t.timestamp)}</td>
                    <td className="py-1.5 pr-3 text-gray-300 max-w-[250px] truncate">{t.question || t.market_id?.slice(0, 16)}</td>
                    <td className={`py-1.5 pr-3 font-medium ${t.side === "BUY" ? "text-emerald-400" : "text-red-400"}`}>{t.side}</td>
                    <td className={`py-1.5 pr-3 ${t.outcome === "YES" ? "text-emerald-400" : "text-red-400"}`}>{t.outcome}</td>
                    <td className="py-1.5 pr-3 tabular-nums text-gray-300">{t.price?.toFixed(3)}</td>
                    <td className="py-1.5 pr-3 tabular-nums text-gray-400">{t.size?.toFixed(0)}</td>
                    <td className="py-1.5 pr-3 tabular-nums text-gray-200 font-medium">${t.usd_value?.toLocaleString()}</td>
                    <td className="py-1.5 text-gray-500 font-mono text-[10px]">{t.wallet?.slice(0, 10)}...</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Detection Pipeline */}
      <div>
        <h2 className="text-sm font-medium text-gray-400 mb-3">Detection Pipeline</h2>
        {pipeline.length === 0 ? (
          <div className="text-gray-500 text-sm py-4 text-center">No evaluations yet</div>
        ) : (
          <div className="space-y-2">
            {pipeline.map((e) => (
              <PipelineCard key={e.id} event={e} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PipelineCard({ event }) {
  const [expanded, setExpanded] = useState(false);
  const e = event;
  const m = e.metadata || {};

  const borderColor = {
    large_trade: "border-l-blue-500",
    trade_evaluated: "border-l-gray-600",
    trade_flagged: "border-l-yellow-500",
    ai_analysis: "border-l-purple-500",
    ai_error: "border-l-red-700",
    trade_skipped: "border-l-orange-500",
    signal_detected: "border-l-red-500",
  }[e.event_type] || "border-l-gray-600";

  const bgColor = {
    large_trade: "bg-blue-500/5",
    trade_evaluated: "bg-gray-800/30",
    trade_flagged: "bg-yellow-500/5",
    ai_analysis: "bg-purple-500/5",
    ai_error: "bg-red-700/10",
    trade_skipped: "bg-orange-500/5",
    signal_detected: "bg-red-500/5",
  }[e.event_type] || "bg-gray-800/30";

  const icon = {
    large_trade: "💰",
    trade_evaluated: "🔎",
    trade_flagged: "🚨",
    ai_analysis: "🤖",
    ai_error: "❌",
    trade_skipped: "⏭️",
    signal_detected: "📊",
  }[e.event_type] || "📋";

  return (
    <div
      className={`border-l-2 ${borderColor} ${bgColor} rounded-r-lg px-3 py-2 cursor-pointer`}
      onClick={() => setExpanded(!expanded)}
    >
      <div className="flex items-start gap-2">
        <span>{icon}</span>
        <div className="flex-1 min-w-0">
          <div className="text-sm text-gray-200 font-medium">{e.title}</div>
          {e.detail && <div className="text-xs text-gray-400 mt-0.5">{e.detail}</div>}

          {/* Inline tags */}
          <div className="flex gap-2 mt-1 text-xs flex-wrap">
            {m.outcome && (
              <span className={m.outcome === "YES" ? "text-emerald-400" : "text-red-400"}>{m.outcome}</span>
            )}
            {m.side && <span className="text-gray-500">{m.side}</span>}
            {m.price != null && <span className="text-gray-500">@{m.price.toFixed(3)}</span>}
            {m.topic && <span className="text-purple-400">{m.topic}</span>}
            {m.score != null && <span className="text-yellow-400">Score: {m.score}</span>}
            {m.recommendation && (
              <span className={
                m.recommendation === "STRONG_BUY" ? "text-emerald-400 font-medium" :
                m.recommendation === "BUY" ? "text-blue-400" :
                m.recommendation === "SKIP" ? "text-red-400" : "text-gray-400"
              }>{m.recommendation}</span>
            )}
          </div>

          {/* Expanded: rule results */}
          {expanded && m.rule_results && (
            <div className="mt-2 bg-gray-900/50 rounded p-2 text-xs">
              <div className="text-gray-500 uppercase text-[10px] mb-1 font-medium">Rule Results</div>
              {Object.entries(m.rule_results).map(([rule, result]) => (
                <div key={rule} className="flex items-center gap-2 py-0.5">
                  <span className={result === "MATCHED" ? "text-emerald-400" : "text-gray-600"}>
                    {result === "MATCHED" ? "✅" : "❌"}
                  </span>
                  <span className="text-gray-400 font-mono">{rule}</span>
                  <span className="text-gray-600">{result !== "MATCHED" ? result : ""}</span>
                </div>
              ))}
            </div>
          )}

          {/* Expanded: AI investigation report */}
          {expanded && e.event_type === "ai_analysis" && (
            <div className="mt-2 bg-purple-900/20 border border-purple-500/20 rounded p-3 text-xs space-y-2">
              <div className="text-purple-400 uppercase text-[10px] font-bold">AI Investigation Report</div>
              {m.insider_score != null && (
                <div className="flex gap-4 text-sm">
                  <span>Insider Score: <span className={`font-bold ${m.insider_score >= 7 ? "text-red-400" : m.insider_score >= 5 ? "text-yellow-400" : "text-emerald-400"}`}>{m.insider_score}/10</span></span>
                  <span>Confidence: <span className="font-bold text-gray-200">{(m.confidence * 100).toFixed(0)}%</span></span>
                  {m.cost_usd != null && <span className="text-gray-500">Cost: ${m.cost_usd}</span>}
                  {m.input_tokens && <span className="text-gray-500">Tokens: {m.input_tokens + (m.output_tokens || 0)}</span>}
                </div>
              )}
              {m.key_findings && m.key_findings.length > 0 && (
                <div>
                  <div className="text-gray-500 text-[10px] uppercase mb-0.5">Key Findings</div>
                  <ul className="list-disc list-inside text-gray-300 space-y-0.5">
                    {m.key_findings.map((f, i) => <li key={i}>{f}</li>)}
                  </ul>
                </div>
              )}
              {m.upcoming_event && (
                <div className="text-yellow-400">Upcoming event: {m.upcoming_event}</div>
              )}
              {m.news_justification != null && (
                <div className={m.news_justification ? "text-emerald-400" : "text-red-400"}>
                  News justification: {m.news_justification ? "Yes — public info explains this trade" : "No — no public justification found"}
                </div>
              )}
              {e.detail && (
                <div className="text-gray-300 whitespace-pre-wrap leading-relaxed border-t border-gray-700 pt-2 mt-2">
                  {e.detail}
                </div>
              )}
            </div>
          )}

          {/* AI error */}
          {expanded && e.event_type === "ai_error" && (
            <div className="mt-2 bg-red-900/20 border border-red-500/20 rounded p-2 text-xs text-red-300">
              {e.detail}
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-1">
          <span className="text-xs text-gray-600">{timeAgo(e.timestamp)}</span>
          {m.rule_results && (
            <span className="text-[10px] text-gray-600">{expanded ? "▲" : "▼"} rules</span>
          )}
        </div>
      </div>
    </div>
  );
}
