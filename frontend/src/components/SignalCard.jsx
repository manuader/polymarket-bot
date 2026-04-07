function scoreColor(score) {
  if (score >= 8) return "text-red-400 bg-red-500/10 border-red-500/30";
  if (score >= 6) return "text-orange-400 bg-orange-500/10 border-orange-500/30";
  if (score >= 4) return "text-yellow-400 bg-yellow-500/10 border-yellow-500/30";
  return "text-gray-400 bg-gray-500/10 border-gray-500/30";
}

function statusBadge(status) {
  const map = {
    active: "bg-blue-500/20 text-blue-400",
    resolved_win: "bg-emerald-500/20 text-emerald-400",
    resolved_loss: "bg-red-500/20 text-red-400",
    expired: "bg-gray-500/20 text-gray-400",
  };
  return map[status] || map.active;
}

export default function SignalCard({ signal }) {
  const timeAgo = signal.detected_at
    ? new Date(signal.detected_at).toLocaleString()
    : "";

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 hover:border-gray-700 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-gray-100 truncate">
            {signal.question || signal.market_id?.slice(0, 20)}
          </div>
          <div className="text-xs text-gray-500 mt-1">
            {signal.signal_type?.replace(/\+/g, " + ")}
          </div>
          {signal.category && (
            <span className="inline-block mt-1 px-2 py-0.5 rounded text-xs bg-gray-800 text-gray-400">
              {signal.category}
            </span>
          )}
        </div>

        <div className="flex flex-col items-end gap-1">
          <div
            className={`px-2 py-1 rounded border text-lg font-bold ${scoreColor(signal.score)}`}
          >
            {signal.score}
          </div>
          <span className={`px-2 py-0.5 rounded text-xs ${statusBadge(signal.status)}`}>
            {signal.status}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-4 mt-3 text-xs text-gray-500">
        <span>
          Direction:{" "}
          <span className={signal.direction === "YES" ? "text-emerald-400" : "text-red-400"}>
            {signal.direction}
          </span>
        </span>
        {signal.total_suspicious_volume > 0 && (
          <span>Vol: ${signal.total_suspicious_volume?.toLocaleString()}</span>
        )}
        {signal.confidence && (
          <span>Conf: {(signal.confidence * 100).toFixed(0)}%</span>
        )}
        <span className="ml-auto">{timeAgo}</span>
      </div>

      {signal.recommendation && (
        <div className="mt-2">
          <span
            className={`px-2 py-0.5 rounded text-xs font-medium ${
              signal.recommendation === "STRONG_BUY"
                ? "bg-emerald-500/20 text-emerald-400"
                : signal.recommendation === "BUY"
                ? "bg-blue-500/20 text-blue-400"
                : signal.recommendation === "SKIP"
                ? "bg-red-500/20 text-red-400"
                : "bg-gray-500/20 text-gray-400"
            }`}
          >
            {signal.recommendation}
          </span>
        </div>
      )}
    </div>
  );
}
