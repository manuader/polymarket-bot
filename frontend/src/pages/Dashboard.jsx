import { useState, useEffect } from "react";
import PortfolioSummary from "../components/PortfolioSummary";
import EquityCurve from "../components/EquityCurve";
import SignalCard from "../components/SignalCard";
import TradeTable from "../components/TradeTable";
import ActivityFeed from "../components/ActivityFeed";
import useWebSocket from "../hooks/useWebSocket";

const API = "/api/dashboard";

export default function Dashboard() {
  const [summary, setSummary] = useState(null);
  const [equity, setEquity] = useState([]);
  const [signals, setSignals] = useState([]);
  const [positions, setPositions] = useState([]);
  const [activity, setActivity] = useState([]);
  const [botStats, setBotStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const { lastMessage } = useWebSocket();

  async function fetchAll() {
    try {
      const [sumRes, eqRes, sigRes, posRes, actRes, statsRes] = await Promise.all([
        fetch(`${API}/summary`),
        fetch(`${API}/equity-curve`),
        fetch(`${API}/active-signals`),
        fetch(`${API}/open-positions`),
        fetch("/api/activity/feed?limit=30"),
        fetch("/api/activity/stats"),
      ]);
      setSummary(await sumRes.json());
      setEquity(await eqRes.json());
      setSignals(await sigRes.json());
      setPositions(await posRes.json());
      setActivity(await actRes.json());
      setBotStats(await statsRes.json());
    } catch (err) {
      console.error("Failed to fetch dashboard:", err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 15000); // refresh every 15s
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (lastMessage) fetchAll();
  }, [lastMessage]);

  if (loading) {
    return <div className="text-gray-500 py-12 text-center">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <PortfolioSummary data={summary} />

      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-sm font-medium text-gray-400 mb-3">Equity Curve</h2>
        <EquityCurve data={equity} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Active Signals */}
        <div>
          <h2 className="text-sm font-medium text-gray-400 mb-3">
            Active Signals ({signals.length})
          </h2>
          <div className="space-y-2">
            {signals.length === 0 && (
              <div className="text-gray-500 text-sm py-4 text-center">No active signals</div>
            )}
            {signals.slice(0, 10).map((s) => (
              <SignalCard key={s.id} signal={s} />
            ))}
          </div>
        </div>

        {/* Open Positions */}
        <div>
          <h2 className="text-sm font-medium text-gray-400 mb-3">
            Open Positions ({positions.length})
          </h2>
          <TradeTable
            trades={positions.map((p) => ({
              ...p,
              pnl: p.unrealized_pnl,
              pnl_pct: p.unrealized_pnl_pct,
              status: "open",
            }))}
          />
        </div>
      </div>

      {/* Bot Activity Feed */}
      <div>
        <h2 className="text-sm font-medium text-gray-400 mb-3">Bot Activity</h2>
        <ActivityFeed events={activity} stats={botStats} />
      </div>
    </div>
  );
}
