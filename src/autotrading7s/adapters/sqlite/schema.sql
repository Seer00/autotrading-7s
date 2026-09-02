-- AutoTrading 7s 스키마 v1 — 설계서 12절
-- 비율은 TEXT(Decimal 문자열), 시각은 TEXT(ISO 8601, tz-aware 필수).

CREATE TABLE schema_version (
  version INTEGER NOT NULL
);

CREATE TABLE split_config (
  id INTEGER PRIMARY KEY,
  stock_code TEXT NOT NULL,
  stock_name TEXT,
  label TEXT,
  max_stages INTEGER NOT NULL CHECK(max_stages BETWEEN 2 AND 7),
  drop_pct TEXT NOT NULL,
  target_pct TEXT NOT NULL,
  amount_per_stage INTEGER NOT NULL CHECK(amount_per_stage > 0),
  allow_rebuy INTEGER NOT NULL DEFAULT 1 CHECK(allow_rebuy IN (0, 1)),
  rebuy_cooldown_sec INTEGER NOT NULL DEFAULT 60 CHECK(rebuy_cooldown_sec >= 0),
  total_limit INTEGER NOT NULL CHECK(total_limit >= 0),
  status TEXT NOT NULL CHECK(status IN ('IDLE', 'ACTIVE')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(stock_code, label)
);

CREATE TABLE cycle (
  id INTEGER PRIMARY KEY,
  config_id INTEGER NOT NULL REFERENCES split_config(id),
  seq INTEGER NOT NULL CHECK(seq >= 1),
  status TEXT NOT NULL CHECK(status IN
    ('STARTING', 'RUNNING', 'PAUSED', 'LIQUIDATING', 'CLOSED')),
  anchor_price INTEGER CHECK(anchor_price IS NULL OR anchor_price > 0),
  ladder_json TEXT,
  realized_pnl INTEGER,
  close_reason TEXT CHECK(close_reason IS NULL OR close_reason IN
    ('NORMAL', 'EMERGENCY', 'FORCED')),
  forced_close_reason TEXT,
  forced_close_qty INTEGER CHECK(forced_close_qty IS NULL OR forced_close_qty > 0),
  started_at TEXT NOT NULL,
  closed_at TEXT,
  UNIQUE(config_id, seq),
  -- D20: FORCED 는 증언과 잔량이 둘 다 있어야 한다
  CHECK(close_reason IS NOT 'FORCED'
        OR (forced_close_reason IS NOT NULL AND forced_close_qty IS NOT NULL))
);

CREATE TABLE stage_state (
  id INTEGER PRIMARY KEY,
  cycle_id INTEGER NOT NULL REFERENCES cycle(id),
  stage_no INTEGER NOT NULL CHECK(stage_no BETWEEN 1 AND 7),
  status TEXT NOT NULL CHECK(status IN
    ('WAITING', 'BUY_PENDING', 'HOLDING', 'SELL_PENDING', 'SOLD')),
  trigger_price INTEGER NOT NULL CHECK(trigger_price > 0),
  planned_qty INTEGER NOT NULL CHECK(planned_qty >= 0),
  fill_price INTEGER CHECK(fill_price IS NULL OR fill_price > 0),
  fill_qty INTEGER CHECK(fill_qty IS NULL OR fill_qty > 0),
  bought_at TEXT,
  last_sold_at TEXT,
  rebuy_count INTEGER NOT NULL DEFAULT 0 CHECK(rebuy_count >= 0),
  UNIQUE(cycle_id, stage_no),
  -- 도메인의 StageState 불변식을 스키마에서도 강제한다
  CHECK(status NOT IN ('HOLDING', 'SELL_PENDING')
        OR (fill_price IS NOT NULL AND fill_qty IS NOT NULL))
);

CREATE TABLE order_log (
  id INTEGER PRIMARY KEY,
  client_ref TEXT NOT NULL UNIQUE,
  cycle_id INTEGER NOT NULL REFERENCES cycle(id),
  stage_state_id INTEGER REFERENCES stage_state(id),
  side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
  order_type TEXT NOT NULL CHECK(order_type IN ('LIMIT', 'MARKET')),
  path TEXT NOT NULL CHECK(path IN ('TRIGGER', 'EMERGENCY')),
  req_price INTEGER,
  req_qty INTEGER NOT NULL CHECK(req_qty > 0),
  fill_price INTEGER,
  fill_qty INTEGER,
  status TEXT NOT NULL CHECK(status IN
    ('SENDING', 'ACCEPTED', 'PARTIAL', 'FILLED', 'CANCELED', 'REJECTED', 'UNKNOWN')),
  broker_order_id TEXT,
  api_code TEXT,
  api_message TEXT,
  trigger_reason TEXT NOT NULL,
  tick_price INTEGER,
  tick_source TEXT CHECK(tick_source IS NULL OR tick_source IN ('WS', 'REST_POLL')),
  sent_at TEXT NOT NULL,
  settled_at TEXT,
  -- 자동 트리거 경로는 시장가를 낼 수 없다 (설계서 6절)
  CHECK(path IS NOT 'TRIGGER' OR order_type = 'LIMIT')
);

CREATE INDEX idx_order_log_cycle ON order_log(cycle_id);
CREATE INDEX idx_order_log_status ON order_log(status);

CREATE TABLE emergency_liquidation_log (
  id INTEGER PRIMARY KEY,
  scope TEXT NOT NULL CHECK(scope IN ('SINGLE', 'ALL')),
  stock_code TEXT,
  cycle_id INTEGER REFERENCES cycle(id),
  requested_at TEXT NOT NULL,
  reason TEXT,
  qty_before INTEGER,
  qty_after INTEGER,
  canceled_orders INTEGER,
  result TEXT NOT NULL CHECK(result IN
    ('SUCCESS', 'PARTIAL', 'FAILED', 'REJECTED_CLOSED_MARKET', 'FORCED_CLOSE')),
  detail_json TEXT,
  completed_at TEXT
);

CREATE TABLE token_session (
  id INTEGER PRIMARY KEY,
  env TEXT NOT NULL CHECK(env IN ('MOCK', 'REAL')),
  app_key_hash TEXT NOT NULL,
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE reconcile_log (
  id INTEGER PRIMARY KEY,
  checked_at TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  internal_qty INTEGER NOT NULL,
  broker_qty INTEGER NOT NULL,
  verdict TEXT NOT NULL CHECK(verdict IN
    ('MATCH', 'INTERNAL_LESS', 'INTERNAL_MORE')),
  action_taken TEXT
);

-- 설계서 12.3절. 현재가·평가손익은 실시간 값이므로 뷰에 담지 않는다.
CREATE VIEW holdings AS
SELECT cfg.stock_code,
       cfg.stock_name,
       cfg.label,
       cy.id                                                AS cycle_id,
       SUM(ss.fill_qty)                                     AS total_qty,
       SUM(ss.fill_price * ss.fill_qty) / SUM(ss.fill_qty)   AS avg_price,
       COUNT(*)                                             AS holding_stages,
       cfg.max_stages,
       cy.status                                            AS cycle_status
FROM stage_state ss
JOIN cycle cy         ON cy.id  = ss.cycle_id
JOIN split_config cfg ON cfg.id = cy.config_id
WHERE ss.status IN ('HOLDING', 'SELL_PENDING')
  AND cy.status != 'CLOSED'
GROUP BY cy.id;
