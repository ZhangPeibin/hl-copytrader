import { NavLink, Route, Routes } from "react-router-dom";

import OverviewPage from "./pages/OverviewPage";
import WalletDetailPage from "./pages/WalletDetailPage";
import WalletsPage from "./pages/WalletsPage";

export default function App() {
  return (
    <main>
      <nav className="nav">
        <span className="brand">HL Copytrader</span>
        <NavLink to="/" end>
          概览
        </NavLink>
        <NavLink to="/wallets">钱包</NavLink>
        <a href="/api/health" className="nav-right">
          /api/health
        </a>
      </nav>

      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/wallets" element={<WalletsPage />} />
        <Route path="/wallets/:id" element={<WalletDetailPage />} />
        <Route path="*" element={<p className="empty">页面不存在。</p>} />
      </Routes>
    </main>
  );
}
