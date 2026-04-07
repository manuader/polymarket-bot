# SPEC: Polymarket Insider Trading Detection & Paper Trading Bot

## Visión General

Sistema de dos módulos que: (1) detecta posible insider trading en Polymarket analizando patrones de trading, datos de fuentes externas y contexto web, y (2) simula operaciones de paper trading basándose en esas señales para evaluar rentabilidad.

El sistema NO se conecta a Polymarket para ejecutar trades reales. Todo es simulación sobre datos reales.

---

## Arquitectura General

```
┌─────────────────────────────────────────────────────────────┐
│                     DATA PIPELINE                           │
│                                                             │
│  Polymarket CLOB WebSocket ──► Trade Ingestion              │
│  Polymarket Gamma API ───────► Market Metadata              │
│  Hashdive API/Scraping ──────► Insider Indicators           │
│  Dune Analytics ─────────────► On-chain Wallet History      │
│                                                             │
│  Todo se almacena en PostgreSQL                             │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   DETECTION ENGINE                          │
│                                                             │
│  Filtro 1 (reglas) ──► Filtro 2 (IA + web search)          │
│                                                             │
│  Output: señales con score 1-10 + análisis                  │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  PAPER TRADING ENGINE                       │
│                                                             │
│  Portfolio virtual ──► Position sizing ──► Simulación       │
│                                                             │
│  Output: P&L, win rate, ROI, historial                      │
└─────────────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                     DASHBOARD                               │
│                                                             │
│  React app con: alertas activas, portfolio, historial,      │
│  métricas de rendimiento, detalle de cada señal             │
└─────────────────────────────────────────────────────────────┘
```

---

## Módulo 1: Data Pipeline

### 1.1 Trade Ingestion (Polymarket CLOB WebSocket)

**Conexión:** WebSocket a `wss://ws-subscriptions-clob.polymarket.com/ws/market`

**Channels a suscribir:**
- `market` channel: trades en tiempo real de todos los mercados activos

**Datos a capturar por cada trade:**
```
{
  market_id: string,          // condition_id del mercado
  token_id: string,           // ID del token YES o NO
  timestamp: datetime,
  price: float,               // precio de ejecución (0.01 - 0.99)
  size: float,                // cantidad de shares
  side: "BUY" | "SELL",
  outcome: "YES" | "NO",
  maker_address: string,      // wallet del maker (si disponible)
  taker_address: string,      // wallet del taker (si disponible)
  usd_value: float            // size * price en USDC
}
```

**Lógica:**
- Conectar al WebSocket y mantener conexión persistente con reconnect automático
- Parsear cada mensaje del trade channel
- Insertar en tabla `trades` de PostgreSQL
- Calcular y actualizar en tiempo real agregados por mercado (volumen últimas 1h, 4h, 24h)

### 1.2 Market Metadata (Polymarket Gamma API)

**Endpoint:** `https://gamma-api.polymarket.com`

**Polling:** Cada 5 minutos para mercados activos, cada hora para el catálogo completo.

**Datos a almacenar por mercado:**
```
{
  condition_id: string,
  question: string,            // "Will X happen by Y?"
  description: string,         // descripción completa con reglas
  category: string,            // "politics", "sports", "crypto", etc.
  end_date: datetime,          // fecha de resolución
  tokens: [{
    token_id: string,
    outcome: string,           // "Yes" o "No"
    price: float
  }],
  volume: float,               // volumen total del mercado
  liquidity: float,
  active: boolean,
  slug: string,                // para construir URL polymarket.com/event/slug
  tags: string[]
}
```

### 1.3 Fuentes Externas de Inteligencia

#### Hashdive (hashdive.com)
- Scraping de la sección "Possible Insiders" por mercado
- Extraer: wallet addresses flaggeadas, número de trades, montos, win rate del wallet
- Frecuencia: cada 15 minutos para mercados con volumen > $10,000

#### Dune Analytics
- Usar queries públicas de dashboards de Polymarket para obtener:
  - Historial de wallets (antigüedad, número de trades totales, mercados en los que participó)
  - Conexiones entre wallets (depósitos desde misma dirección de exchange)
  - Volumen histórico por wallet
- Hay queries existentes: buscar "Polymarket" en dune.com/browse/dashboards

#### PolymarketAnalytics.com
- Scraping de alertas de whales y smart money
- Datos de interés: wallets con mayor volumen reciente, wallets con mejor win rate

#### @whalewatchpoly y cuentas similares en X
- Opcional/fase 2: monitorear via API de X o scraping de feeds RSS
- Estos proveen alertas de trades grandes en tiempo real

**Nota sobre scraping:** Si alguna fuente no tiene API pública, implementar scraping con rate limiting responsable. Si bloquean, degradar gracefully y depender de las otras fuentes. Las fuentes externas son complementarias, no críticas — el CLOB WebSocket es la fuente primaria.

### 1.4 Base de Datos (PostgreSQL)

**Esquema principal:**

```sql
-- Mercados activos y metadata
CREATE TABLE markets (
  condition_id TEXT PRIMARY KEY,
  question TEXT NOT NULL,
  description TEXT,
  category TEXT,
  end_date TIMESTAMP,
  slug TEXT,
  tags TEXT[],
  volume NUMERIC,
  liquidity NUMERIC,
  active BOOLEAN DEFAULT true,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

-- Cada trade capturado del WebSocket
CREATE TABLE trades (
  id SERIAL PRIMARY KEY,
  market_id TEXT REFERENCES markets(condition_id),
  token_id TEXT NOT NULL,
  timestamp TIMESTAMP NOT NULL,
  price NUMERIC NOT NULL,
  size NUMERIC NOT NULL,
  side TEXT NOT NULL,           -- BUY o SELL
  outcome TEXT NOT NULL,        -- YES o NO
  maker_address TEXT,
  taker_address TEXT,
  usd_value NUMERIC NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_trades_market_time ON trades(market_id, timestamp);
CREATE INDEX idx_trades_taker ON trades(taker_address);
CREATE INDEX idx_trades_usd ON trades(usd_value);

-- Perfil agregado de cada wallet
CREATE TABLE wallets (
  address TEXT PRIMARY KEY,
  first_seen TIMESTAMP,
  total_trades INTEGER DEFAULT 0,
  total_volume NUMERIC DEFAULT 0,
  markets_traded INTEGER DEFAULT 0,
  wins INTEGER DEFAULT 0,
  losses INTEGER DEFAULT 0,
  win_rate NUMERIC,
  avg_trade_size NUMERIC,
  is_flagged_hashdive BOOLEAN DEFAULT false,
  updated_at TIMESTAMP DEFAULT NOW()
);

-- Agregados de volumen por mercado por ventana temporal
CREATE TABLE market_volume_snapshots (
  id SERIAL PRIMARY KEY,
  market_id TEXT REFERENCES markets(condition_id),
  timestamp TIMESTAMP NOT NULL,
  volume_1h NUMERIC,
  volume_4h NUMERIC,
  volume_24h NUMERIC,
  trade_count_1h INTEGER,
  avg_trade_size_1h NUMERIC,
  price_change_1h NUMERIC       -- cambio de precio en la última hora
);

-- Señales detectadas por el engine
CREATE TABLE signals (
  id SERIAL PRIMARY KEY,
  market_id TEXT REFERENCES markets(condition_id),
  detected_at TIMESTAMP DEFAULT NOW(),
  signal_type TEXT NOT NULL,     -- WHALE_NEW_ACCOUNT, VOLUME_SPIKE, etc.
  score INTEGER CHECK (score BETWEEN 1 AND 10),
  direction TEXT,                -- YES o NO (hacia dónde apunta el insider)
  confidence NUMERIC,
  analysis TEXT,                 -- análisis de la IA
  trigger_wallets TEXT[],        -- wallets que dispararon la señal
  trigger_trades INTEGER[],     -- IDs de trades que dispararon la señal
  total_suspicious_volume NUMERIC,
  market_price_at_detection NUMERIC,
  time_to_resolution INTERVAL,  -- tiempo restante hasta end_date
  web_context TEXT,              -- resumen del contexto web encontrado
  status TEXT DEFAULT 'active'   -- active, expired, resolved_win, resolved_loss
);

-- Operaciones del paper trading
CREATE TABLE paper_trades (
  id SERIAL PRIMARY KEY,
  signal_id INTEGER REFERENCES signals(id),
  opened_at TIMESTAMP DEFAULT NOW(),
  closed_at TIMESTAMP,
  market_id TEXT REFERENCES markets(condition_id),
  direction TEXT NOT NULL,       -- YES o NO
  entry_price NUMERIC NOT NULL,
  exit_price NUMERIC,
  size NUMERIC NOT NULL,         -- cantidad de shares
  usd_invested NUMERIC NOT NULL,
  usd_returned NUMERIC,
  pnl NUMERIC,
  pnl_pct NUMERIC,
  exit_reason TEXT,              -- resolution, stop_loss, take_profit, manual
  status TEXT DEFAULT 'open'     -- open, closed
);

-- Estado del portfolio virtual
CREATE TABLE portfolio (
  id SERIAL PRIMARY KEY,
  timestamp TIMESTAMP DEFAULT NOW(),
  balance NUMERIC NOT NULL,      -- USDC disponible
  invested NUMERIC NOT NULL,     -- USDC en posiciones abiertas
  total_value NUMERIC NOT NULL,  -- balance + valor actual de posiciones
  total_pnl NUMERIC,
  total_trades INTEGER,
  winning_trades INTEGER,
  losing_trades INTEGER
);
```

---

## Módulo 2: Detection Engine

### 2.1 Filtro 1: Reglas Heurísticas (sin IA)

Corre cada vez que se ingesta un trade nuevo. Evalúa si el trade o un patrón reciente en el mercado merece análisis profundo.

**Umbral mínimo:** Solo analizar trades con usd_value >= $10,000 (configurable).

**Reglas a implementar:**

#### Regla 1: WHALE_NEW_ACCOUNT
```
SI wallet.first_seen < 7 días atrás
Y trade.usd_value > $10,000
Y wallet.total_trades < 5
ENTONCES flag con prioridad ALTA
```
Justificación: En 7/7 casos documentados, los insiders usaron cuentas nuevas.

#### Regla 2: VOLUME_SPIKE
```
SI market.volume_1h > 3x market.volume_24h_promedio
Y NO hay noticias recientes sobre el tema (se valida en filtro 2)
ENTONCES flag con prioridad ALTA
```
Justificación: En 6/7 casos hubo spikes de volumen anormales previos al evento.

#### Regla 3: PRE_ANNOUNCEMENT_ACTIVITY
```
SI market.end_date - NOW() < 48 horas
Y trade.usd_value > $5,000
Y wallet es nueva o tiene pocos trades
ENTONCES flag con prioridad ALTA
```
Justificación: En 6/7 casos, los trades ocurrieron cerca de la fecha de resolución o de un anuncio programado.

#### Regla 4: IMPROBABLE_BET
```
SI trade.price < 0.15 (outcome cotiza < 15% probabilidad)
Y trade.usd_value > $10,000
ENTONCES flag con prioridad MEDIA-ALTA
```
Justificación: Apostar fuerte a algo que el mercado considera muy improbable es una señal clásica (casos Maduro al 5.5%, Nobel al 3.75%).

#### Regla 5: COORDINATED_WALLETS
```
SI en la última hora, 3+ wallets nuevas
compran el mismo outcome en el mismo mercado
Y el volumen combinado > $20,000
ENTONCES flag con prioridad MUY ALTA
```
Justificación: En 3/7 casos se detectaron múltiples wallets coordinadas (Irán, Nobel). Es la señal más fuerte.

#### Regla 6: HIGH_WIN_RATE_WHALE
```
SI wallet.win_rate > 85%
Y wallet.total_trades > 10
Y trade.usd_value > $10,000
ENTONCES flag con prioridad MEDIA
```
Justificación: El trader de Irán tenía 93% de win rate. Wallets con precisión anormalmente alta merecen seguimiento.

**Output del Filtro 1:** Lista de trades/mercados flaggeados con tipo de señal y prioridad. Solo estos pasan al Filtro 2.

### 2.2 Filtro 2: Análisis con IA (Claude API + Web Search)

Para cada señal del Filtro 1, se invoca la API de Claude (modelo: claude-sonnet-4-20250514) con web search habilitado.

**Prompt template para la IA:**

```
Eres un analista de mercados de predicción especializado en detectar insider trading en Polymarket.

## Señal Detectada
- Tipo: {signal_type}
- Mercado: {market_question}
- Categoría: {market_category}
- Fecha de resolución: {end_date}
- Tiempo restante: {time_to_resolution}

## Datos del Trade Sospechoso
- Wallet(s): {wallet_addresses}
- Monto total: ${total_usd}
- Dirección: {direction} (YES/NO)
- Precio de entrada: ${entry_price}
- Precio actual del mercado: ${current_price}

## Perfil de la(s) Wallet(s)
- Antigüedad: {wallet_age}
- Trades totales: {total_trades}
- Win rate: {win_rate}%
- Otros mercados en los que operó: {other_markets}
- ¿Flaggeada por Hashdive como posible insider? {hashdive_flag}

## Contexto del Mercado
- Volumen últimas 24h: ${volume_24h}
- Spike de volumen detectado: {volume_spike_info}
- Movimiento de precio reciente: {price_movement}

## Tu Tarea

1. Buscar en internet información relevante sobre el evento de este mercado:
   - ¿Hay un anuncio programado próximo? ¿Cuándo?
   - ¿Hay noticias recientes que justifiquen el movimiento de precio?
   - ¿Quién tendría acceso a información privilegiada sobre este resultado?

2. Evaluar la probabilidad de insider trading considerando:
   - Timing del trade vs fecha de anuncio/resolución
   - Perfil de la wallet (nueva, sin historial, concentrada en un mercado)
   - Magnitud de la apuesta vs probabilidad del mercado
   - Existencia de información pública que justifique la apuesta
   - Patrones conocidos de insider trading en Polymarket

3. Responder EXCLUSIVAMENTE en el siguiente formato JSON (sin markdown, sin backticks):

{
  "insider_score": <1-10>,
  "confidence": <0.0-1.0>,
  "likely_direction": "YES" | "NO",
  "reasoning": "<explicación breve de 2-3 oraciones>",
  "key_findings": ["<hallazgo 1>", "<hallazgo 2>", ...],
  "upcoming_event": "<descripción del evento/anuncio relevante si existe>",
  "upcoming_event_date": "<fecha si se encontró>" | null,
  "news_justification": true | false,
  "recommendation": "STRONG_BUY" | "BUY" | "HOLD" | "SKIP"
}

Criterios para el score:
- 1-3: Probablemente actividad normal de ballena o trader sofisticado
- 4-5: Sospechoso pero insuficiente evidencia
- 6-7: Altamente sospechoso, múltiples indicadores coinciden
- 8-10: Casi certeza de insider trading (cuenta nueva + monto grande + timing pre-anuncio + sin noticias justificativas)
```

**Configuración de la llamada a la API:**
```javascript
{
  model: "claude-sonnet-4-20250514",
  max_tokens: 1000,
  tools: [{
    type: "web_search_20250305",
    name: "web_search"
  }],
  messages: [{ role: "user", content: prompt }]
}
```

**Optimización de costos:**
- Usar Sonnet (no Opus) para mantener costos bajos
- Solo invocar para trades que pasaron el Filtro 1 (debería ser <5% del total)
- Cachear resultados de búsqueda web por mercado (si ya analizaste un mercado hace menos de 1 hora, reutilizar el contexto web)
- Limitar a máximo 50 invocaciones por día (configurable)

---

## Módulo 3: Paper Trading Engine

### 3.1 Configuración Inicial

```
INITIAL_BALANCE = 10000       # USDC virtuales
MAX_POSITION_PCT = 10         # máximo 10% del portfolio por posición
MAX_POSITIONS = 10            # máximo 10 posiciones abiertas simultáneas
MIN_SCORE_TO_TRADE = 7        # solo operar con score >= 7
STOP_LOSS_PCT = 50            # cerrar si la posición pierde 50%
TAKE_PROFIT_PCT = 80          # cerrar si ya ganamos 80% del potencial
```

### 3.2 Lógica de Entrada

Cuando el Detection Engine emite una señal con score >= MIN_SCORE_TO_TRADE:

1. **Verificar que hay presupuesto disponible:**
   - balance >= MIN_TRADE_SIZE ($100)
   - posiciones abiertas < MAX_POSITIONS

2. **Calcular position size:**
   - Base: MAX_POSITION_PCT del portfolio total
   - Ajustar por score: multiplicar por (score / 10)
   - Ejemplo: portfolio $10,000, score 8 → 10% * (8/10) = 8% = $800

3. **Simular la compra:**
   - Tomar el precio actual del outcome indicado por la señal
   - Aplicar slippage estimado: +2% sobre el precio actual (simula que no somos los primeros)
   - Calcular shares: usd_invested / (price + slippage)
   - Registrar en paper_trades

4. **Tipo de orden simulada:** FOK (queremos entrar rápido, no dejar una limit order esperando)

### 3.3 Lógica de Salida

Evaluar cada posición abierta cada minuto:

- **Resolución del mercado:** Si el mercado se resolvió, cerrar la posición a $1.00 (ganamos) o $0.00 (perdimos)
- **Take profit:** Si el precio actual >= entry_price + (1 - entry_price) * TAKE_PROFIT_PCT, cerrar
- **Stop loss:** Si el precio actual <= entry_price * (1 - STOP_LOSS_PCT / 100), cerrar
- **Timeout:** Si faltan menos de 2 horas para la resolución y el precio está por encima del entry, considerar cerrar para asegurar ganancias parciales

### 3.4 Métricas a Trackear

```
- Total P&L (USDC y %)
- Win rate (% de trades ganadores)
- Promedio de P&L por trade ganador vs perdedor
- Sharpe ratio (si aplica)
- Max drawdown
- ROI mensual
- P&L por categoría de mercado (politics, crypto, sports, etc.)
- P&L por score de señal (¿los score 9-10 rinden más que los 7-8?)
- Tiempo promedio en posición
- Distribución de retornos
```

---

## Módulo 4: Dashboard (React)

### 4.1 Páginas

**Dashboard principal:**
- Balance actual del portfolio virtual
- P&L total y del día
- Gráfico de equity curve (valor del portfolio en el tiempo)
- Señales activas (últimas 24h) con su score y estado
- Posiciones abiertas con P&L en tiempo real

**Alertas / Señales:**
- Lista de todas las señales detectadas
- Filtros por: score, categoría, estado (active, resolved_win, resolved_loss)
- Detalle de cada señal: análisis de la IA, wallets involucradas, trades que la dispararon, contexto web

**Historial de Trades:**
- Tabla con todos los paper trades
- Columnas: mercado, dirección, entry price, exit price, P&L, duración, motivo de salida
- Filtros por fecha, categoría, resultado

**Analytics:**
- Win rate por rango de score
- P&L por categoría de mercado
- Distribución de retornos (histograma)
- Mejor y peor trade
- Drawdown chart

### 4.2 Stack

- Frontend: React con Tailwind CSS
- Backend API: Python (FastAPI)
- Base de datos: PostgreSQL
- WebSocket del backend al frontend para actualizaciones en tiempo real

---

## Stack Tecnológico

```
Lenguaje principal:    Python 3.11+
Framework API:         FastAPI
Base de datos:         PostgreSQL 15+
ORM:                   SQLAlchemy + Alembic (migrations)
WebSocket client:      websockets (Python)
HTTP client:           httpx (async)
Web scraping:          playwright o httpx + beautifulsoup4
Frontend:              React + Tailwind + Recharts (gráficos)
IA:                    Anthropic API (Claude Sonnet)
Task scheduling:       APScheduler o simple asyncio loops
Containerización:      Docker + docker-compose
```

---

## Estructura de Archivos

```
polymarket-insider-bot/
├── docker-compose.yml
├── .env.example                    # API keys, DB credentials
├── README.md
│
├── backend/
│   ├── requirements.txt
│   ├── main.py                     # FastAPI app entry point
│   ├── config.py                   # Settings y constantes configurables
│   │
│   ├── db/
│   │   ├── database.py             # Conexión a PostgreSQL
│   │   ├── models.py               # SQLAlchemy models
│   │   └── migrations/             # Alembic
│   │
│   ├── pipeline/
│   │   ├── websocket_client.py     # Conexión al CLOB WebSocket
│   │   ├── market_sync.py          # Sync de metadata via Gamma API
│   │   ├── hashdive_scraper.py     # Scraping de Hashdive
│   │   ├── dune_client.py          # Queries a Dune Analytics
│   │   └── wallet_profiler.py      # Agregación de perfiles de wallet
│   │
│   ├── detection/
│   │   ├── heuristic_filter.py     # Filtro 1: reglas
│   │   ├── ai_analyzer.py          # Filtro 2: Claude API + web search
│   │   └── signal_manager.py       # Gestión de señales
│   │
│   ├── trading/
│   │   ├── paper_engine.py         # Lógica de paper trading
│   │   ├── position_sizer.py       # Cálculo de position size
│   │   └── portfolio.py            # Estado del portfolio
│   │
│   └── api/
│       ├── routes/
│       │   ├── dashboard.py        # Endpoints del dashboard
│       │   ├── signals.py          # CRUD de señales
│       │   ├── trades.py           # Historial de trades
│       │   └── analytics.py        # Métricas y stats
│       └── websocket.py            # WS para push de updates al frontend
│
├── frontend/
│   ├── package.json
│   ├── src/
│   │   ├── App.jsx
│   │   ├── pages/
│   │   │   ├── Dashboard.jsx
│   │   │   ├── Signals.jsx
│   │   │   ├── Trades.jsx
│   │   │   └── Analytics.jsx
│   │   ├── components/
│   │   │   ├── EquityCurve.jsx
│   │   │   ├── SignalCard.jsx
│   │   │   ├── TradeTable.jsx
│   │   │   └── PortfolioSummary.jsx
│   │   └── hooks/
│   │       └── useWebSocket.js
│   └── tailwind.config.js
│
└── scripts/
    ├── seed_markets.py             # Carga inicial de mercados
    └── backtest.py                 # Backtesting con datos históricos
```

---

## Variables de Entorno (.env)

```
# Base de datos
DATABASE_URL=postgresql://user:pass@localhost:5432/polymarket_bot

# Anthropic API (para el análisis con IA)
ANTHROPIC_API_KEY=sk-ant-...

# Configuración del bot
MIN_TRADE_USD=10000              # Umbral mínimo para analizar un trade
MIN_SCORE_TO_TRADE=7             # Score mínimo para paper trade
INITIAL_BALANCE=10000            # Balance inicial del paper trading
MAX_POSITION_PCT=10              # % máximo del portfolio por posición
MAX_AI_CALLS_PER_DAY=50          # Límite de invocaciones a Claude

# Polymarket
CLOB_WS_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
GAMMA_API_URL=https://gamma-api.polymarket.com
CLOB_API_URL=https://clob.polymarket.com
```

---

## Plan de Implementación (orden sugerido)

### Fase 1: Data Pipeline (empezar por acá)
1. Setup de PostgreSQL + modelos SQLAlchemy + migraciones
2. Conexión al CLOB WebSocket para capturar trades en tiempo real
3. Sync de market metadata via Gamma API
4. Agregación de wallet profiles a partir de los trades capturados
5. Verificar que los datos fluyen correctamente con logs

### Fase 2: Detection Engine
1. Implementar las 6 reglas heurísticas del Filtro 1
2. Integrar Claude API con web search para el Filtro 2
3. Pipeline completo: trade llega → pasa filtros → genera señal
4. Tests con datos sintéticos que simulen los 7 casos documentados

### Fase 3: Paper Trading Engine
1. Lógica de portfolio virtual (balance, posiciones)
2. Position sizing basado en score
3. Lógica de entrada automática cuando llega señal con score >= threshold
4. Lógica de salida (resolución, stop loss, take profit)
5. Cálculo de métricas de rendimiento

### Fase 4: Dashboard
1. API endpoints con FastAPI
2. Frontend React con las 4 páginas
3. WebSocket para updates en tiempo real
4. Gráficos de equity curve y analytics

### Fase 5: Fuentes externas (nice to have)
1. Scraping de Hashdive para enriquecer señales
2. Integración con Dune Analytics para historial de wallets
3. Monitoreo de @whalewatchpoly u otras cuentas

---

## Notas Importantes

- **No ejecutar trades reales.** Todo es paper trading. La conexión a Polymarket es read-only (WebSocket + REST público, sin auth).
- **Rate limiting:** Respetar los rate limits del CLOB API y de las fuentes externas. No bombardear los endpoints.
- **Costos de IA:** Con el filtro heurístico previo, se estima un máximo de 10-20 invocaciones a Claude por día. A ~$0.01-0.05 por invocación con Sonnet, el costo es despreciable (<$1/día).
- **Timezone:** Todos los timestamps en UTC.
- **Logging:** Log estructurado (JSON) con niveles INFO para trades capturados, WARNING para señales detectadas, ERROR para fallos de conexión.
- **Resiliencia:** El WebSocket se desconecta frecuentemente. Implementar reconnect exponential backoff. No perder trades durante reconexiones (aceptar gaps, no es crítico tener 100% de cobertura).
