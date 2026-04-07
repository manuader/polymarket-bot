import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

export default function ScoreBracketChart({ data }) {
  if (!data || !Object.keys(data).length) {
    return (
      <div className="h-48 flex items-center justify-center text-gray-500 text-sm">
        No bracket data yet
      </div>
    );
  }

  const chartData = Object.entries(data).map(([bracket, stats]) => ({
    bracket: `Score ${bracket}`,
    win_rate: stats.win_rate ? (stats.win_rate * 100).toFixed(1) : 0,
    trades: stats.total_trades || 0,
    profit_factor: stats.profit_factor || 0,
  }));

  return (
    <div className="h-48">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="bracket" tick={{ fill: "#6b7280", fontSize: 11 }} />
          <YAxis tick={{ fill: "#6b7280", fontSize: 11 }} tickFormatter={(v) => `${v}%`} />
          <Tooltip
            contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }}
            labelStyle={{ color: "#9ca3af" }}
          />
          <Bar dataKey="win_rate" fill="#10b981" radius={[4, 4, 0, 0]} name="Win Rate %" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
