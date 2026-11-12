import re, main
boards = [("Zip","ashby","zip"),("Teleskope","ashby","teleskope"),("Databricks","greenhouse","databricks"),("Chainguard","greenhouse","chainguard"),
          ("Vanta","ashby","vanta"),("Teleport","greenhouse","teleport"),("Cribl","greenhouse","cribl"),("Monte Carlo","ashby","montecarlodata"),
          ("SpecterOps","greenhouse","specterops"),("CertifyOS","ashby","certifyos")]
toks = main._role_tokens(["solutions engineer","sales engineer","forward deployed engineer","solutions architect","pre-sales engineer"])
for name, ats, slug in boards:
    try:
        jobs = main.FETCHERS[ats](name, slug)
    except Exception as e:
        print("ERR", name, e); continue
    for j in jobs:
        if not main.matches_role(j["title"], toks) or main.is_too_senior(j["title"], main.DEFAULT_TOO_SENIOR):
            continue
        d = j["description"]
        snips = [d[max(0,m.start()-60):m.end()+60].replace("\n"," ") for m in re.finditer(r"years?", d, re.I)][:4]
        print("JOB", name, "|", j["title"], "| desc", len(d), "| parsed", main.years_required(d), "| comp", j["compensation"])
        for s in snips: print("   ~", s)
