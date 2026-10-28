# ATS slug mappings for companies in config.yml
# Format: "Company Name": {"ats": "<platform>", "slug": "<board_slug>", "domain": "<site>"}
# domain is used to look up people to message (Hunter/Apollo).
# A wrong slug is auto-detected and re-probed; see [heal] lines in the run log.
# ATS options: greenhouse, lever, ashby, smartrecruiters, workable, rippling
#
# To find a slug: visit the company careers page, look at the URL.
#   Greenhouse:  boards.greenhouse.io/<slug>
#   Lever:       jobs.lever.co/<slug>
#   Ashby:       jobs.ashbyhq.com/<slug>
#   Workable:    apply.workable.com/<slug>
#   Rippling:    ats.rippling.com/<slug>
#   SmartRecruiters: jobs.smartrecruiters.com/<CompanyId>

COMPANY_SLUGS = {
    # --- AI Agents & Workflow Orchestration ---
    "Letta":            {"ats": "ashby",     "slug": "letta", "domain": "letta.com"},
    "LangChain":        {"ats": "ashby",     "slug": "langchain", "domain": "langchain.com"},
    "Vellum AI":        {"ats": "ashby",     "slug": "vellum", "domain": "vellum.ai"},
    "Beam AI":          {"ats": "ashby",     "slug": "beam-ai", "domain": "beam.ai"},
    "Lindy AI":         {"ats": "ashby",     "slug": "lindy", "domain": "lindy.ai"},
    "Relevance AI":     {"ats": "lever",     "slug": "relevanceai", "domain": "relevanceai.com"},
    "Kore.ai":          {"ats": "greenhouse","slug": "kore-ai", "domain": "kore.ai"},

    # --- Data Infrastructure & Modern Data Stack ---
    "Hex":              {"ats": "greenhouse","slug": "hex", "domain": "hex.tech"},
    "Monte Carlo":      {"ats": "greenhouse","slug": "monte-carlo-data", "domain": "montecarlodata.com"},
    "Anomalo":          {"ats": "greenhouse","slug": "anomalo", "domain": "anomalo.com"},
    "Fivetran":         {"ats": "greenhouse","slug": "fivetran", "domain": "fivetran.com"},
    "Airbyte":          {"ats": "greenhouse","slug": "airbyte", "domain": "airbyte.com"},
    "Hightouch":        {"ats": "ashby",     "slug": "hightouch", "domain": "hightouch.com"},
    "Census":           {"ats": "lever",     "slug": "census", "domain": "getcensus.com"},
    "Coalesce":         {"ats": "greenhouse","slug": "coalesce", "domain": "coalesce.io"},
    "Arize AI":         {"ats": "greenhouse","slug": "arize-ai", "domain": "arize.com"},
    "Metaplane":        {"ats": "lever",     "slug": "metaplane", "domain": "metaplane.dev"},
    "Meltano":          {"ats": "greenhouse","slug": "meltano", "domain": "meltano.com"},

    # --- AI Security & Governance ---
    "Aikido Security":  {"ats": "ashby",     "slug": "aikido", "domain": "aikido.dev"},
    "Cyera":            {"ats": "greenhouse","slug": "cyera", "domain": "cyera.io"},
    "Doppel":           {"ats": "ashby",     "slug": "doppel", "domain": "doppel.com"},
    "Adaptive Security":{"ats": "ashby",     "slug": "adaptive-security", "domain": "adaptivesecurity.com"},
    "Prompt Security":  {"ats": "ashby",     "slug": "prompt-security", "domain": "prompt.security"},
    "Protect AI":       {"ats": "greenhouse","slug": "protect-ai", "domain": "protectai.com"},
    "Calypso AI":       {"ats": "ashby",     "slug": "calypso-ai", "domain": "calypsoai.com"},
    "Lasso Security":   {"ats": "ashby",     "slug": "lasso-security", "domain": "lasso.security"},

    # --- Developer Tools & API Infrastructure ---
    "Temporal":         {"ats": "greenhouse","slug": "temporal", "domain": "temporal.io"},
    "Retool":           {"ats": "greenhouse","slug": "retool", "domain": "retool.com"},
    "Merge":            {"ats": "greenhouse","slug": "merge-api", "domain": "merge.dev"},
    "Apideck":          {"ats": "lever",     "slug": "apideck", "domain": "apideck.com"},
    "Stytch":           {"ats": "ashby",     "slug": "stytch", "domain": "stytch.com"},
    "Clerk":            {"ats": "ashby",     "slug": "clerk", "domain": "clerk.com"},
    "Supabase":         {"ats": "ashby",     "slug": "supabase", "domain": "supabase.com"},
    "Inngest":          {"ats": "ashby",     "slug": "inngest", "domain": "inngest.com"},
    "Trigger.dev":      {"ats": "ashby",     "slug": "trigger-dev", "domain": "trigger.dev"},

    # --- Integration & Connectivity Platforms ---
    "Tray.io":          {"ats": "greenhouse","slug": "tray", "domain": "tray.ai"},
    "Workato":          {"ats": "greenhouse","slug": "workato", "domain": "workato.com"},
    "Paragon":          {"ats": "ashby",     "slug": "useparagon", "domain": "useparagon.com"},
    "Nango":            {"ats": "ashby",     "slug": "nango", "domain": "nango.dev"},
    "Boomi":            {"ats": "greenhouse","slug": "boomi", "domain": "boomi.com"},

    # --- Fintech & Payments Infrastructure ---
    "Coinflow":         {"ats": "ashby",     "slug": "coinflow", "domain": "coinflow.cash"},
    "Lithic":           {"ats": "greenhouse","slug": "lithic", "domain": "lithic.com"},
    "Slope":            {"ats": "ashby",     "slug": "slope", "domain": "slopepay.com"},
    "Modern Treasury":  {"ats": "greenhouse","slug": "modern-treasury", "domain": "moderntreasury.com"},

    # --- Enterprise AI & Applied ML ---
    "Glean":            {"ats": "greenhouse","slug": "glean", "domain": "glean.com"},
    "Moveworks":        {"ats": "greenhouse","slug": "moveworks", "domain": "moveworks.com"},
    "Observe AI":       {"ats": "greenhouse","slug": "observe-ai", "domain": "observe.ai"},
    "Hebbia":           {"ats": "ashby",     "slug": "hebbia", "domain": "hebbia.ai"},
    "Aisera":           {"ats": "greenhouse","slug": "aisera", "domain": "aisera.com"},
    "Cohere":           {"ats": "ashby",     "slug": "cohere", "domain": "cohere.com"},
    "Weights & Biases": {"ats": "greenhouse","slug": "weights-biases", "domain": "wandb.ai"},

    # --- Infrastructure / Cloud / DevOps ---
    "Cribl":            {"ats": "greenhouse","slug": "cribl", "domain": "cribl.io"},
    "Chronosphere":     {"ats": "greenhouse","slug": "chronosphere", "domain": "chronosphere.io"},
    "Cortex":           {"ats": "ashby",     "slug": "cortex", "domain": "cortex.io"},
    "Rootly":           {"ats": "ashby",     "slug": "rootly", "domain": "rootly.com"},
    "Port":             {"ats": "ashby",     "slug": "port", "domain": "getport.io"},

    # --- Anchor / Enterprise ---
    "Databricks":       {"ats": "greenhouse","slug": "databricks", "domain": "databricks.com"},
    "Stripe":           {"ats": "greenhouse","slug": "stripe", "domain": "stripe.com"},
    "Snowflake":        {"ats": "greenhouse","slug": "snowflake-computing", "domain": "snowflake.com"},
    "OpenAI":           {"ats": "ashby",     "slug": "openai", "domain": "openai.com"},
    "Anthropic":        {"ats": "greenhouse","slug": "anthropic", "domain": "anthropic.com"},
}
