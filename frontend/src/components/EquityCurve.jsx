import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

export default function EquityCurve({ data }) {
  if (!data?.length) {
    return (
      <div className="h-64 flex items-center justify-center text-gray-500 text-sm">
        No portfolio history yet
      </div>
    );
  }

  const formatted = data.map((d) => ({
    ...d,
    date: d.timestamp ? new Date(d.timestamp).toLocaleDateString() : "",
  }));

  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={formatted}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="date" tick={{ fill: "#6b7280", fontSize: 11 }} />
          <YAxis
            tick={{ fill: "#6b7280", fontSize: 11 }}
            tickFormatter={(v) => `$${v.toLocaleString()}`}
          />
          <Tooltip
            contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }}
            labelStyle={{ color: "#9ca3af" }}
            formatter={(value) => [`$${value.toLocaleString()}`, "Portfolio"]}
          />
          <Line
            type="monotone"
            dataKey="total_value"
            stroke="#10b981"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
