import os
import sys
from types import SimpleNamespace

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boards_extra
import discover
import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fake_get(pages: dict):
    """Stands in for boards_extra._get: serves canned HTML by URL prefix."""
    def _get(url, **kw):
        for prefix, text in pages.items():
            if url.startswith(prefix):
                return SimpleNamespace(text=text, url=url, status_code=200)
        return None
    return _get


# ─────────────────────────────────────────────
# Profiles
# ─────────────────────────────────────────────
def test_buddy_profile_loads_and_every_board_has_a_fetcher():
    with open(os.path.join(ROOT, "profiles", "buddy.yml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    pinned = main.pinned_boards(cfg)
    assert len(pinned) >= 30
    assert all(c["ats"] in main.FETCHERS for c in pinned)
    assert cfg["Discovery"]["enabled"] is False
    assert cfg["Contacts"]["enabled"] is False


def test_use_profile_moves_state_and_webhook(monkeypatch, tmp_path):
    monkeypatch.setenv("BUDDY_DISCORD_WEBHOOK_URL", "https://discord.test/buddy")
    saved = (main.CONFIG_PATH, main.SEEN_PATH, main.DISCORD_WEBHOOK_URL,
             discover.CACHE_PATH, discover.CONTACTS_PATH)
    try:
        main.use_profile("buddy")
        assert main.CONFIG_PATH.endswith(os.path.join("profiles", "buddy.yml"))
        assert main.SEEN_PATH.endswith(os.path.join("profiles", "buddy", ".seen_jobs.json"))
        assert discover.CACHE_PATH.endswith(os.path.join("profiles", "buddy", ".board_cache.json"))
        assert main.DISCORD_WEBHOOK_URL == "https://discord.test/buddy"
    finally:
        (main.CONFIG_PATH, main.SEEN_PATH, main.DISCORD_WEBHOOK_URL,
         discover.CACHE_PATH, discover.CONTACTS_PATH) = saved


def test_pinned_boards_are_used_as_given_and_fill_missing_locations(monkeypatch):
    cfg = {"Companies": [{"name": "Village of Niles", "ats": "page", "slug": "https://x.test/jobs",
                          "location": "Niles, IL"}],
           "Discovery": {"enabled": False}}
    cache = {"boards": {}, "seeded_boards": ["page:https://x.test/jobs"], "discovered": {}, "contacts": {}}
    targets = main.all_targets(cfg, cache)
    assert [(t["ats"], t["location"]) for t in targets] == [("page", "Niles, IL")]

    monkeypatch.setitem(main.FETCHERS, "page", lambda company, slug: [
        {"id": "page:x:1", "company": company, "title": "Water Plant Operator", "url": "https://x.test/1",
         "location": "", "workplace": "", "is_remote": False, "compensation": None, "posted": "",
         "description": ""}])
    found = main.scan(targets, ["water"], set(), cache, {"min_salary": 0})
    assert [(j["title"], j["location"]) for j in found] == [("Water Plant Operator", "Niles, IL")]


# ─────────────────────────────────────────────
# Fetchers (canned pages, no network)
# ─────────────────────────────────────────────
ICIMS_HTML = """
<ul class="container-fluid iCIMS_JobsTable">
<li class="iCIMS_JobCardItem"><div class="row">
<div class="col-xs-6 header left"><span class="sr-only field-label">Location</span><span >
US-IL-Chicago</span></div>
<div class="col-xs-12 title">
<a href="https://careers-acme.icims.com/jobs/5072/staff-environmental-engineer/job?in_iframe=1" class="iCIMS_Anchor">
<span class="sr-only field-label">Title</span><h3 >Staff Environmental Engineer</h3></a></div>
<div class="col-xs-12 description">Field work and reports.</div>
<dt class="iCIMS_JobHeaderField">Remote</dt><dd class="iCIMS_JobHeaderData"><span >No</span></dd>
</div></li>
</ul>
"""


def test_icims_reads_job_cards(monkeypatch):
    pages = {"https://careers-acme.icims.com/jobs/search?pr=0": ICIMS_HTML}
    monkeypatch.setattr(boards_extra, "_get", fake_get(pages))
    jobs = boards_extra.fetch_icims("Acme", "careers-acme")
    assert [(j["title"], j["location"], j["is_remote"]) for j in jobs] == [
        ("Staff Environmental Engineer", "Chicago, IL, US", False)]
    assert jobs[0]["url"].endswith("/jobs/5072/staff-environmental-engineer/job")


NEOGOV_HTML = """
<ul class="search-results-listing-container">
<li class="list-item" data-job-id="5397926">
<h3><a class="item-details-link" href="/careers/winnetka/jobs/5397926/water-plant-operator">Water Plant Operator</a></h3>
<ul class="list-meta"><li>Winnetka, IL</li><li>Full-time - $30.00 - $45.00 Hourly</li></ul>
<div class="list-entry">Runs the plant. Posted 11/02/2026</div>
</li>
</ul>
"""


def test_neogov_reads_rows_and_hourly_pay(monkeypatch):
    calls = []

    def _get(url, **kw):
        calls.append(kw.get("params", {}).get("page"))
        text = NEOGOV_HTML if kw["params"]["page"] == 1 else ""
        return SimpleNamespace(text=text, url=url, status_code=200)
    monkeypatch.setattr(boards_extra, "_get", _get)
    jobs = boards_extra.fetch_neogov("Village of Winnetka", "winnetka")
    assert [(j["title"], j["location"], j["posted"][:10], j["compensation"]) for j in jobs] == [
        ("Water Plant Operator", "Winnetka, IL", "2026-11-02", 93600)]
    assert calls == [1, 2]


TALEO_HTML = """<script>
api.fillList('requisitionListInterface', 'listRequisition', ['false','false','false','205479','CIVIL ENGINEER IV','205479',
'CIVIL ENGINEER IV','205479','205479','205479','205479','205479','%2488,272.00 - %24154,044.00','Nov 9, 2026','419006','Apply']);
</script>"""


def test_taleo_reads_the_requisition_list(monkeypatch):
    monkeypatch.setattr(boards_extra, "_get", fake_get({"https://chicago.taleo.net/": TALEO_HTML}))
    jobs = boards_extra.fetch_taleo("City of Chicago", "chicago|100")
    assert [(j["id"], j["title"], j["posted"][:10]) for j in jobs] == [
        ("taleo:chicago:205479", "CIVIL ENGINEER IV", "2026-11-09")]


def test_page_links_skip_navigation_and_respect_href_filter():
    html = """
    <a href="/jobs">Jobs</a>
    <a href="/careers/culture">Culture</a>
    <a href="/job/chicago/staff-scientist/1/2"><h2>Staff Scientist</h2><span>Chicago</span></a>
    <a href="https://other.test/about">About us</a>
    """
    every = boards_extra.page_links("https://acme.test/search", html)
    assert ("https://acme.test/careers/culture", "Culture") in every
    only_jobs = boards_extra.page_links("https://acme.test/search", html, boards_extra.re.compile("/job/"))
    assert only_jobs == [("https://acme.test/job/chicago/staff-scientist/1/2", "Staff Scientist")]


def test_jobsyn_marks_foreign_jobs_so_the_location_filter_drops_them(monkeypatch):
    payload = {"jobs": [
        {"guid": "A", "title_exact": "Water Resources Engineer", "location_exact": "Virtual, COL",
         "country_exact": "Colombia", "country_short_exact": "COL", "date_new": "2026-11-01T00:00:00Z"},
        {"guid": "B", "title_exact": "Water Resources Engineer", "location_exact": "Chicago, IL",
         "country_exact": "United States", "country_short_exact": "USA", "date_new": "2026-11-01T00:00:00Z"},
    ], "pagination": {"has_more_pages": False}}
    monkeypatch.setattr(boards_extra.requests, "get",
                        lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: payload))
    monkeypatch.setattr(boards_extra, "ROLE_TOKENS", ["water"])
    monkeypatch.setattr(boards_extra, "SEARCH_LOCATION", "")
    jobs = boards_extra.fetch_jobsyn("WSP", "wspgroup.dejobs.org")
    assert [main.matches_location(j) for j in jobs] == [False, True]
