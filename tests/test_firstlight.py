import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contacts
import discover
import main


def iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def empty_cache():
    return {"boards": {}, "seeded_boards": [], "discovered": {}, "contacts": {}}


# ─────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────
YC_ROWS = [
    {"name": "Tiny", "team_size": 5, "status": "Active", "regions": ["United States"], "industries": []},
    {"name": "Growthy", "slug": "growthy", "website": "https://www.growthy.ai", "team_size": 60,
     "status": "Active", "regions": ["United States", "Remote"], "industries": ["B2B"],
     "one_liner": "Agents for ops", "batch": "Winter 2024"},
    {"name": "Overseas", "team_size": 80, "status": "Active", "regions": ["Europe"], "industries": []},
    {"name": "BigCo", "team_size": 5000, "status": "Active", "regions": ["United States"], "industries": []},
    {"name": "Listed", "team_size": 300, "status": "Public", "regions": ["United States"], "industries": []},
    {"name": "Clinic", "team_size": 40, "status": "Active", "regions": ["Remote"], "industries": ["Healthcare"]},
]


def test_filter_yc_keeps_active_mid_size_us_companies():
    cfg = {"min_team_size": 15, "max_team_size": 1000,
           "regions": ["United States", "Remote"], "exclude_industries": ["Healthcare"]}
    out = discover.filter_yc(YC_ROWS, cfg)
    assert [c["name"] for c in out] == ["Growthy"]
    assert out[0]["domain"] == "growthy.ai"
    assert out[0]["source"] == "YC Winter 2024"


def test_candidate_slugs_order_and_dedupe():
    assert discover.candidate_slugs("Trigger.dev", "trigger.dev", "trigger-dev") == \
        ["trigger-dev", "trigger", "triggerdev", "Triggerdev"]
    assert discover.candidate_slugs("Hex", "hex.tech")[0] == "hex"


def test_probe_board_tries_slugs_then_ats(monkeypatch):
    calls = []

    def fake(ats, slug):
        calls.append((ats, slug))
        return [] if (ats, slug) == ("greenhouse", "growthy") else None

    monkeypatch.setattr(discover, "_board_jobs", fake)
    assert discover.probe_board("Growthy", "growthy.ai") == {"ats": "greenhouse", "slug": "growthy"}
    assert calls[:2] == [("ashby", "growthy"), ("greenhouse", "growthy")]


def test_smartrecruiters_empty_company_is_not_a_board(monkeypatch):
    class R:
        status_code = 200
        def __init__(self, data): self.data = data
        def json(self): return self.data
    monkeypatch.setattr(discover.requests, "get", lambda url, **kw: R({"totalFound": 0, "content": []}))
    assert discover._board_jobs("smartrecruiters", "nobody") is None
    monkeypatch.setattr(discover.requests, "get", lambda url, **kw: R({"totalFound": 1, "content": [{"id": "1"}]}))
    assert discover._board_jobs("smartrecruiters", "Acme") == [{"id": "1"}]


def test_fetch_smartrecruiters_pages_and_maps_fields(monkeypatch):
    def page(offset):
        n = 100 if offset == 0 else 20
        return {"totalFound": 120, "content": [
            {"id": f"{offset + i}", "name": "Solutions Engineer", "releasedDate": "2026-10-28T10:00:00.000Z",
             "location": {"city": "Chicago", "region": "IL", "country": "us", "remote": i == 0}}
            for i in range(n)]}
    urls = []
    monkeypatch.setattr(main, "_get", lambda url: urls.append(url) or page(int(url.split("offset=")[1])))
    jobs = main.fetch_smartrecruiters("Acme", "Acme")
    assert len(jobs) == 120 and len(urls) == 2
    assert jobs[0]["url"] == "https://jobs.smartrecruiters.com/Acme/0"
    assert jobs[0]["location"] == "Chicago, IL, US (Remote)" and jobs[0]["is_remote"]
    assert main.matches_location(jobs[1])  # Chicago, on-site


def test_discover_caches_probe_results(monkeypatch):
    monkeypatch.setattr(discover, "from_yc", lambda cfg: discover.filter_yc(YC_ROWS, {"regions": ["United States"]}))
    probes = []
    monkeypatch.setattr(discover, "probe_board",
                        lambda n, d, h: probes.append(n) or ({"ats": "ashby", "slug": h} if n == "Growthy" else None))
    cache = empty_cache()
    out = discover.discover({"yc": {"enabled": True}}, cache, now=1000)
    assert [(c["name"], c["ats"], c["slug"]) for c in out] == [("Growthy", "ashby", "growthy")]
    n = len(probes)
    discover.discover({"yc": {"enabled": True}}, cache, now=2000)
    assert len(probes) == n  # nothing re-probed


def test_save_cache_keeps_contacts_out_of_git_file(tmp_path, monkeypatch):
    monkeypatch.setattr(discover, "CACHE_PATH", str(tmp_path / "board.json"))
    monkeypatch.setattr(discover, "CONTACTS_PATH", str(tmp_path / "contacts.json"))
    cache = empty_cache()
    cache["contacts"]["x.com"] = {"fetched": 1, "people": [{"name": "A B"}]}
    discover.save_cache(cache)
    assert "A B" not in (tmp_path / "board.json").read_text()
    assert discover.load_cache()["contacts"]["x.com"]["people"][0]["name"] == "A B"


# ─────────────────────────────────────────────
# Scanning
# ─────────────────────────────────────────────
def job(id_, title, hours_ago, location="Remote - US"):
    return {"id": id_, "company": "Growthy", "title": title, "url": f"https://x/{id_}",
            "location": location, "workplace": "", "is_remote": True,
            "compensation": None, "posted": iso(hours_ago), "description": ""}


def test_first_scan_sends_older_matches_as_backlog_not_silently(monkeypatch):
    board = {"jobs": [
        job("a:1", "Forward Deployed Engineer", 24 * 10),  # old: goes in the digest
        job("a:2", "Solutions Engineer", 3),               # fresh: alert
        job("a:3", "Account Executive", 1),                # wrong role
        job("a:4", "AI Engineer", 2, location="London, UK"),  # wrong place
    ]}
    monkeypatch.setitem(main.FETCHERS, "fake", lambda company, slug: list(board["jobs"]))
    targets = [{"name": "Growthy", "ats": "fake", "slug": "growthy"}]
    tokens = main._role_tokens(["forward deployed engineer", "solutions engineer", "ai engineer"])
    seen, cache = set(), empty_cache()

    first = main.scan(targets, tokens, seen, cache, {})
    assert {j["id"]: j["backlog"] for j in first} == {"a:1": True, "a:2": False}
    assert seen == {"a:1", "a:2"}
    digest = main.backlog_lines([j for j in first if j["backlog"]])
    assert "Forward Deployed Engineer" in digest and "https://x/a:1" in digest

    board["jobs"].append(job("a:5", "Applied AI Engineer", 0.2))
    second = main.scan(targets, tokens, seen, cache, {})
    assert [j["id"] for j in second] == ["a:5"] and not second[0]["backlog"]
    assert second[0]["target"]["name"] == "Growthy"


def test_role_matching_broadened_and_excludes_managers():
    tokens = main._role_tokens([r.lower() for r in ["AI Engineer", "Automation Engineer", "Customer Engineer"]])
    assert main.matches_role("Software Engineer, AI", tokens)
    assert main.matches_role("Applied AI Engineer", tokens)
    assert main.matches_role("Senior Test Infrastructure Engineer", tokens)
    assert not main.matches_role("Engineering Manager, Applied AI", tokens)
    assert not main.matches_role("Backend Engineer", tokens)


def test_structured_comp_parsing():
    ashby = {"compensation": {"summaryComponents": [
        {"compensationType": "Salary", "interval": "1 YEAR", "minValue": 150000, "maxValue": 190000},
        {"compensationType": "EquityPercentage", "minValue": 0.1, "maxValue": 0.3}]}}
    assert main.ashby_comp(ashby) == 190000
    assert main.ashby_comp({"compensation": {"compensationTierSummary": "$120K – $160K • Offers Equity"}}) == 160000
    lever = {"salaryRange": {"min": 100000, "max": 140000, "currency": "USD", "interval": "per-year-salary"}}
    assert main.lever_comp(lever) == 140000


def test_manual_target_heals_dead_board(monkeypatch):
    monkeypatch.setattr(main, "COMPANY_SLUGS", {"Acme": {"ats": "greenhouse", "slug": "wrong", "domain": "acme.io"}})
    monkeypatch.setattr(main, "board_alive", lambda ats, slug: False)
    monkeypatch.setattr(main, "probe_board", lambda n, d, h: {"ats": "ashby", "slug": "acme"})
    cache = empty_cache()
    t = main.manual_targets({"Companies": ["Acme  # note"]}, cache)
    assert (t[0]["ats"], t[0]["slug"], t[0]["domain"]) == ("ashby", "acme", "acme.io")
    # Cached: no re-probe on the next run
    monkeypatch.setattr(main, "probe_board", lambda *a: (_ for _ in ()).throw(AssertionError("re-probed")))
    assert main.manual_targets({"Companies": ["Acme"]}, cache)[0]["slug"] == "acme"


# ─────────────────────────────────────────────
# Contacts
# ─────────────────────────────────────────────
PEOPLE = [
    {"name": "Rita Recruiter", "title": "Technical Recruiter", "email": "rita@g.ai", "seniority": "senior"},
    {"name": "Sam Solutions", "title": "Head of Solutions Engineering", "email": "sam@g.ai", "seniority": "head"},
    {"name": "Fay Founder", "title": "Co-founder & CEO", "email": None, "seniority": "founder"},
    {"name": "Ed Eng", "title": "VP Engineering", "email": "ed@g.ai", "seniority": "vp"},
]


def test_fallback_ranks_solutions_then_eng_leaders_over_recruiters():
    picks = contacts.fallback_picks(PEOPLE, {"title": "Solutions Engineer"})
    assert [p["name"] for p in picks] == ["Sam Solutions", "Ed Eng", "Fay Founder"]
    assert "hiring manager" in picks[0]["why"]


def test_fallback_prefers_engineers_for_engineering_jobs():
    people = PEOPLE + [{"name": "Ana Applied", "title": "Applied AI Engineer", "email": "ana@g.ai"}]
    picks = contacts.fallback_picks(people, {"title": "AI Engineer"})
    assert [p["name"] for p in picks][:2] == ["Ed Eng", "Ana Applied"]


def test_no_api_key_uses_free_ranking(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # unset GitHub secrets arrive as empty strings
    picks = contacts.rank_contacts(job("a:1", "Solutions Engineer", 1), {}, PEOPLE, {})
    assert picks[0]["name"] == "Sam Solutions"


def test_clean_picks_drops_invented_emails():
    picks = [{"name": "Sam Solutions", "title": "Head", "email": "sam@g.ai", "linkedin": None, "why": "w", "angle": "a"},
             {"name": "Fay Founder", "title": "CEO", "email": "fay@g.ai", "linkedin": None, "why": "w", "angle": "a"}]
    out = contacts.clean_picks(picks, PEOPLE)
    assert out[0]["email"] == "sam@g.ai"
    assert out[1]["email"] is None


def test_merge_people_dedupes_and_fills_gaps():
    out = contacts.merge_people([
        {"name": "Ed Eng", "title": "VP Engineering", "email": None, "linkedin": "https://li/ed"},
        {"name": "ed eng", "title": "", "email": "ed@g.ai", "linkedin": None},
    ])
    assert out == [{"name": "Ed Eng", "title": "VP Engineering", "email": "ed@g.ai", "linkedin": "https://li/ed"}]


def test_extract_json_from_fenced_block():
    text = 'Research notes...\n```json\n{"picks": [{"name": "A"}]}\n```'
    assert contacts._extract_json(text) == {"picks": [{"name": "A"}]}
    assert contacts._extract_json("no json here") is None


def test_rank_contacts_resumes_paused_turn(monkeypatch):
    import anthropic

    replies = [
        SimpleNamespace(stop_reason="pause_turn", content=[]),
        SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=(
            '```json\n{"picks": [{"name": "Sam Solutions", "title": "Head of SE", "email": "sam@g.ai", '
            '"linkedin": null, "why": "Runs the team.", "angle": "Saw your talk."}]}\n```'))]),
    ]
    calls = []

    class FakeMessages:
        def create(self, **kw):
            calls.append(kw)
            return replies.pop(0)

    class FakeClient:
        def __init__(self):
            self.beta = SimpleNamespace(messages=FakeMessages())

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    j = job("a:2", "Solutions Engineer", 1)
    picks = contacts.rank_contacts(j, {"domain": "g.ai"}, PEOPLE, {"profile": "me"})
    assert picks[0]["name"] == "Sam Solutions" and picks[0]["email"] == "sam@g.ai"
    assert len(calls) == 2
    assert calls[1]["messages"][1]["role"] == "assistant"
    assert calls[0]["tools"][0]["type"] == "web_search_20260209"


def test_format_contacts_fits_discord_field():
    long = [{"name": "N" * 50, "title": "T" * 100, "email": "x@y.z", "linkedin": "https://l",
             "why": "w" * 400, "angle": "a" * 400}] * 3
    text = main.format_contacts(long)
    assert len(text) <= 1024 and text.startswith("**1.")


def test_non_us_locations_rejected_even_when_marked_remote():
    def loc(l, remote=True):
        return {"location": l, "workplace": "", "is_remote": remote}
    assert not main.matches_location(loc("IN-Bangalore-MSO"))
    assert not main.matches_location(loc("Remote - Canada"))
    assert not main.matches_location(loc("London, UK"))
    assert main.matches_location(loc("Remote U.S."))
    assert main.matches_location(loc("Remote (US or Canada)"))
    assert main.matches_location(loc("Milwaukee, WI"))  # "uk" inside a word is not the UK
    assert main.matches_location(loc("Chicago, IL", remote=False))


def test_unrelated_functions_ranked_below_senior_generalists():
    people = [
        {"name": "Cee Fo", "title": "CFO", "seniority": "c_suite"},
        {"name": "Pat Partner", "title": "Director of Partner Management", "seniority": "director"},
        {"name": "Gus General", "title": "Chief of Staff", "seniority": "executive"},
        {"name": "Fran FDE", "title": "Forward Deployed Engineer"},
    ]
    names = [p["name"] for p in contacts.fallback_picks(people, {"title": "Forward Deployed Engineer"})]
    assert names[:2] == ["Fran FDE", "Gus General"]


def test_level_filter_skips_senior_titles_and_big_year_asks():
    f = {"max_years_required": 5}
    def j(title, desc=""):
        return {"title": title, "description": desc}
    assert not main.matches_level(j("Senior Solutions Engineer"), f)
    assert not main.matches_level(j("Sr. Forward Deployed Engineer"), f)
    assert not main.matches_level(j("Staff AI Engineer"), f)
    assert not main.matches_level(j("Solutions Engineer III"), f)
    assert main.matches_level(j("Solutions Engineer II"), f)
    assert main.matches_level(j("Forward Deployed Engineer"), f)
    assert main.matches_level(j("Leadership Programs Engineer"), f)
    assert not main.matches_level(j("Solutions Engineer", "You have 7+ years of experience in pre-sales."), f)
    assert main.matches_level(j("Solutions Engineer", "3-5 years of relevant experience; 10 years of experience a plus"), f)
    assert main.matches_level(j("Solutions Engineer", "Experience with Python."), f)


def test_accented_and_asian_cities_are_non_us():
    assert main.is_non_us("São Paulo")
    assert main.is_non_us("Taipei")
    assert not main.is_non_us("Remote - US")
    assert not main.is_non_us("Chicago, IL")


# ─────────────────────────────────────────────
# Company career sites
# ─────────────────────────────────────────────
import careers


def test_finds_board_behind_a_careers_page():
    gh = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=retool"></script>'
    assert careers.board_in_html(gh) == {"ats": "greenhouse", "slug": "retool"}
    assert careers.board_in_html('<a href="https://jobs.lever.co/acme/123">x</a>') == {"ats": "lever", "slug": "acme"}
    wd = 'https://acme.wd5.myworkdayjobs.com/en-US/External_Careers/job/Chicago/SE_R123'
    assert careers.board_in_html(wd) == {"ats": "workday", "slug": "acme|wd5|External_Careers"}
    assert careers.board_in_html("<p>no board here</p>") is None


LD = '''<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
"title":"Solutions Engineer","datePosted":"2026-10-29","url":"https://acme.com/careers/se",
"jobLocation":{"@type":"Place","address":{"addressLocality":"Chicago","addressRegion":"IL","addressCountry":"US"}},
"baseSalary":{"@type":"MonetaryAmount","value":{"@type":"QuantitativeValue","minValue":130000,"maxValue":160000,"unitText":"YEAR"}},
"description":"<p>Help customers.</p>"}</script>'''


def test_self_hosted_careers_page_jobs_are_read(monkeypatch):
    listing = ('<a href="/careers/se">Solutions Engineer</a><a href="/careers/ae">Account Executive</a>'
               '<a href="/about">About</a>')
    pages = {"https://acme.com/careers": listing, "https://acme.com/careers/se": LD}
    fetched = []

    def fake_html(url):
        fetched.append(url)
        return (url, pages[url]) if url in pages else ("", "")

    monkeypatch.setattr(careers, "_html", fake_html)
    assert careers.find_careers_board("acme.com") == {"ats": "site", "slug": "https://acme.com/careers"}

    careers.ROLE_TOKENS, careers.SEEN = ["solutions engineer"], set()
    jobs = careers.fetch_site("Acme", "https://acme.com/careers")
    assert "https://acme.com/careers/ae" not in fetched  # wrong role: page never fetched
    assert len(jobs) == 1
    j = jobs[0]
    assert (j["title"], j["location"], j["compensation"]) == ("Solutions Engineer", "Chicago, IL, US", 160000)
    assert main.matches_location(j) and j["id"].startswith("site:acme.com:")


def test_workday_jobs_are_read(monkeypatch):
    class R:
        status_code = 200
        def __init__(self, data): self._d = data
        def json(self): return self._d
    calls = []

    def fake_post(url, json=None, **kw):
        calls.append((url, json["searchText"]))
        return R({"total": 1, "jobPostings": [{"title": "Solutions Engineer",
                  "externalPath": "/job/Chicago-IL/Solutions-Engineer_R555", "locationsText": "Chicago, IL",
                  "postedOn": "Posted 3 Days Ago"}]})

    monkeypatch.setattr(careers.requests, "post", fake_post)
    careers.ROLE_TOKENS = ["solutions engineer"]
    jobs = careers.fetch_workday("Acme", "acme|wd5|External")
    assert calls[0] == ("https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs", "solutions engineer")
    assert jobs[0]["id"] == "workday:acme:R555"
    assert jobs[0]["url"] == "https://acme.wd5.myworkdayjobs.com/External/job/Chicago-IL/Solutions-Engineer_R555"
    assert main.is_fresh(jobs[0], 21)


def test_probe_falls_back_to_careers_page(monkeypatch):
    monkeypatch.setattr(discover, "_board_jobs", lambda ats, slug: None)
    monkeypatch.setattr(discover, "find_careers_board", lambda d: {"ats": "site", "slug": f"https://{d}/careers"})
    assert discover.probe_board("Acme", "acme.com") == {"ats": "site", "slug": "https://acme.com/careers"}


def test_undated_jobs_on_a_new_board_go_to_the_digest(monkeypatch):
    j = job("s:1", "Solutions Engineer", 1)
    j["posted"] = ""
    monkeypatch.setitem(main.FETCHERS, "fake", lambda company, slug: [j])
    out = main.scan([{"name": "Growthy", "ats": "fake", "slug": "g"}],
                    main._role_tokens(["solutions engineer"]), set(), empty_cache(), {})
    assert out[0]["backlog"]


def test_site_job_without_jobposting_uses_page_heading(monkeypatch):
    listing = '<a href="/careers/se"><div>Solutions Engineer</div><p>Full-Time, SF &amp; Remote, Remote Execution</p></a>'
    detail = "<html><h1>Solutions Engineer</h1><p>Location: Remote (US)</p></html>"
    pages = {"https://b.io/careers": listing, "https://b.io/careers/se": detail}
    monkeypatch.setattr(careers, "_html", lambda u: (u, pages[u]) if u in pages else ("", ""))
    careers.ROLE_TOKENS, careers.SEEN = ["solutions engineer"], set()
    j = careers.fetch_site("B", "https://b.io/careers")[0]
    assert j["title"] == "Solutions Engineer" and j["location"].startswith("Remote")


def test_rippling_location_without_city(monkeypatch):
    monkeypatch.setattr(main, "_get", lambda url: {"items": [{"id": 1, "name": "Solutions Engineer",
                        "locations": [{"city": None, "name": "Remote (US)"}]}]})
    assert main.fetch_rippling("X", "x")[0]["location"] == "Remote (US)"
