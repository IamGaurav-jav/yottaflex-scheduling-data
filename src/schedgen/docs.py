"""Generate docs/data_dictionary.md from schema.py (single source of truth)."""
from .schema import BY_NAME, GROUND_TRUTH, TABLES

ID_CONVENTIONS = """\
| Entity | Pattern | Example | Notes |
|---|---|---|---|
| ProductionOrder | `PROD-2026-NNNNNN` | `PROD-2026-000001` | The brief's `PROD-` id is interpreted as the *production order* id (assumption A1) |
| Routing | `ROUTE-<sku>-VV` | `ROUTE-SKU-001-01` | `<sku>` is the Product key; `VV` is the version |
| Operation | `OP-<order-id>-SS` | `OP-PROD-2026-000001-01` | order-level instance of a routing step |
| Machine | `MACH-NNN` | `MACH-001` | |
| OperationSchedule | `SCH-2026-NNNNNN` | `SCH-2026-000001` | assigned in start-time order of the baseline plan, immutable afterwards |
| Other | `SKU-NNN`, `TOOL-NNN`, `MAT-NNN`, `LOT-NNNNNN`, `CAL-NNNNNN`, `DT-NNNNNN`, `CO-NNNNNN`, `CON-NNNNNN`, `EVT-NNNNNN`, `EXC-NNNNNN`, `GT-NNNN` | | counters are zero padded and never reused |
"""

ER = """\
```mermaid
erDiagram
  Product ||--o{ Routing : "has versions"
  Routing ||--|{ RoutingStep : "has steps"
  RoutingStep }o--o{ Machine : "eligible (routing_step_machine)"
  RoutingStep }o--o{ Material : "requires (routing_step_material)"
  Tooling |o--o{ RoutingStep : "needs shared tool"
  Machine ||--o{ MachineCalendar : "staffed windows"
  Shift ||--o{ MachineCalendar : ""
  Machine ||--o{ MachineDowntime : ""
  Product ||--o{ ProductionOrder : ""
  Routing ||--o{ ProductionOrder : "version valid at release"
  ProductionOrder ||--|{ Operation : ""
  RoutingStep ||--o{ Operation : "instantiated as"
  Operation ||--o| OperationSchedule : "scheduled once"
  Machine ||--o{ OperationSchedule : ""
  Operation ||--o{ MaterialConsumption : ""
  InventoryLot ||--o{ MaterialConsumption : ""
  Material ||--o{ InventoryLot : ""
  ProductionOrder ||--o{ ScheduleException : "explains"
  ChangeoverMatrix }o--|| Machine : "by machine_group + family"
```
"""


def _esc(s):
    return str(s).replace("|", "\\|")


def render():
    out = ["# Data dictionary", "",
           "_Generated from `src/schedgen/schema.py` by `python run.py docs`; do not edit by hand._", "",
           "Conventions: timestamps are plant-local ISO-8601 (`YYYY-MM-DDTHH:MM:SS`, no time zone), dates are "
           "`YYYY-MM-DD`, durations are integer minutes. Columns marked **derived** are computed from source "
           "records (`derive.finalize`) and re-checked by the validator. NULL is an empty CSV cell.", "",
           "## ID / nomenclature conventions", "", ID_CONVENTIONS, "## Entity relationships", "", ER,
           "Cardinalities are listed per table below. The two many-to-many relationships use bridge tables "
           "(`routing_step_machine`, `routing_step_material`).", ""]
    for tab in TABLES + [GROUND_TRUTH]:
        out += [f"## `{tab.name}` - {tab.entity}", "", tab.desc, ""]
        out.append(f"- **Primary key:** `{', '.join(tab.pk)}`")
        for cols, ref, rcols, card in tab.fks:
            out.append(f"- **Foreign key:** `{', '.join(cols)}` -> `{ref}({', '.join(rcols)})` - {card}")
        for u in tab.uniques:
            out.append(f"- **Unique:** `({', '.join(u)})`")
        if tab.name == "ground_truth":
            out.append("- **Location:** `ground_truth.csv/json` next to (not inside) `csv/`; never part of the input tables.")
        out += ["", "| Field | Type | Required | Allowed values / pattern / range | Unit | Unique | Validation rule | Description |",
                "|---|---|---|---|---|---|---|---|"]
        for f in tab.fields:
            allowed = []
            if f.enum:
                allowed.append(", ".join(f"`{x}`" for x in f.enum))
            if f.pattern:
                allowed.append(f"`{f.pattern}`")
            if f.min is not None or f.max is not None:
                allowed.append(f"{'' if f.min is None else f.min} .. {'' if f.max is None else f.max}")
            req = "yes" if f.required else "no"
            desc = ("**derived** " if f.derived else "") + f.desc
            out.append(f"| `{f.name}` | {f.type} | {req} | {_esc('; '.join(allowed))} | {f.unit} | "
                       f"{'yes' if f.unique or (f.name,) == tab.pk else ''} | {_esc(f.rule)} | {_esc(desc)} |")
        out.append("")
    return "\n".join(out)


def write_data_dictionary(path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render())
