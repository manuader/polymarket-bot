function StatCard({ label, value, subValue, color = "text-gray-100" }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-2xl font-bold ${color}`}>{value}</div>
      {subValue && <div className="text-xs text-gray-500 mt-1">{subValue}</div>}
    </div>
  );
}

export default function PortfolioSummary({ data }) {
  if (!data) return null;

  const pnlColor = data.total_pnl >= 0 ? "text-emerald-400" : "text-red-400";
  const todayColor = data.today_pnl >= 0 ? "text-emerald-400" : "text-red-400";
  const winRate =
    data.total_trades > 0
      ? ((data.winning_trades / data.total_trades) * 100).toFixed(1) + "%"
      : "N/A";

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
      <StatCard
        label="Portfolio Value"
        value={`$${data.total_value?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
      />
      <StatCard
        label="Available"
        value={`$${data.balance?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
        subValue={`$${data.invested?.toFixed(2)} invested`}
      />
      <StatCard
        label="Total P&L"
        value={`${data.total_pnl >= 0 ? "+" : ""}$${data.total_pnl?.toFixed(2)}`}
        color={pnlColor}
      />
      <StatCard
        label="Today P&L"
        value={`${data.today_pnl >= 0 ? "+" : ""}$${data.today_pnl?.toFixed(2)}`}
        color={todayColor}
      />
      <StatCard
        label="Win Rate"
        value={winRate}
        subValue={`${data.winning_trades}W / ${data.losing_trades}L`}
      />
      <StatCard
        label="Active Signals"
        value={data.active_signals || 0}
        subValue={`${data.open_positions || 0} open positions`}
      />
    </div>
  );
}
