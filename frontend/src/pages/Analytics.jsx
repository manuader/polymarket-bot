import { useState, useEffect } from "react";
import EquityCurve from "../components/EquityCurve";
import ScoreBracketChart from "../components/ScoreBracketChart";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";

export default function Analytics() {
  const [perf, setPerf] = useState(null);
  const [byCategory, setByCategory] = useState({});
  const [byScore, setByScore] = useState({});
  const [equity, setEquity] = useState([]);
  const [distribution, setDistribution] = useState([]);
  const [loading, setLoading] = useState(true);

  async function fetchAll() {
    try {
      const [perfRes, catRes, scoreRes, eqRes, distRes] = await Promise.all([
        fetch("/api/analytics/performance"),
        fetch("/api/analytics/by-category"),
        fetch("/api/analytics/by-score"),
        fetch("/api/dashboard/equity-curve"),
        fetch("/api/analytics/return-distribution"),
      ]);
      setPerf(await perfRes.json());
      setByCategory(await catRes.json());
      setByScore(await scoreRes.json());
      setEquity(await eqRes.json());
      setDistribution(await distRes.json());
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchAll();
  }, []);

  if (loading) return <div className="text-gray-500 py-12 text-center">Loading...</div>;

  // Histogram bins
  const histBins = [];
  if (distribution.length) {
    const min = Math.floor(Math.min(...distribution) / 10) * 10;
    const max = Math.ceil(Math.max(...distribution) / 10) * 10;
    for (let i = min; i <= max; i += 10) {
      const count = distribution.filter((v) => v >= i && v < i + 10).length;
      histBins.push({ range: `${i}%`, count });
    }
  }

  // Category data
  const catData = Object.entries(byCategory).map(([cat, stats]) => ({
    category: cat,
    pnl: stats.total_pnl,
    trades: stats.trades,
  }));

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-bold text-gray-100">Analytics</h1>

      {/* Key Metrics */}
      {perf && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricCard label="Total P&L" value={`$${perf.total_pnl?.toFixed(2)}`}
            color={perf.total_pnl >= 0 ? "text-emerald-400" : "text-red-400"} />
          <MetricCard label="Win Rate" value={`${(perf.win_rate * 100).toFixed(1)}%`} />
          <MetricCard label="Profit Factor" value={perf.profit_factor || "N/A"} />
          <MetricCard label="Sharpe Ratio" value={perf.sharpe_ratio?.toFixed(2) || "N/A"} />
          <MetricCard label="Max Drawdown" value={`${perf.max_drawdown?.toFixed(1)}%`} color="text-red-400" />
          <MetricCard label="Avg P&L/Trade" value={`$${perf.avg_pnl_per_trade?.toFixed(2)}`}
            color={perf.avg_pnl_per_trade >= 0 ? "text-emerald-400" : "text-red-400"} />
          <MetricCard label="Avg Win" value={`$${perf.avg_win?.toFixed(2)}`} color="text-emerald-400" />
          <MetricCard label="Avg Loss" value={`$${perf.avg_loss?.toFixed(2)}`} color="text-red-400" />
        </div>
      )}

      {/* Equity Curve */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-sm font-medium text-gray-400 mb-3">Equity Curve</h2>
        <EquityCurve data={equity} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Win Rate by Score */}
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h2 className="text-sm font-medium text-gray-400 mb-3">Win Rate by Score Bracket</h2>
          <ScoreBracketChart data={byScore} />
        </div>

        {/* P&L by Category */}
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h2 className="text-sm font-medium text-gray-400 mb-3">P&L by Category</h2>
          {catData.length ? (
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={catData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                  <XAxis dataKey="category" tick={{ fill: "#6b7280", fontSize: 11 }} />
                  <YAxis tick={{ fill: "#6b7280", fontSize: 11 }} tickFormatter={(v) => `$${v}`} />
                  <Tooltip
                    contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }}
                  />
                  <Bar dataKey="pnl" fill="#10b981" radius={[4, 4, 0, 0]} name="P&L ($)" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="h-48 flex items-center justify-center text-gray-500 text-sm">No data</div>
          )}
        </div>

        {/* Return Distribution */}
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 lg:col-span-2">
          <h2 className="text-sm font-medium text-gray-400 mb-3">Return Distribution</h2>
          {histBins.length ? (
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={histBins}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                  <XAxis dataKey="range" tick={{ fill: "#6b7280", fontSize: 11 }} />
                  <YAxis tick={{ fill: "#6b7280", fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }}
                  />
                  <Bar dataKey="count" fill="#6366f1" radius={[4, 4, 0, 0]} name="Trades" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="h-48 flex items-center justify-center text-gray-500 text-sm">No trades yet</div>
          )}
        </div>
      </div>

      {/* Best/Worst Trade */}
      {perf?.best_trade && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="bg-gray-900 border border-emerald-500/30 rounded-lg p-4">
            <div className="text-xs text-gray-500 uppercase mb-1">Best Trade</div>
            <div className="text-emerald-400 text-xl font-bold">+${perf.best_trade.pnl?.toFixed(2)} ({perf.best_trade.pnl_pct?.toFixed(1)}%)</div>
            <div className="text-xs text-gray-500 mt-1">Trade #{perf.best_trade.id}</div>
          </div>
          <div className="bg-gray-900 border border-red-500/30 rounded-lg p-4">
            <div className="text-xs text-gray-500 uppercase mb-1">Worst Trade</div>
            <div className="text-red-400 text-xl font-bold">${perf.worst_trade.pnl?.toFixed(2)} ({perf.worst_trade.pnl_pct?.toFixed(1)}%)</div>
            <div className="text-xs text-gray-500 mt-1">Trade #{perf.worst_trade.id}</div>
          </div>
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value, color = "text-gray-100" }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-3">
      <div className="text-xs text-gray-500 uppercase">{label}</div>
      <div className={`text-xl font-bold ${color}`}>{value}</div>
    </div>
  );
}
