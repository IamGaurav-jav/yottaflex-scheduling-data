"""Single source of truth for every table: used for CSV headers, SQL DDL,
the data dictionary, and schema-level validation (types, enums, ranges)."""
from dataclasses import dataclass

FAMILIES = tuple(f"FAM-{c}" for c in "ABCDEFGH")
GROUPS = ("SAW", "TURN", "MILL", "GRIND", "WELD", "HEAT", "PAINT", "ASSY", "TEST")
CATEGORIES = ("SHAFT", "HOUSING", "GEAR", "BRACKET", "VALVE", "FLANGE")
PRODUCT_STATUS = ("ACTIVE", "PHASE_OUT", "DISCONTINUED")
ROUTING_STATUS = ("ACTIVE", "RETIRED")
SHIFT_PATTERNS = ("P1", "P2", "P3", "P4")
MACHINE_STATUS = ("ACTIVE", "INACTIVE")
DOWNTIME_TYPES = ("PLANNED_MAINTENANCE", "BREAKDOWN", "CALIBRATION")
MATERIAL_CATEGORIES = ("RAW", "COMPONENT", "CONSUMABLE")
UOMS = ("KG", "EA", "L")
LOT_STATUS = ("ON_ORDER", "DELAYED", "AVAILABLE")
ORDER_TYPES = ("STANDARD", "URGENT")
ORDER_STATUS = ("CREATED", "SCHEDULED", "UNSCHEDULABLE")
OP_STATUS = ("PLANNED", "SCHEDULED", "UNSCHEDULED")
SCHED_STATUS = ("PLANNED",)
ENTITY_TYPES = ("ORDER", "OPERATION", "LOT")
EXCEPTION_TYPES = ("MATERIAL_SHORTAGE", "NO_CAPACITY_IN_HORIZON", "LATE_ORDER")
ROOT_CAUSES = ("MATERIAL_SHORTAGE", "NO_CAPACITY_IN_HORIZON", "DUE_DATE_TOO_TIGHT",
               "MATERIAL_DELAY", "MACHINE_CAPACITY")
ISSUE_TYPES = ("OPERATIONS_OVERLAP", "PRECEDENCE_VIOLATED", "MATERIAL_CONSUMED_BEFORE_AVAILABLE",
               "SCHEDULE_OUTSIDE_SHIFT_CALENDAR", "CHANGEOVER_OMITTED")

# Allowed lifecycle transitions: entity type -> {from_state: {to_states}}; None = initial.
TRANSITIONS = {
    "ORDER": {None: {"CREATED"}, "CREATED": {"SCHEDULED", "UNSCHEDULABLE"}},
    "OPERATION": {None: {"PLANNED"}, "PLANNED": {"SCHEDULED", "UNSCHEDULED"}},
    "LOT": {None: {"ON_ORDER"}, "ON_ORDER": {"AVAILABLE", "DELAYED"}, "DELAYED": {"AVAILABLE"}},
}


@dataclass(frozen=True)
class Field:
    name: str
    type: str  # str | int | float | bool | date | ts
    desc: str
    required: bool = True
    enum: tuple = ()
    unit: str = ""
    unique: bool = False
    rule: str = ""
    pattern: str = ""
    min: float = None
    max: float = None
    derived: bool = False


@dataclass(frozen=True)
class Table:
    name: str
    entity: str
    desc: str
    pk: tuple
    fields: tuple
    fks: tuple = ()  # (cols, ref_table, ref_cols, cardinality)
    uniques: tuple = ()
    core: bool = True

    def cols(self):
        return [f.name for f in self.fields]


def F(name, type, desc, **kw):
    return Field(name, type, desc, **kw)


TABLES = [
    Table("product", "Product", "Sellable item (SKU). Master data; parent of Routing and ProductionOrder.",
          ("sku",),
          (F("sku", "str", "Stock keeping unit, immutable business key.", pattern=r"^SKU-\d{3}$", unique=True),
           F("product_name", "str", "Descriptive name."),
           F("category", "str", "Product category; drives routing template.", enum=CATEGORIES),
           F("changeover_family", "str", "Setup family used by the ChangeoverMatrix.", enum=FAMILIES),
           F("uom", "str", "Unit of measure for order quantity.", enum=("EA",)),
           F("standard_batch_qty", "int", "Typical batch size.", unit="EA", min=1),
           F("status", "str", "Lifecycle state. DISCONTINUED products must not be ordered.", enum=PRODUCT_STATUS),
           F("effective_from", "date", "First date the SKU may be ordered."),
           F("effective_to", "date", "Last orderable date; required iff DISCONTINUED.", required=False,
             rule="effective_to >= effective_from")),
          core=True),
    Table("routing", "Routing", "Ordered list of steps to make a SKU. A SKU can have several dated versions.",
          ("routing_id",),
          (F("routing_id", "str", "ROUTE-<sku>-<version>.", pattern=r"^ROUTE-SKU-\d{3}-\d{2}$", unique=True),
           F("sku", "str", "Owning product."),
           F("version", "int", "Routing version number.", min=1),
           F("status", "str", "ACTIVE or RETIRED.", enum=ROUTING_STATUS),
           F("effective_from", "date", "Start of validity."),
           F("effective_to", "date", "End of validity; NULL = open ended.", required=False,
             rule="effective_to >= effective_from")),
          fks=((("sku",), "product", ("sku",), "Product 1:N Routing"),),
          uniques=(("sku", "version"),)),
    Table("tooling", "Tooling", "Shared fixture/tool; one physical unit that only one operation can use at a time.",
          ("tool_id",),
          (F("tool_id", "str", "Tool identifier.", pattern=r"^TOOL-\d{3}$", unique=True),
           F("tool_name", "str", "Name."),
           F("machine_group", "str", "Machine group the tool fits.", enum=GROUPS),
           F("status", "str", "Tool status.", enum=("ACTIVE",))),
          core=False),
    Table("routing_step", "Routing step (operation template)", "One step of a routing; defines machine group, "
          "durations, operator and tooling requirements.",
          ("step_id",),
          (F("step_id", "str", "<routing_id>-S<seq>.", pattern=r"^ROUTE-SKU-\d{3}-\d{2}-S\d{2}$", unique=True),
           F("routing_id", "str", "Parent routing."),
           F("step_seq", "int", "1-based sequence inside the routing.", min=1),
           F("step_name", "str", "Name of the step."),
           F("machine_group", "str", "Required machine group.", enum=GROUPS),
           F("cycle_min_per_unit", "float", "Nominal processing time per unit at speed_factor 1.0.",
             unit="min/unit", min=0.01),
           F("fixed_min", "int", "Fixed handling time per operation.", unit="min", min=0),
           F("operators_required", "int", "Crew needed; only shifts with crew_size >= this can run the step.",
             enum=(1, 2)),
           F("tool_id", "str", "Shared tool required (optional).", required=False)),
          fks=((("routing_id",), "routing", ("routing_id",), "Routing 1:N RoutingStep"),
               (("tool_id",), "tooling", ("tool_id",), "Tooling 1:N RoutingStep (optional)")),
          uniques=(("routing_id", "step_seq"),), core=False),
    Table("machine", "Machine", "Physical resource.",
          ("machine_id",),
          (F("machine_id", "str", "Machine identifier.", pattern=r"^MACH-\d{3}$", unique=True),
           F("machine_name", "str", "Name."),
           F("machine_group", "str", "Machine group.", enum=GROUPS),
           F("initial_family", "str", "Changeover family loaded at the start of the horizon.", enum=FAMILIES),
           F("shift_pattern", "str", "P1=A Mon-Fri, P2=A+B Mon-Fri, P3=A+B Mon-Sat, P4=A+B+C Mon-Fri.",
             enum=SHIFT_PATTERNS),
           F("status", "str", "Machine status.", enum=MACHINE_STATUS),
           F("commissioned_date", "date", "Date machine entered service.")),
          ),
    Table("routing_step_machine", "Step-machine eligibility (bridge)",
          "Which machines can run a step and how fast (N:M).",
          ("step_id", "machine_id"),
          (F("step_id", "str", "Routing step."),
           F("machine_id", "str", "Eligible machine; must belong to the step's machine_group."),
           F("speed_factor", "float", "Relative speed; process_min = ceil(std_process_min / speed_factor).",
             min=0.5, max=2.0)),
          fks=((("step_id",), "routing_step", ("step_id",), "RoutingStep N:M Machine"),
               (("machine_id",), "machine", ("machine_id",), "RoutingStep N:M Machine")), core=False),
    Table("shift", "Shift", "Labour shift definition.",
          ("shift_id",),
          (F("shift_id", "str", "Shift code.", enum=("SHIFT-A", "SHIFT-B", "SHIFT-C"), unique=True),
           F("shift_name", "str", "Name."),
           F("start_time", "str", "Local start HH:MM.", pattern=r"^\d{2}:\d{2}$"),
           F("duration_min", "int", "Length; SHIFT-C crosses midnight.", unit="min", min=1)),
          core=False),
    Table("machine_calendar", "MachineCalendar", "Staffed working window of a machine on a date. Only staffed "
          "windows exist; absence of a row means the machine is not staffed.",
          ("calendar_id",),
          (F("calendar_id", "str", "Calendar row id.", pattern=r"^CAL-\d{6}$", unique=True),
           F("machine_id", "str", "Machine."),
           F("calendar_date", "date", "Shift start date."),
           F("shift_id", "str", "Shift."),
           F("shift_start_ts", "ts", "Window start."),
           F("shift_end_ts", "ts", "Window end.", rule="> shift_start_ts"),
           F("crew_size", "int", "Operators present.", min=1, max=3),
           F("capacity_min", "int", "Derived: shift_end_ts - shift_start_ts.", unit="min", derived=True)),
          fks=((("machine_id",), "machine", ("machine_id",), "Machine 1:N MachineCalendar"),
               (("shift_id",), "shift", ("shift_id",), "Shift 1:N MachineCalendar")),
          uniques=(("machine_id", "calendar_date", "shift_id"),)),
    Table("machine_downtime", "MachineDowntime", "Known unavailability (maintenance, breakdown).",
          ("downtime_id",),
          (F("downtime_id", "str", "Downtime id.", pattern=r"^DT-\d{6}$", unique=True),
           F("machine_id", "str", "Machine."),
           F("downtime_type", "str", "Type.", enum=DOWNTIME_TYPES),
           F("start_ts", "ts", "Start."),
           F("end_ts", "ts", "End.", rule="> start_ts"),
           F("duration_min", "int", "Derived: end - start.", unit="min", derived=True)),
          fks=((("machine_id",), "machine", ("machine_id",), "Machine 1:N MachineDowntime"),), core=False),
    Table("changeover_matrix", "ChangeoverMatrix", "Sequence-dependent setup time by machine group and "
          "from/to family. Asymmetric. Diagonal (same family) is a short clean-down, never zero.",
          ("changeover_id",),
          (F("changeover_id", "str", "Row id.", pattern=r"^CO-\d{6}$", unique=True),
           F("machine_group", "str", "Machine group.", enum=GROUPS),
           F("from_family", "str", "Previous family.", enum=FAMILIES),
           F("to_family", "str", "Next family.", enum=FAMILIES),
           F("setup_min", "int", "Required setup.", unit="min", min=1, max=120)),
          uniques=(("machine_group", "from_family", "to_family"),)),
    Table("material", "Material", "Raw material, component or consumable.",
          ("material_id",),
          (F("material_id", "str", "Material id.", pattern=r"^MAT-\d{3}$", unique=True),
           F("material_name", "str", "Name."),
           F("category", "str", "Category.", enum=MATERIAL_CATEGORIES),
           F("uom", "str", "Unit of measure.", enum=UOMS),
           F("lead_time_days", "int", "Nominal supplier lead time.", unit="day", min=0, max=60)),
          ),
    Table("routing_step_material", "Step-material requirement (bridge)", "Material consumed per unit by a step (N:M).",
          ("step_id", "material_id"),
          (F("step_id", "str", "Routing step."),
           F("material_id", "str", "Material."),
           F("qty_per_unit", "float", "Quantity per finished unit.", unit="material uom", min=0.001),
           F("scrap_pct", "float", "Scrap allowance.", unit="%", min=0, max=30)),
          fks=((("step_id",), "routing_step", ("step_id",), "RoutingStep N:M Material"),
               (("material_id",), "material", ("material_id",), "RoutingStep N:M Material")), core=False),
    Table("inventory_lot", "InventoryLot", "Received/expected lot of a material. available_ts is when stock "
          "can first be consumed.",
          ("lot_id",),
          (F("lot_id", "str", "Lot id.", pattern=r"^LOT-\d{6}$", unique=True),
           F("material_id", "str", "Material."),
           F("qty_received", "int", "Lot quantity.", unit="material uom", min=1),
           F("promised_ts", "ts", "Supplier promised availability."),
           F("available_ts", "ts", "Actual availability; >= promised_ts.", rule=">= promised_ts"),
           F("delay_min", "int", "Derived: available_ts - promised_ts.", unit="min", derived=True),
           F("qty_consumed", "int", "Derived: sum of MaterialConsumption.qty.", derived=True),
           F("qty_remaining", "int", "Derived: qty_received - qty_consumed; must be >= 0.", derived=True),
           F("status", "str", "Latest lifecycle state (from StatusEvent).", enum=LOT_STATUS, derived=True)),
          fks=((("material_id",), "material", ("material_id",), "Material 1:N InventoryLot"),)),
    Table("production_order", "ProductionOrder", "Demand to produce qty of a SKU by a due date.",
          ("order_id",),
          (F("order_id", "str", "Order id.", pattern=r"^PROD-2026-\d{6}$", unique=True),
           F("sku", "str", "Product."),
           F("routing_id", "str", "Routing version valid on release date."),
           F("qty", "int", "Ordered units.", unit="EA", min=1),
           F("order_type", "str", "STANDARD or URGENT.", enum=ORDER_TYPES),
           F("priority", "int", "1=urgent .. 4=low. URGENT orders are priority 1.", enum=(1, 2, 3, 4)),
           F("created_ts", "ts", "Order entry time (<= plan run)."),
           F("release_ts", "ts", "Earliest start on the shop floor.", rule=">= created_ts"),
           F("due_ts", "ts", "Customer due time.", rule="> release_ts"),
           F("status", "str", "Latest lifecycle state (from StatusEvent).", enum=ORDER_STATUS, derived=True),
           F("total_process_min", "int", "Derived: sum of scheduled process_min.", required=False,
             unit="min", derived=True),
           F("planned_start_ts", "ts", "Derived: min(start_ts) of its schedule rows.", required=False, derived=True),
           F("planned_finish_ts", "ts", "Derived: max(end_ts) of its schedule rows.", required=False, derived=True),
           F("lateness_min", "int", "Derived: max(0, planned_finish_ts - due_ts).", required=False,
             unit="min", derived=True)),
          fks=((("sku",), "product", ("sku",), "Product 1:N ProductionOrder"),
               (("routing_id",), "routing", ("routing_id",), "Routing 1:N ProductionOrder"))),
    Table("operation", "Operation", "Order-level instance of a routing step.",
          ("op_id",),
          (F("op_id", "str", "OP-<order-id>-<seq>.", pattern=r"^OP-PROD-2026-\d{6}-\d{2}$", unique=True),
           F("order_id", "str", "Parent order."),
           F("step_id", "str", "Routing step it instantiates."),
           F("op_seq", "int", "1-based sequence in the order; op n+1 may not start before op n ends.", min=1),
           F("machine_group", "str", "Copied from step.", enum=GROUPS),
           F("operators_required", "int", "Copied from step.", enum=(1, 2)),
           F("tool_id", "str", "Copied from step.", required=False),
           F("qty", "int", "Order quantity.", unit="EA", min=1),
           F("std_process_min", "int", "ceil(qty * cycle_min_per_unit) + fixed_min.", unit="min", min=1,
             derived=True),
           F("status", "str", "Latest lifecycle state (from StatusEvent).", enum=OP_STATUS, derived=True)),
          fks=((("order_id",), "production_order", ("order_id",), "ProductionOrder 1:N Operation"),
               (("step_id",), "routing_step", ("step_id",), "RoutingStep 1:N Operation"),
               (("tool_id",), "tooling", ("tool_id",), "Tooling 1:N Operation (optional)")),
          uniques=(("order_id", "op_seq"),)),
    Table("operation_schedule", "OperationSchedule", "Planned machine slot of an operation. "
          "Occupancy = [setup_start_ts, end_ts); process runs [start_ts, end_ts).",
          ("schedule_id",),
          (F("schedule_id", "str", "Schedule id.", pattern=r"^SCH-2026-\d{6}$", unique=True),
           F("op_id", "str", "Scheduled operation.", unique=True),
           F("machine_id", "str", "Assigned machine; must be eligible."),
           F("setup_min", "int", "Changeover before the process; must be >= ChangeoverMatrix value.",
             unit="min", min=0),
           F("setup_start_ts", "ts", "Derived: start_ts - setup_min.", derived=True),
           F("start_ts", "ts", "Process start (material consumed here)."),
           F("end_ts", "ts", "Process end.", rule="> start_ts"),
           F("process_min", "int", "Derived: ceil(std_process_min / speed_factor).", unit="min", derived=True),
           F("status", "str", "Schedule status.", enum=SCHED_STATUS)),
          fks=((("op_id",), "operation", ("op_id",), "Operation 1:0..1 OperationSchedule"),
               (("machine_id",), "machine", ("machine_id",), "Machine 1:N OperationSchedule"))),
    Table("material_consumption", "Material consumption", "Planned issue of lot stock to an operation.",
          ("consumption_id",),
          (F("consumption_id", "str", "Row id.", pattern=r"^CON-\d{6}$", unique=True),
           F("op_id", "str", "Consuming operation."),
           F("material_id", "str", "Material."),
           F("lot_id", "str", "Lot drawn from; lot.material_id must match."),
           F("qty", "int", "Quantity drawn.", unit="material uom", min=1),
           F("consumed_ts", "ts", "Derived: equals the operation's start_ts.", derived=True)),
          fks=((("op_id",), "operation", ("op_id",), "Operation 1:N MaterialConsumption"),
               (("material_id",), "material", ("material_id",), "Material 1:N MaterialConsumption"),
               (("lot_id",), "inventory_lot", ("lot_id",), "InventoryLot 1:N MaterialConsumption")), core=False),
    Table("status_event", "Status event (history)", "Append-only lifecycle history for orders, operations and lots.",
          ("event_id",),
          (F("event_id", "str", "Event id.", pattern=r"^EVT-\d{6}$", unique=True),
           F("entity_type", "str", "ORDER, OPERATION or LOT.", enum=ENTITY_TYPES),
           F("entity_id", "str", "Id of the entity (polymorphic reference, validated per entity_type)."),
           F("from_state", "str", "Previous state; NULL for the initial event.", required=False),
           F("to_state", "str", "New state."),
           F("event_ts", "ts", "When the transition happened/is planned."),
           F("reason_code", "str", "Why.", required=False)), core=False),
    Table("schedule_exception", "Schedule exception", "Explanation for every order that is infeasible or late.",
          ("exception_id",),
          (F("exception_id", "str", "Exception id.", pattern=r"^EXC-\d{6}$", unique=True),
           F("order_id", "str", "Affected order."),
           F("op_id", "str", "Affected operation (optional).", required=False),
           F("exception_type", "str", "Type.", enum=EXCEPTION_TYPES),
           F("root_cause", "str", "Computed root cause.", enum=ROOT_CAUSES),
           F("lateness_min", "int", "Lateness for LATE_ORDER.", required=False, unit="min"),
           F("detail", "str", "Human readable explanation.")),
          fks=((("order_id",), "production_order", ("order_id",), "ProductionOrder 1:N ScheduleException"),
               (("op_id",), "operation", ("op_id",), "Operation 1:N ScheduleException (optional)")), core=False),
]

GROUND_TRUTH = Table("ground_truth", "Ground truth (separate file)", "Labels for injected anomalies. "
    "Never read by the generator input path or the validator.",
    ("anomaly_id",),
    (F("anomaly_id", "str", "GT-0001...", pattern=r"^GT-\d{4}$", unique=True),
     F("issue_type", "str", "Injected anomaly class.", enum=ISSUE_TYPES),
     F("rule_id", "str", "Validator rule expected to fire."),
     F("entity_type", "str", "Table of the primary affected row.",
       enum=("OPERATION_SCHEDULE", "MATERIAL_CONSUMPTION")),
     F("entity_id", "str", "Primary key of the affected row."),
     F("op_id", "str", "Affected operation."),
     F("related_entity_id", "str", "Other row involved (e.g. overlapped schedule, predecessor, lot).",
       required=False),
     F("description", "str", "What was changed and why it is a violation."),
     F("original_value", "str", "JSON of the replaced values."),
     F("injected_value", "str", "JSON of the new values.")),
    core=False)

BY_NAME = {t.name: t for t in TABLES}
TABLE_ORDER = [t.name for t in TABLES]
