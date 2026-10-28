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
        ["trigger-dev", "trigger", "triggerdev"]
    assert discover.candidate_slugs("Hex", "hex.tech")[0] == "hex"


def test_probe_board_tries_slugs_then_ats(monkeypatch):
    calls = []

    def fake(ats, slug):
        calls.append((ats, slug))
        return [] if (ats, slug) == ("greenhouse", "growthy") else None

    monkeypatch.setattr(discover, "_board_jobs", fake)
    assert discover.probe_board("Growthy", "growthy.ai") == {"ats": "greenhouse", "slug": "growthy"}
    assert calls[:2] == [("ashby", "growthy"), ("greenhouse", "growthy")]


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


def test_first_scan_only_alerts_fresh_jobs_then_alerts_new_ones(monkeypatch):
    board = {"jobs": [
        job("a:1", "Forward Deployed Engineer", 24 * 10),  # old: recorded silently
        job("a:2", "Solutions Engineer", 3),               # fresh: alert
        job("a:3", "Account Executive", 1),                # wrong role
        job("a:4", "AI Engineer", 2, location="London, UK"),  # wrong place
    ]}
    monkeypatch.setitem(main.FETCHERS, "fake", lambda company, slug: list(board["jobs"]))
    targets = [{"name": "Growthy", "ats": "fake", "slug": "growthy"}]
    tokens = main._role_tokens(["forward deployed engineer", "solutions engineer", "ai engineer"])
    seen, cache = set(), empty_cache()

    first = main.scan(targets, tokens, seen, cache, {})
    assert [j["id"] for j in first] == ["a:2"]
    assert seen == {"a:1", "a:2"}

    board["jobs"].append(job("a:5", "Applied AI Engineer", 0.2))
    second = main.scan(targets, tokens, seen, cache, {})
    assert [j["id"] for j in second] == ["a:5"]
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
    names = [p["name"] for p in contacts.fallback_picks(PEOPLE)]
    assert names == ["Sam Solutions", "Ed Eng", "Fay Founder"]


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
