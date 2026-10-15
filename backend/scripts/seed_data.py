"""Static seed values, taken from PROJECT_CONTEXT sections 5 and 12."""

# role id -> code (ids and names come from data/Ref_Data/UserRoles.csv)
ROLE_CODES = {101: "TRADER", 102: "OPS_USER", 103: "OPS_MANAGER", 104: "ML_ADMIN", 105: "SYS_ADMIN"}
ROLE_FALLBACK = {  # used only if UserRoles.csv is missing
    101: ("Trader", "Submit and track trade bookings", "Trade Booking Form, Trade Details"),
    102: ("Operations User", "Handle settlement exceptions", "Exception Dashboard, Repair Queue"),
    103: ("Operations Manager", "Oversee operations team performance", "Reports, Team Management, Approvals"),
    104: ("ML Model Administrator", "Manage AI/ML model lifecycle", "Model Monitoring, Retraining, Configuration"),
    105: ("System Administrator", "Full system administration", "All system functions, User Management"),
}

T, OU, OM, ML, SA = 101, 102, 103, 104, 105
_OPS = {OU: "ALL", OM: "ALL", SA: "ALL"}
_ML = {ML: "ALL", SA: "ALL"}
_SA = {SA: "ALL"}

# (code, description, {role_id: scope})  - section 5.2, including its documented deviations (* and **)
PERMISSIONS = [
    ("TRADE_SUBMIT_SINGLE", "Submit Individual Trades", {T: "ALL", SA: "ALL"}),
    ("TRADE_SUBMIT_BULK", "Submit Bulk Trades", {T: "ALL", SA: "ALL"}),
    ("TRADE_VIEW", "View Trade Details", {T: "OWN", OU: "ALL", OM: "ALL", SA: "ALL"}),
    ("RISK_SCORE_VIEW", "View AI Risk Score", {T: "ALL", OU: "ALL", OM: "ALL", SA: "ALL"}),
    ("EXCEPTION_DASHBOARD_VIEW", "View Exception Dashboard", _OPS),
    ("REPAIR_QUEUE_MANAGE", "Repair Queue Management", {OU: "ASSIGNED", OM: "ALL", SA: "ALL"}),
    ("FIX_APPLY", "Apply/Modify Fixes", _OPS),
    ("QUEUE_BULK_OPS", "Bulk Queue Operations", _OPS),
    ("QUEUE_ASSIGN", "Assign Trades to Users", {OU: "OWN", OM: "ALL", SA: "ALL"}),
    ("QUEUE_REASSIGN", "Reassign Trades Between Users", {OM: "ALL", SA: "ALL"}),
    ("HIGH_RESOLUTION_APPROVE", "Approve High-Priority Resolutions", {OM: "ALL", SA: "ALL"}),
    ("TEAM_METRICS_VIEW", "View Team Performance Metrics", {OM: "ALL", SA: "ALL"}),
    ("COMPLIANCE_REPORT", "Generate Compliance Reports", {OM: "ALL", SA: "ALL"}),
    ("MODEL_METRICS_VIEW", "View Model Performance Metrics", _ML),
    ("DRIFT_MONITOR", "Monitor Data Drift (PSI)", _ML),
    ("FEATURE_IMPORTANCE_VIEW", "View Feature Importance", _ML),
    ("MODEL_RETRAIN", "Trigger Model Retraining", _ML),
    ("MODEL_PROMOTE", "Promote Model Versions", _ML),
    ("HYBRID_WEIGHTS_CONFIG", "Configure Alpha/Beta Weights", _ML),
    ("BUSINESS_RULES_CONFIG", "Configure Business Rules", _SA),
    ("SYSTEM_CONFIG", "Configure System Parameters", _SA),
    ("USER_MANAGE", "Manage User Accounts", _SA),
    ("ROLE_ASSIGN", "Role Assignment", _SA),
    ("API_KEYS_MANAGE", "Manage API Keys", _SA),
    ("MONITORING_ALERTS_CONFIG", "Configure Monitoring Alerts", _SA),
    ("AUDIT_VIEW", "View Complete Audit Trail", {OU: "ASSIGNED", OM: "ALL", SA: "ALL"}),
    ("TRADE_AMEND", "Update existing trade / Apply Fix target", {T: "OWN", OU: "ALL", OM: "ALL", SA: "ALL"}),
    ("CHATBOT_USE", "Use chatbot", {T: "ALL", OU: "ALL", OM: "ALL", ML: "ALL", SA: "ALL"}),
]

# Section 5.4 - one trader per "Trader Name" in the data. Username = firstname.lastname
TRADER_NAMES = [
    "Aisha Khan", "Carlos Rodriguez", "David Wilson", "Emma Davis", "Fatima Hassan", "James Anderson",
    "John Miller", "Laura Martinez", "Michael Brown", "Nina Petrova", "Priya Sharma", "Robert Taylor",
    "Sarah Chen", "Tom Richardson", "Wei Zhang",
]
# (username, full name, role id)
STAFF_USERS = [
    ("ops.user1", "Ops User One", OU),
    ("ops.user2", "Ops User Two", OU),
    ("ops.user3", "Ops User Three", OU),
    ("ops.manager", "Ops Manager", OM),
    ("ml.admin", "ML Admin", ML),
    ("sys.admin", "System Admin", SA),
]

# Section 12: key -> (default value, description, editable_by)
SYSTEM_CONFIG = {
    "hybrid_weights": ({"alpha": 0.3, "beta": 0.7}, "FinalScore = alpha*ML + beta*Rule (must sum to 1)", "ML_ADMIN"),
    "classification_thresholds": ({"pass": 0.5, "exception": 0.7}, "< pass = PASS, >= exception = EXCEPTION, between = MONITOR", "ML_ADMIN"),
    "rule_weights": (
        {"ssi": 0.35, "ccy": 0.15, "sd_past": 0.15, "sd_weekend": 0.10, "sd_far": 0.05, "sd_cap": 0.15,
         "cp_tier": {"1": 0, "2": 0.10, "3": 0.20, "4": 0.25}, "qty_high": 0.10, "qty_low": 0.05, "price_warn": 0.0},
        "Rule engine weights", "SYS_ADMIN"),
    "counterparty_tier_thresholds": ({"tier4": 1500, "tier3": 1000, "tier2": 500}, "Historical fail-count boundaries for risk tiers", "SYS_ADMIN"),
    "quantity_limits": ({"min": 100, "max": 5000}, "Quantity outside this range adds rule score", "SYS_ADMIN"),
    "settlement_far_days": (15, "Settlement more than this many days out is flagged", "SYS_ADMIN"),
    "price_zscore_warn": (3, "Warn when price is this many std devs from the symbol mean", "SYS_ADMIN"),
    "business_date_override": (None, "YYYY-MM-DD to pretend 'today' is another date (demo); null = real today (IST)", "SYS_ADMIN"),
    "batch_size": (10000, "Rows per chunk in bulk upload", "SYS_ADMIN"),
    "max_upload_rows": (100000, "Maximum rows in one uploaded file", "SYS_ADMIN"),
    "psi_threshold": (0.2, "PSI above this is RED drift (amber from 0.1)", "ML_ADMIN"),
    "drift_window_days": (30, "Days of recent predictions used for drift checks", "ML_ADMIN"),
    "rejection_report_recipients": (["trader-group@tradeperfect.local"], "Who receives rejection reports", "SYS_ADMIN"),
    "audit_report_recipients": (["compliance@tradeperfect.local"], "Who receives audit reports", "SYS_ADMIN"),
}

# Sample holidays. Deliberately OUTSIDE the date ranges of both provided datasets,
# so they do not change any score. A Sys Admin adds real ones later (endpoint 50, BE-6).
HOLIDAYS = [
    ("2026-12-25", "GLOBAL", "Christmas Day"),
    ("2027-01-01", "GLOBAL", "New Year's Day"),
    ("2027-01-26", "GLOBAL", "Republic Day (India)"),
]
