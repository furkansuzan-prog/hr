"""Permission catalog and the 15 starting role templates (Master Prompt section 10).
Roles are data (configurable per tenant); this module only seeds the defaults."""

PERMISSIONS: dict[str, str] = {
    "org.read": "View company / project / contractor hierarchy",
    "org.write": "Manage company / project / contractor hierarchy",
    "people.read": "View workforce records (names masked without people.pii)",
    "people.pii": "View personal identifiers (names, employee numbers)",
    "people.write": "Create / update workforce records",
    "timekeeping.read": "View timesheet and absence data",
    "cost.read": "View payroll and labour cost data (field-level protected)",
    "health.read": "View health-derived indicators (sickness, unplanned absence, protected leave; D-12)",
    "hse.read": "View aggregated HSE indicators",
    "training.read": "View training and competency data",
    "kpi.read": "View KPI results and catalog",
    "kpi.calculate": "Trigger KPI calculation runs",
    "kpi.manage": "Create KPI formula versions, activate KPIs",
    "period.close": "Close / reopen reporting periods",
    "forecast.read": "View forecasts and scenarios",
    "assistant.use": "Use the HR Intelligence Assistant",
    "report.read": "View reports",
    "report.export": "Download / export reports",
    "import.write": "Upload and commit data imports",
    "risk.read": "View risk register and actions",
    "risk.write": "Manage risks and improvement actions",
    "sustainability.read": "View sustainability dashboards and framework coverage",
    "sustainability.write": "Manage framework evidence",
    "audit.read": "View audit logs",
    "admin.users": "Manage users and role assignments",
    "admin.platform": "Platform administration",
}

_ALL = set(PERMISSIONS)
_ANALYTICS = {"org.read", "kpi.read", "forecast.read", "assistant.use", "report.read", "risk.read"}

ROLE_TEMPLATES: dict[str, tuple[str, set[str]]] = {
    "SYSTEM_ADMIN": ("System Administrator", _ALL),
    "PLATFORM_ADMIN": ("Platform Administrator", {"admin.platform", "admin.users", "audit.read", "org.read", "org.write"}),
    "COMPANY_ADMIN": ("Company Administrator", {"admin.users", "org.read", "org.write", "people.read", "import.write"}),
    "HR_DIRECTOR": (
        "HR Director",
        _ANALYTICS
        | {"people.read", "people.pii", "timekeeping.read", "cost.read", "health.read", "training.read", "hse.read",
           "kpi.calculate", "period.close", "report.export", "risk.write", "sustainability.read"},
    ),
    "HR_MANAGER": (
        "HR Manager",
        _ANALYTICS
        | {"people.read", "people.pii", "people.write", "timekeeping.read", "training.read", "kpi.calculate",
           "report.export", "import.write", "risk.write"},
    ),
    "HR_SPECIALIST": ("HR Specialist", {"org.read", "people.read", "people.pii", "people.write", "timekeeping.read",
                                         "training.read", "kpi.read", "import.write"}),
    "SUSTAINABILITY_MANAGER": (
        "Sustainability Manager",
        _ANALYTICS | {"hse.read", "training.read", "sustainability.read", "sustainability.write", "report.export"},
    ),
    "OPERATIONS_DIRECTOR": ("Operations Director", _ANALYTICS | {"timekeeping.read", "hse.read", "report.export"}),
    "PROJECT_MANAGER": ("Project Manager", _ANALYTICS | {"timekeeping.read", "hse.read"}),
    "DEPARTMENT_MANAGER": ("Department Manager", _ANALYTICS | {"timekeeping.read"}),
    "CONTRACTOR_MANAGER": (
        "Contractor Manager",
        {"org.read", "people.read", "people.pii", "timekeeping.read", "training.read", "hse.read", "kpi.read",
         "report.read", "report.export", "import.write", "assistant.use"},
    ),
    "FINANCE_ANALYST": ("Finance Analyst", _ANALYTICS | {"cost.read", "report.export"}),
    "HSE_SPECIALIST": ("HSE Specialist", {"org.read", "hse.read", "health.read", "training.read", "kpi.read",
                                           "report.read", "risk.read", "risk.write"}),
    "INTERNAL_AUDITOR": ("Internal Auditor", _ANALYTICS | {"audit.read", "people.read", "sustainability.read"}),
    "READONLY_EXECUTIVE": ("Read-Only Executive", {"org.read", "kpi.read", "forecast.read", "report.read",
                                                   "sustainability.read", "assistant.use"}),
}

# D-12 / ADR-025: health-derived KPIs need kpi.read AND health.read (scope intersection, like sensitive + cost.read).
# health.read is granted by default to SYSTEM_ADMIN (all permissions), HR_DIRECTOR and HSE_SPECIALIST only; project /
# department managers, operations, executives, finance, auditors and contractor managers do not get it (PO-5).
HEALTH_DERIVED_KPIS: frozenset[str] = frozenset({"KPI-028", "KPI-053", "KPI-054"})
# Leave types whose per-type hours are health data (KVKK md.6). In KPIs that stay visible without health.read (KPI-003
# absenteeism) these per-type figures are hidden from users without health.read on the result's scope.
HEALTH_LEAVE_TYPES: tuple[str, ...] = ("sick", "work_injury", "parental")
HEALTH_ROLES: frozenset[str] = frozenset(r for r, (_, perms) in ROLE_TEMPLATES.items() if "health.read" in perms)
