const severityStyles = {
  info: "border-l-blue-500 bg-blue-500/5",
  warning: "border-l-yellow-500 bg-yellow-500/5",
  alert: "border-l-red-500 bg-red-500/5",
  error: "border-l-red-700 bg-red-700/5",
};

const eventIcons = {
  large_trade: "💰",
  trade_evaluated: "🔎",
  trade_flagged: "🔍",
  signal_detected: "🚨",
  signal_resolved: "📊",
  ai_analysis: "🤖",
  position_opened: "📈",
  position_closed: "📉",
  trades_ingested: "📥",
  cleanup: "🧹",
  error: "❌",
};

function timeAgo(isoString) {
  if (!isoString) return "";
  const diff = Date.now() - new Date(isoString).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default function ActivityFeed({ events, stats }) {
  return (
    <div className="space-y-3">
      {/* AI Cost Stats */}
      {stats?.ai && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 flex items-center gap-4 text-xs text-gray-400 flex-wrap">
          <span>AI Calls: <span className="text-gray-200 font-medium">{stats.ai.total_calls}</span></span>
          <span>Tokens: <span className="text-gray-200 font-medium">{(stats.ai.total_input_tokens + stats.ai.total_output_tokens).toLocaleString()}</span></span>
          <span>Cost: <span className="text-emerald-400 font-medium">${stats.ai.estimated_cost_usd.toFixed(4)}</span></span>
          <span>Trades in DB: <span className="text-gray-200 font-medium">{stats.trades_in_db?.toLocaleString() || 0}</span></span>
        </div>
      )}

      {/* Event List */}
      {(!events || events.length === 0) ? (
        <div className="text-gray-500 text-sm py-8 text-center">
          No bot activity yet. The bot is monitoring trades and will log activity here when it detects something interesting.
        </div>
      ) : (
        <div className="space-y-1.5">
          {events.map((e) => (
            <div
              key={e.id}
              className={`border-l-2 rounded-r-lg px-3 py-2 ${severityStyles[e.severity] || severityStyles.info}`}
            >
              <div className="flex items-start gap-2">
                <span className="text-sm">{eventIcons[e.event_type] || "📋"}</span>
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-gray-200 font-medium">{e.title}</div>
                  {e.detail && (
                    <div className="text-xs text-gray-400 mt-0.5 line-clamp-3">{e.detail}</div>
                  )}
                  {e.metadata && (
                    <div className="flex gap-3 mt-1 text-xs text-gray-500 flex-wrap">
                      {e.metadata.score != null && <span>Score: <span className="text-gray-300 font-medium">{e.metadata.score}</span></span>}
                      {e.metadata.confidence != null && <span>Conf: <span className="text-gray-300">{(e.metadata.confidence * 100).toFixed(0)}%</span></span>}
                      {e.metadata.recommendation && (
                        <span className={`font-medium ${
                          e.metadata.recommendation === "STRONG_BUY" ? "text-emerald-400" :
                          e.metadata.recommendation === "BUY" ? "text-blue-400" :
                          e.metadata.recommendation === "SKIP" ? "text-red-400" : "text-gray-400"
                        }`}>{e.metadata.recommendation}</span>
                      )}
                      {e.metadata.outcome && (
                        <span className={e.metadata.outcome === "YES" ? "text-emerald-400" : "text-red-400"}>
                          {e.metadata.outcome}
                        </span>
                      )}
                      {e.metadata.side && <span>{e.metadata.side}</span>}
                      {e.metadata.price != null && <span>@{e.metadata.price.toFixed(3)}</span>}
                      {e.metadata.topic && <span className="text-purple-400">{e.metadata.topic}</span>}
                      {e.metadata.input_tokens && <span>Tokens: {e.metadata.input_tokens + (e.metadata.output_tokens || 0)}</span>}
                      {e.metadata.was_correct != null && (
                        <span className={e.metadata.was_correct ? "text-emerald-400 font-medium" : "text-red-400 font-medium"}>
                          {e.metadata.was_correct ? "CORRECT" : "WRONG"}
                        </span>
                      )}
                    </div>
                  )}
                </div>
                <span className="text-xs text-gray-600 whitespace-nowrap">{timeAgo(e.timestamp)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
