import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Signals from "./pages/Signals";
import Trades from "./pages/Trades";
import Analytics from "./pages/Analytics";

const navItems = [
  { to: "/", label: "Dashboard" },
  { to: "/signals", label: "Signals" },
  { to: "/trades", label: "Trades" },
  { to: "/analytics", label: "Analytics" },
];

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-950">
        {/* Nav */}
        <nav className="border-b border-gray-800 bg-gray-900/80 backdrop-blur sticky top-0 z-50">
          <div className="max-w-7xl mx-auto px-4 flex items-center h-14 gap-6">
            <span className="text-lg font-bold text-emerald-400 tracking-tight">
              PM Insider Bot
            </span>
            <div className="flex gap-1">
              {navItems.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) =>
                    `px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-emerald-500/20 text-emerald-400"
                        : "text-gray-400 hover:text-gray-200"
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </div>
          </div>
        </nav>

        {/* Content */}
        <main className="max-w-7xl mx-auto px-4 py-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/signals" element={<Signals />} />
            <Route path="/trades" element={<Trades />} />
            <Route path="/analytics" element={<Analytics />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
