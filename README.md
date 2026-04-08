# Polymarket Insider Trading Detection & Paper Trading Bot

Sistema que detecta posible insider trading en Polymarket analizando patrones de trading en tiempo real, y simula operaciones de paper trading basándose en esas señales para evaluar rentabilidad.

El sistema **NO ejecuta trades reales**. Todo es simulación sobre datos reales (read-only).

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────┐
│                    DATA INGESTION                        │
│                                                          │
│  Gamma API (cada 5min) ──► 2000 mercados activos en DB   │
│  Data API (cada 15s) ───► trades nuevos en DB             │
│  CLOB WebSocket (8 conn) ──► price updates en real-time   │
│  Order Book cache (cada 60s) ──► slippage dinámico        │
└───────────────────────┬──────────────────────────────────┘
                        │
            (solo trades >= $10,000 USD)
                        ▼
┌─────────────────────────────────────────────────────────┐
│              FILTRO 1: 8 REGLAS HEURÍSTICAS              │
│                                                          │
│  Con wallet (perfil REAL de Polymarket API):              │
│  1. WHALE_NEW_ACCOUNT — wallet < 7 días + < 5 trades     │
│  3. PRE_ANNOUNCEMENT — < 48h a resolución + wallet nueva  │
│  5. COORDINATED_WALLETS — 3+ wallets nuevas coordinadas   │
│  6. HIGH_WIN_RATE — win rate > 85% + > 10 trades          │
│                                                          │
│  Sin wallet (solo precio/volumen):                        │
│  2. VOLUME_SPIKE — vol 1h > 3x promedio                   │
│  4. IMPROBABLE_BET — apuesta > $10k a < 15% prob          │
│  7. PRICE_REVERSAL — spike > 10% + reversión > 50%        │
│  8. BET_AGAINST_CONSENSUS — contra > 80% de consenso      │
│                                                          │
│  ~95% descartados, ~5% pasan al filtro 2                  │
└───────────────────────┬──────────────────────────────────┘
                        │
                        │  (si prioridad >= 6)
                        ▼
┌─────────────────────────────────────────────────────────┐
│        FILTRO 2: CLAUDE SONNET + WEB SEARCH              │
│                                                          │
│  Recibe:                                                  │
│  • Reglas disparadas + metadata                           │
│  • Mercado: pregunta, categoría, fecha resolución         │
│  • Perfil real del wallet: antigüedad, # trades,          │
│    volumen, win rate, mercados, temas favoritos           │
│  • Contexto de volumen                                    │
│                                                          │
│  Busca en la web: noticias, anuncios próximos,            │
│  quién tendría info privilegiada                          │
│                                                          │
│  Output: score 1-10, confidence, recommendation           │
│  Cache: 6h por mercado | Límite: 50 calls/día             │
└───────────────────────┬──────────────────────────────────┘
                        │
                        │  (si score final >= 5)
                        ▼
┌─────────────────────────────────────────────────────────┐
│              PAPER TRADING ENGINE                         │
│                                                          │
│  Position sizing (Half-Kelly):                            │
│  • Score 5-6 → max 3%  │  Score 7 → max 7%               │
│  • Score 8-10 → max 20% │  × AI confidence                │
│                                                          │
│  Pre-checks:                                              │
│  • Circuit breaker: stop si P&L día < -5%                 │
│  • Max 10 posiciones abiertas                             │
│  • Concentración por categoría < 40%                      │
│                                                          │
│  6 condiciones de salida (cada 60s):                      │
│  1. Mercado resuelto → $1.00 o $0.00                      │
│  2. Trailing stop: profit > 40% → stop a breakeven        │
│  3. Stop loss: -30% (score ≥ 8) o -50% (score < 8)       │
│  4. Take profit: 80% del potencial máximo                 │
│  5. Near-resolution: < 30min + profit → cerrar            │
│  6. Time decay: > 14 días → cerrar                        │
└───────────────────────┬──────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│              LEARNING & MAINTENANCE                       │
│                                                          │
│  Outcome tracker (cada 5min):                             │
│  • Compara predicciones vs resultados reales              │
│  • Registra WIN/LOSS por señal y por regla                │
│  • Calibra Kelly sizing con datos reales                  │
│                                                          │
│  Cleanup (cada 1h):                                       │
│  • Borra trades > 24h sin señales vinculadas              │
│  • Mantiene trades de señales para aprendizaje            │
│                                                          │
│  Activity feed: log de todo con costos de AI              │
└─────────────────────────────────────────────────────────┘
```

---

## Stack

| Componente | Tecnología |
|------------|-----------|
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2.0 async |
| Base de datos | PostgreSQL 15 |
| Frontend | React 18, Tailwind CSS, Recharts |
| AI | Claude Sonnet (Anthropic API) con web search |
| WebSocket | websockets (Python), 8 conexiones paralelas |
| HTTP | httpx (async) |
| Migrations | Alembic |
| Containers | Docker Compose |

---

## Estructura de archivos

```
polymarket/
├── docker-compose.yml
├── .env.example
├── .env                          # (crear desde .env.example)
│
├── backend/
│   ├── main.py                   # FastAPI app + orquestación de 10 background tasks
│   ├── config.py                 # Pydantic settings
│   ├── activity.py               # Bot activity logger + AI cost tracking
│   ├── alembic.ini
│   ├── Dockerfile
│   ├── requirements.txt
│   │
│   ├── db/
│   │   ├── database.py           # Async engine + session
│   │   ├── models.py             # 12 tablas SQLAlchemy
│   │   └── migrations/           # Alembic
│   │
│   ├── pipeline/
│   │   ├── market_sync.py        # Gamma API → markets table (cada 5min)
│   │   ├── websocket_client.py   # CLOB WebSocket, 8 conexiones, heartbeat
│   │   ├── trade_enricher.py     # Data API → trades table (cada 15s)
│   │   ├── volume_tracker.py     # Sliding windows 1h/4h/24h
│   │   ├── wallet_profiler.py    # Perfil real de wallet via Data API (on-demand)
│   │   └── orderbook_cache.py    # Order book depth para slippage
│   │
│   ├── detection/
│   │   ├── heuristic_filter.py   # 8 reglas heurísticas
│   │   ├── ai_analyzer.py        # Claude Sonnet + web search
│   │   ├── signal_manager.py     # Pipeline completo + deduplicación
│   │   └── rules_config.py       # Umbrales configurables
│   │
│   ├── trading/
│   │   ├── paper_engine.py       # Entry + 6 exit conditions
│   │   ├── position_sizer.py     # Half-Kelly con calibración Bayesiana
│   │   ├── portfolio.py          # Balance, circuit breaker, concentración
│   │   ├── slippage_model.py     # Slippage dinámico desde order book
│   │   ├── stats_tracker.py      # P&L, Sharpe, drawdown, profit factor
│   │   ├── cleanup.py            # Purga trades viejos sin señales
│   │   └── outcome_tracker.py    # Registra WIN/LOSS para aprendizaje
│   │
│   └── api/
│       ├── deps.py
│       ├── websocket.py          # WS push al frontend
│       └── routes/
│           ├── dashboard.py      # Portfolio, equity curve, señales, posiciones
│           ├── signals.py        # CRUD señales con filtros
│           ├── trades.py         # Historial paper trades
│           ├── analytics.py      # Métricas de rendimiento
│           └── activity.py       # Activity feed + AI stats + learning
│
└── frontend/
    ├── package.json
    ├── vite.config.js
    ├── tailwind.config.js
    ├── index.html
    └── src/
        ├── App.jsx               # Router + nav
        ├── main.jsx
        ├── index.css
        ├── pages/
        │   ├── Dashboard.jsx     # Portfolio + señales + posiciones + activity feed
        │   ├── Signals.jsx       # Lista filtrable de señales
        │   ├── Trades.jsx        # Historial de paper trades
        │   └── Analytics.jsx     # Métricas, gráficos, distribución
        ├── components/
        │   ├── PortfolioSummary.jsx
        │   ├── SignalCard.jsx
        │   ├── TradeTable.jsx
        │   ├── EquityCurve.jsx
        │   ├── ScoreBracketChart.jsx
        │   └── ActivityFeed.jsx
        └── hooks/
            └── useWebSocket.js
```

---

## Setup

### Requisitos

- Python 3.11+
- Node.js 18+
- Docker (para PostgreSQL)
- Cuenta de Anthropic con API key
- VPN si Polymarket está bloqueado en tu país

### 1. Clonar y configurar

```bash
cd polymarket
cp .env.example .env
```

Editar `.env` y poner tu `ANTHROPIC_API_KEY`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

### 2. Levantar PostgreSQL

```bash
docker compose up -d db
```

> Nota: usa el puerto 5433 para no conflictuar con otras instancias de PostgreSQL.

### 3. Instalar dependencias Python

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
```

### 4. Crear tablas

```bash
cd backend
alembic upgrade head
```

### 5. Iniciar el backend

```bash
uvicorn main:app --reload
```

Deberías ver en los logs:
```
initial_market_sync: markets=2000
ws_starting: total_tokens=4000, connections=8
trades_ingested: new=X
```

### 6. Iniciar el frontend

En otra terminal:

```bash
cd frontend
npm install
npm run dev
```

Abrir http://localhost:5173

---

## Configuración

Todas las variables están en `.env`:

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DATABASE_URL` | `...localhost:5433/...` | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | - | API key de Anthropic (requerida para AI) |
| `MIN_TRADE_USD` | 10000 | Monto mínimo para analizar un trade |
| `MIN_SCORE_TO_TRADE` | 5 | Score mínimo para abrir paper trade |
| `MAX_AI_CALLS_PER_DAY` | 50 | Límite de invocaciones a Claude |
| `INITIAL_BALANCE` | 10000 | Balance inicial del paper trading (USDC) |
| `MAX_POSITION_PCT` | 20 | % máximo del portfolio por posición |
| `MAX_POSITIONS` | 10 | Máximo posiciones abiertas simultáneas |
| `STOP_LOSS_PCT_HIGH` | 30 | Stop loss % para scores >= 8 |
| `STOP_LOSS_PCT_LOW` | 50 | Stop loss % para scores < 8 |
| `TAKE_PROFIT_PCT` | 80 | Take profit % del potencial máximo |
| `TRAILING_STOP_TRIGGER_PCT` | 40 | Profit % para activar trailing stop |
| `CIRCUIT_BREAKER_PCT` | 5 | Stop trading si P&L día < -X% |
| `CATEGORY_CONCENTRATION_MAX_PCT` | 40 | Max % del portfolio en una categoría |

---

## Background tasks

El backend ejecuta 10 tasks en paralelo:

| Task | Frecuencia | Función |
|------|-----------|---------|
| Market sync | 5 min | Sincroniza mercados de Gamma API |
| Trade enricher | 15 seg | Trae trades nuevos de Data API |
| Volume tracker | 60 seg | Snapshots de volumen por mercado |
| Wallet profiler | On-demand | Perfil real via API al detectar trade sospechoso |
| Orderbook cache | 60 seg | Depth para slippage dinámico |
| WebSocket | Real-time | 8 conexiones, 4000 tokens suscritos |
| Detection engine | Continuo | Procesa trades de la queue |
| Paper engine | 60 seg | Chequea condiciones de salida |
| Outcome tracker | 5 min | Registra wins/losses para calibración |
| Cleanup | 1 hora | Purga trades viejos sin señales |

---

## API Endpoints

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/api/dashboard/summary` | Portfolio + métricas del día |
| GET | `/api/dashboard/equity-curve` | Valor del portfolio en el tiempo |
| GET | `/api/dashboard/active-signals` | Señales últimas 24h |
| GET | `/api/dashboard/open-positions` | Posiciones abiertas con P&L |
| GET | `/api/signals/` | Lista de señales (filtros: score, status, category) |
| GET | `/api/signals/{id}` | Detalle de una señal |
| GET | `/api/trades/` | Historial de paper trades |
| GET | `/api/analytics/performance` | Métricas: win rate, Sharpe, drawdown |
| GET | `/api/analytics/by-category` | P&L por categoría de mercado |
| GET | `/api/analytics/by-score` | P&L por bracket de score |
| GET | `/api/analytics/return-distribution` | Histograma de retornos |
| GET | `/api/activity/feed` | Activity feed del bot |
| GET | `/api/activity/stats` | Stats: AI calls, tokens, costos |
| GET | `/api/activity/learning` | Qué aprendió el bot de sus resultados |
| WS | `/ws` | WebSocket para updates real-time al frontend |

---

## Costos estimados

- **Claude Sonnet**: ~$0.01-0.05 por análisis. Con filtro heurístico previo, ~10-20 llamadas/día = **< $1/día**
- **PostgreSQL**: Docker local, sin costo
- **Polymarket APIs**: Públicas, sin costo
- **VPS** (si se deploya): ~$5-10/mes

---

## Notas

- Todo es **paper trading** (simulación). No se conecta a Polymarket para ejecutar trades reales.
- Las APIs de Polymarket son **read-only** y públicas (no requieren autenticación).
- Los timestamps están en **UTC**.
- El WebSocket de Polymarket se desconecta frecuentemente. El bot implementa reconnect con exponential backoff.
- Los wallet profiles se obtienen de la **Data API de Polymarket** (datos reales de blockchain), no de la DB local.
