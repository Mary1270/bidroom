import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
CONTRACT = open(os.path.join(ROOT, "contract.py"), encoding="utf-8").read()


def methods(kind):
    tree = ast.parse(CONTRACT)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BidRoom")
    out = set()
    for n in cls.body:
        if isinstance(n, ast.FunctionDef) and not n.name.startswith("_"):
            if any(ast.unparse(d).startswith("gl.public." + kind) for d in n.decorator_list):
                out.add(n.name)
    return out


def script():
    return re.search(r'<script type="module">(.*?)</script>', HTML, re.S).group(1)


class TestFrontend(unittest.TestCase):
    def test_single_file_with_esm_sh_latest(self):
        self.assertIn('from "https://esm.sh/genlayer-js@latest"', HTML)
        self.assertIn('from "https://esm.sh/genlayer-js@latest/chains"', HTML)
        self.assertEqual(len(re.findall(r"<script\b", HTML)), 1)
        self.assertNotIn("<link", HTML)

    def test_clients_use_only_chain_and_account(self):
        calls = re.findall(r"createClient\(\{([^}]*)\}\)", HTML)
        self.assertGreaterEqual(len(calls), 4)
        for body in calls:
            self.assertTrue(body.strip().startswith("chain: studionet"), body)
        for forbidden in ("initializeConsensusSmartContract", ".connect(", "provider:"):
            self.assertNotIn(forbidden, script())

    def test_routes_and_router(self):
        for route in ('href="#/"', 'href="#/new"', 'href="#/me"', 'href="#/how"', "#/project/"):
            self.assertIn(route, HTML)
        self.assertIn('addEventListener("hashchange", route)', HTML)

    def test_every_called_method_exists(self):
        called = set(re.findall(r'(?:read|readJson)\("([a-z_]+)"', script()))
        called |= set(re.findall(r'(?:go|write)\([^;]*?"([a-z_]+)",\s*\[', script()))
        self.assertEqual(called - methods("view") - methods("write"), set(), called)

    def test_every_write_method_is_reachable(self):
        used = set(re.findall(r'"([a-z_]+)"', script()))
        for name in methods("write"):
            self.assertIn(name, used)

    def test_every_view_used_by_the_ui_exists(self):
        for name in ("list_projects", "get_stats", "get_project", "get_actions", "get_profile", "get_accounting", "get_pending_withdrawal",
                     "get_activity", "get_address_projects"):
            self.assertIn(name, methods("view"))
            self.assertIn(f'"{name}"', script())

    def test_create_argument_order_matches_the_contract(self):
        sig = re.search(r"def create_project\((.*?)\) -> str", CONTRACT, re.S).group(1)
        params = [p.strip().split(":")[0] for p in sig.split(",") if p.strip() and p.strip() != "self"]
        self.assertEqual(params, ["title", "brief", "criteria", "weights", "milestone_titles", "milestone_acceptance",
                                  "milestone_bps", "max_delivery_minutes", "bond_bps", "bidding_minutes",
                                  "accept_minutes", "review_minutes", "grace_minutes"])
        call = re.search(r'"create_project",\s*\[(.*?)\], BigInt', script(), re.S).group(1)
        self.assertEqual(len(re.findall(r"(?:^|,)\s*(?:mins\.|mss\.|crits\.|bondPct|title|brief|ct)", call)), 13)

    def test_place_bid_argument_order(self):
        sig = re.search(r"def place_bid\((.*?)\) -> str", CONTRACT, re.S).group(1)
        self.assertEqual([p.strip().split(":")[0] for p in sig.split(",")[1:]], ["project_id", "price_gen", "delivery_minutes", "proposal"])
        self.assertIn('"place_bid", [id, pr, minutes, prop.value.trim()], BigInt(p.bond)', script())

    def test_contract_data_is_never_inserted_as_html(self):
        self.assertNotIn("innerHTML", script())
        self.assertNotIn("insertAdjacentHTML", script())
        self.assertNotIn("document.write", script())

    def test_no_eval_and_no_extra_remote_code(self):
        self.assertNotIn("eval(", script())
        self.assertEqual(set(re.findall(r"https://[a-z.]+/", script())) - {"https://esm.sh/", "https://raw.githubusercontent.com/"}, set())

    def test_money_is_exact_bigint(self):
        self.assertIn("BigInt(budget) * WEI_PER_GEN", script())
        self.assertNotIn("parseFloat", script())

    def test_no_double_submit_and_no_refresh_during_a_transaction(self):
        js = script()
        self.assertIn("if (busy) {", js)
        self.assertIn("finally { busy--;", js)
        self.assertIn("if (quiet && (busy ||", js)

    def test_bid_price_is_checked_against_the_budget(self):
        self.assertIn("BigInt(pr) * WEI_PER_GEN > BigInt(p.budget)", script())

    def test_no_staged_demo_data_on_the_page(self):
        for fake in ("Landing page redesign &middot; 3 bids", "100 GEN budget locked", "Winner picked on C1, C2, C3"):
            self.assertNotIn(fake, HTML)

    def test_public_profiles_and_arbitration_report(self):
        js = script()
        self.assertIn('startsWith("#/u/")', js)
        self.assertIn("function arbitrationReport(", js)
        self.assertIn("function selectionReport(", js)
        self.assertIn("function escrowCard(", js)
        for field in ("r.quote", "r.complaint", "r.delivery_url", "m.acceptance", "r.ruled_by"):
            self.assertIn(field, js)

    def test_event_names_match_the_contract(self):
        logged = set(re.findall(r'self\._log\(\s*project,\s*"([a-z_]+)"', CONTRACT))
        mapped = set(re.findall(r'^\s*(?:const EVT = \{)?\s*((?:[a-z_]+: \[[^\]]*\],?\s*)+)', script(), re.M) and re.findall(r'([a-z_]+): \["(?:info|ai|risk|money)"', script()))
        self.assertTrue(logged)
        self.assertEqual(logged - mapped, set(), logged - mapped)

    def test_phase_tracker_and_role_panel_exist(self):
        for text in ("Your move", "Who can do what right now", '"Bidding"', '"Selection"', '"Delivery"', '"Dispute"', "get_actions"):
            self.assertIn(text, HTML)


if __name__ == "__main__":
    unittest.main()
