# ATS slug mappings for companies in config.yml
# Format: "Company Name": {"ats": "<platform>", "slug": "<board_slug>"}
# ATS options: greenhouse, lever, ashby, workable, rippling
#
# To find a slug: visit the company careers page, look at the URL.
#   Greenhouse:  boards.greenhouse.io/<slug>
#   Lever:       jobs.lever.co/<slug>
#   Ashby:       jobs.ashbyhq.com/<slug>
#   Workable:    apply.workable.com/<slug>
#   Rippling:    ats.rippling.com/<slug>

COMPANY_SLUGS = {
    # --- AI Agents & Workflow Orchestration ---
    "Letta":            {"ats": "ashby",     "slug": "letta"},
    "LangChain":        {"ats": "ashby",     "slug": "langchain"},
    "Vellum AI":        {"ats": "ashby",     "slug": "vellum"},
    "Beam AI":          {"ats": "ashby",     "slug": "beam-ai"},
    "Lindy AI":         {"ats": "ashby",     "slug": "lindy"},
    "Relevance AI":     {"ats": "lever",     "slug": "relevanceai"},
    "Kore.ai":          {"ats": "greenhouse","slug": "kore-ai"},

    # --- Data Infrastructure & Modern Data Stack ---
    "Hex":              {"ats": "greenhouse","slug": "hex"},
    "Monte Carlo":      {"ats": "greenhouse","slug": "monte-carlo-data"},
    "Anomalo":          {"ats": "greenhouse","slug": "anomalo"},
    "Fivetran":         {"ats": "greenhouse","slug": "fivetran"},
    "Airbyte":          {"ats": "greenhouse","slug": "airbyte"},
    "Hightouch":        {"ats": "ashby",     "slug": "hightouch"},
    "Census":           {"ats": "lever",     "slug": "census"},
    "Coalesce":         {"ats": "greenhouse","slug": "coalesce"},
    "Arize AI":         {"ats": "greenhouse","slug": "arize-ai"},
    "Metaplane":        {"ats": "lever",     "slug": "metaplane"},
    "Meltano":          {"ats": "greenhouse","slug": "meltano"},

    # --- AI Security & Governance ---
    "Aikido Security":  {"ats": "ashby",     "slug": "aikido"},
    "Cyera":            {"ats": "greenhouse","slug": "cyera"},
    "Doppel":           {"ats": "ashby",     "slug": "doppel"},
    "Adaptive Security":{"ats": "ashby",     "slug": "adaptive-security"},
    "Prompt Security":  {"ats": "ashby",     "slug": "prompt-security"},
    "Protect AI":       {"ats": "greenhouse","slug": "protect-ai"},
    "Calypso AI":       {"ats": "ashby",     "slug": "calypso-ai"},
    "Lasso Security":   {"ats": "ashby",     "slug": "lasso-security"},

    # --- Developer Tools & API Infrastructure ---
    "Temporal":         {"ats": "greenhouse","slug": "temporal"},
    "Retool":           {"ats": "greenhouse","slug": "retool"},
    "Merge":            {"ats": "greenhouse","slug": "merge-api"},
    "Apideck":          {"ats": "lever",     "slug": "apideck"},
    "Stytch":           {"ats": "ashby",     "slug": "stytch"},
    "Clerk":            {"ats": "ashby",     "slug": "clerk"},
    "Supabase":         {"ats": "ashby",     "slug": "supabase"},
    "Inngest":          {"ats": "ashby",     "slug": "inngest"},
    "Trigger.dev":      {"ats": "ashby",     "slug": "trigger-dev"},

    # --- Integration & Connectivity Platforms ---
    "Tray.io":          {"ats": "greenhouse","slug": "tray"},
    "Workato":          {"ats": "greenhouse","slug": "workato"},
    "Paragon":          {"ats": "ashby",     "slug": "useparagon"},
    "Nango":            {"ats": "ashby",     "slug": "nango"},
    "Boomi":            {"ats": "greenhouse","slug": "boomi"},

    # --- Fintech & Payments Infrastructure ---
    "Coinflow":         {"ats": "ashby",     "slug": "coinflow"},
    "Lithic":           {"ats": "greenhouse","slug": "lithic"},
    "Slope":            {"ats": "ashby",     "slug": "slope"},
    "Modern Treasury":  {"ats": "greenhouse","slug": "modern-treasury"},

    # --- Enterprise AI & Applied ML ---
    "Glean":            {"ats": "greenhouse","slug": "glean"},
    "Moveworks":        {"ats": "greenhouse","slug": "moveworks"},
    "Observe AI":       {"ats": "greenhouse","slug": "observe-ai"},
    "Hebbia":           {"ats": "ashby",     "slug": "hebbia"},
    "Aisera":           {"ats": "greenhouse","slug": "aisera"},
    "Cohere":           {"ats": "greenhouse","slug": "cohere"},
    "Weights & Biases": {"ats": "greenhouse","slug": "weights-biases"},

    # --- Infrastructure / Cloud / DevOps ---
    "Cribl":            {"ats": "greenhouse","slug": "cribl"},
    "Chronosphere":     {"ats": "greenhouse","slug": "chronosphere"},
    "Cortex":           {"ats": "ashby",     "slug": "cortex"},
    "Rootly":           {"ats": "ashby",     "slug": "rootly"},
    "Port":             {"ats": "ashby",     "slug": "port"},

    # --- Anchor / Enterprise ---
    "Databricks":       {"ats": "greenhouse","slug": "databricks"},
    "Stripe":           {"ats": "greenhouse","slug": "stripe"},
    "Snowflake":        {"ats": "greenhouse","slug": "snowflake-computing"},
    "OpenAI":           {"ats": "greenhouse","slug": "openai"},
    "Anthropic":        {"ats": "ashby",     "slug": "anthropic"},
}
