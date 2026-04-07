export default function TradeTable({ trades }) {
  if (!trades?.length) {
    return <div className="text-gray-500 text-sm py-8 text-center">No trades yet</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-500 uppercase border-b border-gray-800">
            <th className="pb-2 pr-4">Market</th>
            <th className="pb-2 pr-4">Dir</th>
            <th className="pb-2 pr-4">Entry</th>
            <th className="pb-2 pr-4">Exit</th>
            <th className="pb-2 pr-4">Invested</th>
            <th className="pb-2 pr-4">P&L</th>
            <th className="pb-2 pr-4">P&L %</th>
            <th className="pb-2 pr-4">Reason</th>
            <th className="pb-2">Date</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="border-b border-gray-800/50 hover:bg-gray-900/50">
              <td className="py-2 pr-4 max-w-[200px] truncate text-gray-300">
                {t.question || t.market_id?.slice(0, 16)}
              </td>
              <td className={`py-2 pr-4 font-medium ${t.direction === "YES" ? "text-emerald-400" : "text-red-400"}`}>
                {t.direction}
              </td>
              <td className="py-2 pr-4 tabular-nums">{t.entry_price?.toFixed(3)}</td>
              <td className="py-2 pr-4 tabular-nums">{t.exit_price?.toFixed(3) || "-"}</td>
              <td className="py-2 pr-4 tabular-nums">${t.usd_invested?.toFixed(0)}</td>
              <td className={`py-2 pr-4 tabular-nums font-medium ${
                t.pnl > 0 ? "text-emerald-400" : t.pnl < 0 ? "text-red-400" : "text-gray-400"
              }`}>
                {t.pnl != null ? `${t.pnl > 0 ? "+" : ""}$${t.pnl.toFixed(2)}` : "-"}
              </td>
              <td className={`py-2 pr-4 tabular-nums ${
                t.pnl_pct > 0 ? "text-emerald-400" : t.pnl_pct < 0 ? "text-red-400" : "text-gray-400"
              }`}>
                {t.pnl_pct != null ? `${t.pnl_pct > 0 ? "+" : ""}${t.pnl_pct.toFixed(1)}%` : "-"}
              </td>
              <td className="py-2 pr-4 text-gray-500 text-xs">
                {t.exit_reason?.replace(/_/g, " ") || (t.status === "open" ? "OPEN" : "-")}
              </td>
              <td className="py-2 text-gray-500 text-xs">
                {t.opened_at ? new Date(t.opened_at).toLocaleDateString() : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
