-- Generated from schedgen.schema; SQLite dialect. Dates/timestamps are ISO-8601 TEXT.
PRAGMA foreign_keys = ON;

CREATE TABLE product (
  sku TEXT NOT NULL,
  product_name TEXT NOT NULL,
  category TEXT NOT NULL CHECK (category IS NULL OR category IN ('SHAFT', 'HOUSING', 'GEAR', 'BRACKET', 'VALVE', 'FLANGE')),
  changeover_family TEXT NOT NULL CHECK (changeover_family IS NULL OR changeover_family IN ('FAM-A', 'FAM-B', 'FAM-C', 'FAM-D', 'FAM-E', 'FAM-F', 'FAM-G', 'FAM-H')),
  uom TEXT NOT NULL CHECK (uom IS NULL OR uom IN ('EA')),
  standard_batch_qty INTEGER NOT NULL CHECK (standard_batch_qty IS NULL OR (standard_batch_qty >= 1)),
  status TEXT NOT NULL CHECK (status IS NULL OR status IN ('ACTIVE', 'PHASE_OUT', 'DISCONTINUED')),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  PRIMARY KEY (sku)
);

CREATE TABLE routing (
  routing_id TEXT NOT NULL,
  sku TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version IS NULL OR (version >= 1)),
  status TEXT NOT NULL CHECK (status IS NULL OR status IN ('ACTIVE', 'RETIRED')),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  PRIMARY KEY (routing_id),
  UNIQUE (sku, version),
  FOREIGN KEY (sku) REFERENCES product (sku)
);

CREATE TABLE tooling (
  tool_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  machine_group TEXT NOT NULL CHECK (machine_group IS NULL OR machine_group IN ('SAW', 'TURN', 'MILL', 'GRIND', 'WELD', 'HEAT', 'PAINT', 'ASSY', 'TEST')),
  status TEXT NOT NULL CHECK (status IS NULL OR status IN ('ACTIVE')),
  PRIMARY KEY (tool_id)
);

CREATE TABLE routing_step (
  step_id TEXT NOT NULL,
  routing_id TEXT NOT NULL,
  step_seq INTEGER NOT NULL CHECK (step_seq IS NULL OR (step_seq >= 1)),
  step_name TEXT NOT NULL,
  machine_group TEXT NOT NULL CHECK (machine_group IS NULL OR machine_group IN ('SAW', 'TURN', 'MILL', 'GRIND', 'WELD', 'HEAT', 'PAINT', 'ASSY', 'TEST')),
  cycle_min_per_unit REAL NOT NULL CHECK (cycle_min_per_unit IS NULL OR (cycle_min_per_unit >= 0.01)),
  fixed_min INTEGER NOT NULL CHECK (fixed_min IS NULL OR (fixed_min >= 0)),
  operators_required INTEGER NOT NULL CHECK (operators_required IS NULL OR operators_required IN (1, 2)),
  tool_id TEXT,
  PRIMARY KEY (step_id),
  UNIQUE (routing_id, step_seq),
  FOREIGN KEY (routing_id) REFERENCES routing (routing_id),
  FOREIGN KEY (tool_id) REFERENCES tooling (tool_id)
);

CREATE TABLE machine (
  machine_id TEXT NOT NULL,
  machine_name TEXT NOT NULL,
  machine_group TEXT NOT NULL CHECK (machine_group IS NULL OR machine_group IN ('SAW', 'TURN', 'MILL', 'GRIND', 'WELD', 'HEAT', 'PAINT', 'ASSY', 'TEST')),
  initial_family TEXT NOT NULL CHECK (initial_family IS NULL OR initial_family IN ('FAM-A', 'FAM-B', 'FAM-C', 'FAM-D', 'FAM-E', 'FAM-F', 'FAM-G', 'FAM-H')),
  shift_pattern TEXT NOT NULL CHECK (shift_pattern IS NULL OR shift_pattern IN ('P1', 'P2', 'P3', 'P4')),
  status TEXT NOT NULL CHECK (status IS NULL OR status IN ('ACTIVE', 'INACTIVE')),
  commissioned_date TEXT NOT NULL,
  PRIMARY KEY (machine_id)
);

CREATE TABLE routing_step_machine (
  step_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  speed_factor REAL NOT NULL CHECK (speed_factor IS NULL OR (speed_factor >= 0.5 AND speed_factor <= 2.0)),
  PRIMARY KEY (step_id, machine_id),
  FOREIGN KEY (step_id) REFERENCES routing_step (step_id),
  FOREIGN KEY (machine_id) REFERENCES machine (machine_id)
);

CREATE TABLE shift (
  shift_id TEXT NOT NULL CHECK (shift_id IS NULL OR shift_id IN ('SHIFT-A', 'SHIFT-B', 'SHIFT-C')),
  shift_name TEXT NOT NULL,
  start_time TEXT NOT NULL,
  duration_min INTEGER NOT NULL CHECK (duration_min IS NULL OR (duration_min >= 1)),
  PRIMARY KEY (shift_id)
);

CREATE TABLE machine_calendar (
  calendar_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  calendar_date TEXT NOT NULL,
  shift_id TEXT NOT NULL,
  shift_start_ts TEXT NOT NULL,
  shift_end_ts TEXT NOT NULL,
  crew_size INTEGER NOT NULL CHECK (crew_size IS NULL OR (crew_size >= 1 AND crew_size <= 3)),
  capacity_min INTEGER NOT NULL,
  PRIMARY KEY (calendar_id),
  UNIQUE (machine_id, calendar_date, shift_id),
  FOREIGN KEY (machine_id) REFERENCES machine (machine_id),
  FOREIGN KEY (shift_id) REFERENCES shift (shift_id)
);

CREATE TABLE machine_downtime (
  downtime_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  downtime_type TEXT NOT NULL CHECK (downtime_type IS NULL OR downtime_type IN ('PLANNED_MAINTENANCE', 'BREAKDOWN', 'CALIBRATION')),
  start_ts TEXT NOT NULL,
  end_ts TEXT NOT NULL,
  duration_min INTEGER NOT NULL,
  PRIMARY KEY (downtime_id),
  FOREIGN KEY (machine_id) REFERENCES machine (machine_id)
);

CREATE TABLE changeover_matrix (
  changeover_id TEXT NOT NULL,
  machine_group TEXT NOT NULL CHECK (machine_group IS NULL OR machine_group IN ('SAW', 'TURN', 'MILL', 'GRIND', 'WELD', 'HEAT', 'PAINT', 'ASSY', 'TEST')),
  from_family TEXT NOT NULL CHECK (from_family IS NULL OR from_family IN ('FAM-A', 'FAM-B', 'FAM-C', 'FAM-D', 'FAM-E', 'FAM-F', 'FAM-G', 'FAM-H')),
  to_family TEXT NOT NULL CHECK (to_family IS NULL OR to_family IN ('FAM-A', 'FAM-B', 'FAM-C', 'FAM-D', 'FAM-E', 'FAM-F', 'FAM-G', 'FAM-H')),
  setup_min INTEGER NOT NULL CHECK (setup_min IS NULL OR (setup_min >= 1 AND setup_min <= 120)),
  PRIMARY KEY (changeover_id),
  UNIQUE (machine_group, from_family, to_family)
);

CREATE TABLE material (
  material_id TEXT NOT NULL,
  material_name TEXT NOT NULL,
  category TEXT NOT NULL CHECK (category IS NULL OR category IN ('RAW', 'COMPONENT', 'CONSUMABLE')),
  uom TEXT NOT NULL CHECK (uom IS NULL OR uom IN ('KG', 'EA', 'L')),
  lead_time_days INTEGER NOT NULL CHECK (lead_time_days IS NULL OR (lead_time_days >= 0 AND lead_time_days <= 60)),
  PRIMARY KEY (material_id)
);

CREATE TABLE routing_step_material (
  step_id TEXT NOT NULL,
  material_id TEXT NOT NULL,
  qty_per_unit REAL NOT NULL CHECK (qty_per_unit IS NULL OR (qty_per_unit >= 0.001)),
  scrap_pct REAL NOT NULL CHECK (scrap_pct IS NULL OR (scrap_pct >= 0 AND scrap_pct <= 30)),
  PRIMARY KEY (step_id, material_id),
  FOREIGN KEY (step_id) REFERENCES routing_step (step_id),
  FOREIGN KEY (material_id) REFERENCES material (material_id)
);

CREATE TABLE inventory_lot (
  lot_id TEXT NOT NULL,
  material_id TEXT NOT NULL,
  qty_received INTEGER NOT NULL CHECK (qty_received IS NULL OR (qty_received >= 1)),
  promised_ts TEXT NOT NULL,
  available_ts TEXT NOT NULL,
  delay_min INTEGER NOT NULL,
  qty_consumed INTEGER NOT NULL,
  qty_remaining INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IS NULL OR status IN ('ON_ORDER', 'DELAYED', 'AVAILABLE')),
  PRIMARY KEY (lot_id),
  FOREIGN KEY (material_id) REFERENCES material (material_id)
);

CREATE TABLE production_order (
  order_id TEXT NOT NULL,
  sku TEXT NOT NULL,
  routing_id TEXT NOT NULL,
  qty INTEGER NOT NULL CHECK (qty IS NULL OR (qty >= 1)),
  order_type TEXT NOT NULL CHECK (order_type IS NULL OR order_type IN ('STANDARD', 'URGENT')),
  priority INTEGER NOT NULL CHECK (priority IS NULL OR priority IN (1, 2, 3, 4)),
  created_ts TEXT NOT NULL,
  release_ts TEXT NOT NULL,
  due_ts TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IS NULL OR status IN ('CREATED', 'SCHEDULED', 'UNSCHEDULABLE')),
  total_process_min INTEGER,
  planned_start_ts TEXT,
  planned_finish_ts TEXT,
  lateness_min INTEGER,
  PRIMARY KEY (order_id),
  FOREIGN KEY (sku) REFERENCES product (sku),
  FOREIGN KEY (routing_id) REFERENCES routing (routing_id)
);

CREATE TABLE operation (
  op_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  op_seq INTEGER NOT NULL CHECK (op_seq IS NULL OR (op_seq >= 1)),
  machine_group TEXT NOT NULL CHECK (machine_group IS NULL OR machine_group IN ('SAW', 'TURN', 'MILL', 'GRIND', 'WELD', 'HEAT', 'PAINT', 'ASSY', 'TEST')),
  operators_required INTEGER NOT NULL CHECK (operators_required IS NULL OR operators_required IN (1, 2)),
  tool_id TEXT,
  qty INTEGER NOT NULL CHECK (qty IS NULL OR (qty >= 1)),
  std_process_min INTEGER NOT NULL CHECK (std_process_min IS NULL OR (std_process_min >= 1)),
  status TEXT NOT NULL CHECK (status IS NULL OR status IN ('PLANNED', 'SCHEDULED', 'UNSCHEDULED')),
  PRIMARY KEY (op_id),
  UNIQUE (order_id, op_seq),
  FOREIGN KEY (order_id) REFERENCES production_order (order_id),
  FOREIGN KEY (step_id) REFERENCES routing_step (step_id),
  FOREIGN KEY (tool_id) REFERENCES tooling (tool_id)
);

CREATE TABLE operation_schedule (
  schedule_id TEXT NOT NULL,
  op_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  setup_min INTEGER NOT NULL CHECK (setup_min IS NULL OR (setup_min >= 0)),
  setup_start_ts TEXT NOT NULL,
  start_ts TEXT NOT NULL,
  end_ts TEXT NOT NULL,
  process_min INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IS NULL OR status IN ('PLANNED')),
  PRIMARY KEY (schedule_id),
  UNIQUE (op_id),
  FOREIGN KEY (op_id) REFERENCES operation (op_id),
  FOREIGN KEY (machine_id) REFERENCES machine (machine_id)
);

CREATE TABLE material_consumption (
  consumption_id TEXT NOT NULL,
  op_id TEXT NOT NULL,
  material_id TEXT NOT NULL,
  lot_id TEXT NOT NULL,
  qty INTEGER NOT NULL CHECK (qty IS NULL OR (qty >= 1)),
  consumed_ts TEXT NOT NULL,
  PRIMARY KEY (consumption_id),
  FOREIGN KEY (op_id) REFERENCES operation (op_id),
  FOREIGN KEY (material_id) REFERENCES material (material_id),
  FOREIGN KEY (lot_id) REFERENCES inventory_lot (lot_id)
);

CREATE TABLE status_event (
  event_id TEXT NOT NULL,
  entity_type TEXT NOT NULL CHECK (entity_type IS NULL OR entity_type IN ('ORDER', 'OPERATION', 'LOT')),
  entity_id TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  event_ts TEXT NOT NULL,
  reason_code TEXT,
  PRIMARY KEY (event_id)
);

CREATE TABLE schedule_exception (
  exception_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  op_id TEXT,
  exception_type TEXT NOT NULL CHECK (exception_type IS NULL OR exception_type IN ('MATERIAL_SHORTAGE', 'NO_CAPACITY_IN_HORIZON', 'LATE_ORDER')),
  root_cause TEXT NOT NULL CHECK (root_cause IS NULL OR root_cause IN ('MATERIAL_SHORTAGE', 'NO_CAPACITY_IN_HORIZON', 'DUE_DATE_TOO_TIGHT', 'MATERIAL_DELAY', 'MACHINE_CAPACITY')),
  lateness_min INTEGER,
  detail TEXT NOT NULL,
  PRIMARY KEY (exception_id),
  FOREIGN KEY (order_id) REFERENCES production_order (order_id),
  FOREIGN KEY (op_id) REFERENCES operation (op_id)
);

