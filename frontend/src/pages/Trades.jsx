import { useState, useEffect } from "react";
import TradeTable from "../components/TradeTable";

export default function Trades() {
  const [trades, setTrades] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState({ status: "", won: "" });
  const [page, setPage] = useState(0);
  const limit = 30;

  async function fetchTrades() {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filters.status) params.set("status", filters.status);
      if (filters.won !== "") params.set("won", filters.won);
      params.set("limit", limit);
      params.set("offset", page * limit);

      const res = await fetch(`/api/trades/?${params}`);
      const data = await res.json();
      setTrades(data.trades || []);
      setTotal(data.total || 0);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchTrades();
  }, [filters, page]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-lg font-bold text-gray-100">Trade History</h1>
        <span className="text-sm text-gray-500">{total} total</span>

        <div className="flex-1" />

        <select
          className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-300"
          value={filters.status}
          onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
        >
          <option value="">All Status</option>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
        </select>

        <select
          className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-300"
          value={filters.won}
          onChange={(e) => setFilters((f) => ({ ...f, won: e.target.value }))}
        >
          <option value="">All Results</option>
          <option value="true">Winners</option>
          <option value="false">Losers</option>
        </select>
      </div>

      {loading ? (
        <div className="text-gray-500 py-8 text-center">Loading...</div>
      ) : (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <TradeTable trades={trades} />
        </div>
      )}

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
