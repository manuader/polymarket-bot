import { useState, useEffect } from "react";
import SignalCard from "../components/SignalCard";

export default function Signals() {
  const [signals, setSignals] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState({ score_min: "", status: "", category: "" });
  const [page, setPage] = useState(0);
  const limit = 20;

  async function fetchSignals() {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filters.score_min) params.set("score_min", filters.score_min);
      if (filters.status) params.set("status", filters.status);
      if (filters.category) params.set("category", filters.category);
      params.set("limit", limit);
      params.set("offset", page * limit);

      const res = await fetch(`/api/signals/?${params}`);
      const data = await res.json();
      setSignals(data.signals || []);
      setTotal(data.total || 0);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchSignals();
  }, [filters, page]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-lg font-bold text-gray-100">Signals</h1>
        <span className="text-sm text-gray-500">{total} total</span>

        <div className="flex-1" />

        <select
          className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-300"
          value={filters.score_min}
          onChange={(e) => setFilters((f) => ({ ...f, score_min: e.target.value }))}
        >
          <option value="">All Scores</option>
          <option value="8">8+</option>
          <option value="7">7+</option>
          <option value="6">6+</option>
          <option value="5">5+</option>
        </select>

        <select
          className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-300"
          value={filters.status}
          onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
        >
          <option value="">All Status</option>
          <option value="active">Active</option>
          <option value="resolved_win">Won</option>
          <option value="resolved_loss">Lost</option>
          <option value="expired">Expired</option>
        </select>
      </div>

      {loading ? (
        <div className="text-gray-500 py-8 text-center">Loading...</div>
      ) : (
        <div className="space-y-2">
          {signals.map((s) => (
            <SignalCard key={s.id} signal={s} />
          ))}
          {signals.length === 0 && (
            <div className="text-gray-500 py-8 text-center">No signals match filters</div>
          )}
        </div>
      )}

      {/* Pagination */}
      {total > limit && (
        <div className="flex items-center justify-center gap-2 pt-4">
          <button
            className="px-3 py-1 rounded bg-gray-800 text-sm text-gray-300 disabled:opacity-30"
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
          >
            Prev
          </button>
          <span className="text-sm text-gray-500">
            Page {page + 1} of {Math.ceil(total / limit)}
          </span>
          <button
            className="px-3 py-1 rounded bg-gray-800 text-sm text-gray-300 disabled:opacity-30"
            disabled={(page + 1) * limit >= total}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
