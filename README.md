# Polymarket Insider Trading Detection & Paper Trading Bot

Sistema que detecta posible insider trading en Polymarket analizando patrones de trading en tiempo real, y simula operaciones de paper trading para evaluar rentabilidad.

El sistema **no ejecuta trades reales**. Todo es simulación sobre datos reales de mercado (read-only).

## Cómo funciona

### 1. Data Ingestion

El bot ingiere datos de tres fuentes en paralelo:

- **Polymarket Data API** (cada 15s) — trades recientes con wallet addresses. Solo se guardan trades >= `MIN_TRADE_USD` ($2,500 por defecto).
- **Polymarket Gamma API** (cada 5min) — metadata de ~2,000 mercados activos (pregunta, categoría, precios, liquidez, fecha de resolución).
- **CLOB WebSocket** (8 conexiones paralelas) — price updates en tiempo real para ~4,000 tokens.
- **CLOB Order Book** (cada 60s) — depth de mercado para calcular slippage dinámico.

### 2. Detection Engine

Cada trade guardado en la DB pasa por dos filtros:

**Filtro 1 — 8 reglas heurísticas** que evalúan patrones sospechosos:

| Regla | Prioridad | Qué detecta |
|-------|-----------|-------------|
| `COORDINATED_WALLETS` | 9 | 3+ wallets nuevas comprando el mismo outcome en 1 hora |
| `WHALE_NEW_ACCOUNT` | 8 | Wallet < 7 días con < 20 trades haciendo una apuesta grande |
| `VOLUME_SPIKE` | 8 | Volumen en la última hora > 2.5x el promedio |
| `PRE_ANNOUNCEMENT` | 8 | Trade grande < 48h antes de la resolución del mercado |
| `IMPROBABLE_BET` | 7 | Apuesta grande a un outcome con < 22% de probabilidad |
| `PRICE_REVERSAL` | 7 | Spike de precio > 10% seguido de reversión > 50% |
| `BET_AGAINST_CONSENSUS` | 7 | Apuesta grande contra > 72% de consenso del mercado |
| `HIGH_WIN_RATE_WHALE` | 6 | Wallet con win rate > 85% y > 10 trades históricos |

Los perfiles de wallet se obtienen en tiempo real de la **Data API de Polymarket** (`/activity` y `/positions`), no de la DB local. Esto garantiza datos precisos de antigüedad, volumen total, win rate, y temas en los que opera cada wallet.

Cada trade evaluado muestra qué reglas matchearon y cuáles no (con la razón), visible en la página Monitor del frontend.

**Filtro 2 — Claude Sonnet + Web Search** (solo si alguna regla matchea con prioridad >= 6):

- Recibe: reglas activadas, mercado, perfil real del wallet, contexto de volumen.
- Busca en la web: noticias recientes, anuncios programados, quién tendría info privilegiada.
- Produce: score (1-10), confidence (0-1), recommendation (STRONG_BUY/BUY/HOLD/SKIP).
- Si encuentra noticias que justifican el movimiento, reduce el score.
- Cache de 6 horas por mercado. Límite: 50 calls/día.

### 3. Paper Trading Engine

Cuando una señal tiene score >= `MIN_SCORE_TO_TRADE` (5 por defecto), el bot abre una posición simulada:

**Position sizing (Half-Kelly Criterion):**

| Score | Max % del portfolio | Win prob prior |
|-------|-------------------|----------------|
| 5-6 | 3% | 55% |
| 7 | 7% | 65% |
| 8-10 | 20% | 80% |

El tamaño se multiplica por la confidence de la IA (mínimo 30% del Kelly). El slippage se calcula dinámicamente desde el order book real.

**Pre-checks antes de abrir:**
- Circuit breaker: no opera si el P&L del día es < -5%.
- Máximo 10 posiciones abiertas simultáneas.
- Concentración por categoría < 40% del portfolio.

**6 condiciones de salida (evaluadas cada 60s):**

1. **Resolución del mercado** — cierra a $1.00 o $0.00.
2. **Trailing stop** — si el profit supera 40%, el stop sube a breakeven.
3. **Stop loss dinámico** — -30% para score >= 8, -50% para score < 8.
4. **Take profit** — 80% del potencial máximo.
5. **Near-resolution** — < 30 minutos para resolución + en profit.
6. **Time decay** — > 14 días abierta.

### 4. Learning

- **Outcome tracker** (cada 5min) — compara predicciones vs resultados reales de mercados resueltos.
- **Score bracket stats** — registra win rate por bracket de score y ajusta el Kelly sizing automáticamente (calibración Bayesiana).
- **Rule performance** — trackea qué reglas generan más wins vs losses.

### 5. Maintenance

- **Cleanup** (cada 1h) — borra trades > 24h no vinculados a señales, volume snapshots > 48h, activity logs > 7 días.
- **Cache eviction** — order book max 200 entries, wallet profiles max 500.
- **DB pool optimizado** — 5 conexiones + 3 overflow (diseñado para VMs con 1GB RAM).

## Stack

| Componente | Tecnología |
|------------|-----------|
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2.0 async, asyncpg |
| Base de datos | PostgreSQL 15 |
| Frontend | React 18, Tailwind CSS, Recharts |
| AI | Claude Sonnet (Anthropic API) con web search |
| WebSocket | websockets (Python), 8 conexiones paralelas |
| HTTP | httpx (async) |
| Migrations | Alembic |
| Containers | Docker Compose |
| Deploy | Docker Hub + Google Cloud e2-micro |

## Frontend

El dashboard tiene 5 páginas:

- **Dashboard** — portfolio value, equity curve, señales activas, posiciones abiertas, activity feed (solo eventos importantes).
- **Monitor** — tabla de trades en la DB con market names, pipeline de detección con resultados de cada regla expandibles, razonamiento de la IA.
- **Signals** — lista filtrable de señales (por score, status, categoría).
- **Trades** — historial de paper trades con P&L.
- **Analytics** — métricas de rendimiento, win rate por score bracket, P&L por categoría, distribución de retornos.

## Setup

### Requisitos

- Python 3.11+
- Node.js 18+
- Docker
- Cuenta de Anthropic con API key
- VPN si Polymarket está bloqueado en tu país (solo para desarrollo local)

### Instalación local

```bash
git clone https://github.com/manuader/polymarket-bot.git
cd polymarket-bot
cp .env.example .env
# Editar .env con tu ANTHROPIC_API_KEY
```

Levantar PostgreSQL:

```bash
docker compose up -d db
```

Instalar dependencias y crear tablas:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
cd backend
alembic upgrade head
```

Iniciar el backend:

```bash
uvicorn main:app --reload
```

Iniciar el frontend (en otra terminal):

```bash
cd frontend
npm install
npm run dev
```

Abrir http://localhost:5173

### Deploy en producción (Google Cloud)

El proyecto se deploya como una imagen Docker pre-buildeada en Docker Hub.

**Build y push desde tu Mac:**

```bash
docker buildx build --platform linux/amd64 -f Dockerfile.prod -t manuader/polymarket-bot:latest --push .
```

**En la VM (Google Cloud e2-micro, Ubuntu 22.04):**

```bash
# Setup inicial (solo la primera vez)
sudo apt update && sudo apt install -y docker.io docker-compose git
sudo usermod -aG docker $USER && newgrp docker
git clone https://github.com/manuader/polymarket-bot.git
cd polymarket-bot
cp .env.example .env
nano .env  # configurar ANTHROPIC_API_KEY y MIN_TRADE_USD

# Deploy
docker-compose -f docker-compose.prod.yml pull
docker-compose -f docker-compose.prod.yml up -d
sleep 15
docker-compose -f docker-compose.prod.yml exec backend alembic upgrade head
docker-compose -f docker-compose.prod.yml restart backend
```

**Updates posteriores:**

```bash
cd ~/polymarket-bot
docker-compose -f docker-compose.prod.yml pull backend
docker rm -f $(docker ps -aq) 2>/dev/null
docker-compose -f docker-compose.prod.yml up -d
sleep 15
docker-compose -f docker-compose.prod.yml exec backend alembic upgrade head
docker-compose -f docker-compose.prod.yml restart backend
```

### Tests

```bash
cd backend
python -m pytest tests/ -v
```

59 tests unitarios cubriendo: parsing de trades y mercados, Kelly criterion, slippage, composite scoring, order book, AI cost tracking, y configuración de reglas.

## Configuración

Todas las variables están en `.env`:

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DATABASE_URL` | `...localhost:5433/...` | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | — | API key de Anthropic (requerida para AI) |
| `MIN_TRADE_USD` | 2500 | Monto mínimo para guardar y analizar un trade |
| `MIN_SCORE_TO_TRADE` | 5 | Score mínimo para abrir paper trade |
| `MAX_AI_CALLS_PER_DAY` | 50 | Límite de invocaciones a Claude por día |
| `INITIAL_BALANCE` | 10000 | Balance inicial del paper trading (USDC) |
| `MAX_POSITION_PCT` | 20 | % máximo del portfolio por posición |
| `MAX_POSITIONS` | 10 | Máximo posiciones abiertas simultáneas |
| `STOP_LOSS_PCT_HIGH` | 30 | Stop loss % para scores >= 8 |
| `STOP_LOSS_PCT_LOW` | 50 | Stop loss % para scores < 8 |
| `TAKE_PROFIT_PCT` | 80 | Take profit % del potencial máximo |
| `TRAILING_STOP_TRIGGER_PCT` | 40 | Profit % para activar trailing stop |
| `CIRCUIT_BREAKER_PCT` | 5 | Stop trading si P&L día < -X% |
| `CATEGORY_CONCENTRATION_MAX_PCT` | 40 | Max % del portfolio en una categoría |

## API Endpoints

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/dashboard/summary` | Portfolio + métricas del día |
| `GET` | `/api/dashboard/equity-curve` | Valor del portfolio en el tiempo |
| `GET` | `/api/dashboard/active-signals` | Señales últimas 24h |
| `GET` | `/api/dashboard/open-positions` | Posiciones abiertas con P&L |
| `GET` | `/api/signals/` | Lista de señales con filtros |
| `GET` | `/api/signals/{id}` | Detalle de una señal |
| `GET` | `/api/trades/` | Historial de paper trades |
| `GET` | `/api/analytics/performance` | Win rate, Sharpe, drawdown, profit factor |
| `GET` | `/api/analytics/by-category` | P&L por categoría |
| `GET` | `/api/analytics/by-score` | P&L por bracket de score |
| `GET` | `/api/analytics/return-distribution` | Histograma de retornos |
| `GET` | `/api/activity/feed` | Activity feed del bot |
| `GET` | `/api/activity/stats` | AI calls, tokens, costos |
| `GET` | `/api/activity/recent-trades` | Trades recientes de la DB con market info |
| `GET` | `/api/activity/learning` | Qué aprendió el bot de sus resultados |
| `WS` | `/ws` | WebSocket para updates real-time al frontend |

## Background Tasks

El backend ejecuta 10 tasks en paralelo:

| Task | Frecuencia | Función |
|------|-----------|---------|
| Market sync | 5 min | Sincroniza ~2,000 mercados de Gamma API |
| Trade enricher | 15 seg | Trae trades >= MIN_TRADE_USD de Data API |
| Volume tracker | 60 seg | Snapshots de volumen 1h/4h/24h por mercado |
| Wallet profiler | On-demand | Perfil real via API cuando una regla lo necesita |
| Orderbook cache | 60 seg | Depth para slippage dinámico |
| WebSocket | Real-time | 8 conexiones, ~4,000 tokens suscritos |
| Detection engine | Continuo | Procesa trades de la queue contra 8 reglas |
| Paper engine | 60 seg | Chequea condiciones de salida de posiciones |
| Outcome tracker | 5 min | Registra wins/losses para calibración |
| Cleanup | 1 hora | Purga datos viejos (trades, snapshots, activity) |

## Costos estimados

- **Claude Sonnet** — ~$0.01-0.05 por análisis. Con filtro heurístico previo, ~10-20 llamadas/día = < $1/día.
- **Google Cloud e2-micro** — $0/mes (free tier).
- **Polymarket APIs** — $0 (públicas, read-only, sin autenticación).
- **Docker Hub** — $0 (free tier para imágenes públicas).
