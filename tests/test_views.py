import json
import unittest

from _bootstrap import (ALICE, BOB, BOND, BUDGET, CAROL, CLIENT, CLOCK, DELIVERY_URL, PAGE_TEXT, STRANGER, FakeWorld, UserError,
                        World, assert_balanced, create_project, get, make_contract, place_bid, set_caller,
                        to_delivery, to_selection, withdraw)


class TestViews(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        self.world = World()
        self.world.__enter__()
        self.addCleanup(self.world.__exit__)

    def phase(self, pid="project_0"):
        return json.loads(self.c.get_phase(pid))

    def test_phase_follows_the_whole_lifecycle(self):
        create_project(self.c)
        info = self.phase()
        self.assertEqual((info["phase"], info["waiting_on"]), ("bidding", "bidders"))
        place_bid(self.c, ALICE)
        place_bid(self.c, BOB, price=3000)
        CLOCK.minutes(61)
        FakeWorld.scores = {"B0": [3, 3], "B1": [1, 1]}
        info = self.phase()
        self.assertEqual((info["phase"], info["waiting_on"]), ("selection", "anyone"))
        set_caller(STRANGER)
        self.c.select_winner("project_0")
        info = self.phase()
        self.assertEqual((info["phase"], info["status"], info["waiting_on"]), ("selection", "awarded", "winner"))
        set_caller(ALICE)
        self.c.confirm_award("project_0")
        info = self.phase()
        self.assertEqual((info["phase"], info["waiting_on"]), ("delivery", "winner"))
        self.c.submit_milestone("project_0", 0, DELIVERY_URL, "n")
        self.assertEqual(self.phase()["waiting_on"], "client")
        set_caller(CLIENT)
        self.c.dispute_milestone("project_0", 0, "The page does not match the acceptance criteria")
        info = self.phase()
        self.assertEqual((info["phase"], info["waiting_on"]), ("dispute", "anyone"))
        set_caller(STRANGER)
        self.c.rule_dispute("project_0", 0)
        self.assertEqual(self.phase()["phase"], "delivery")
        set_caller(ALICE)
        self.c.submit_milestone("project_0", 1, DELIVERY_URL, "n")
        set_caller(CLIENT)
        self.c.approve_milestone("project_0", 1)
        info = self.phase()
        self.assertEqual((info["phase"], info["waiting_on"], info["next_deadline"]), ("closed", "none", None))

    def test_phase_flips_to_selection_when_time_passes_without_any_transaction(self):
        create_project(self.c)
        self.assertEqual(self.phase()["phase"], "bidding")
        CLOCK.minutes(61)
        self.assertEqual(self.phase()["phase"], "selection")
        self.assertEqual(get(self.c)["status"], "bidding")

    def test_roles(self):
        to_delivery(self.c)
        roles = lambda who: json.loads(self.c.get_actions("project_0", who))["roles"]
        self.assertEqual(roles(CLIENT), ["client"])
        self.assertEqual(roles(ALICE), ["bidder", "winner"])
        self.assertEqual(roles(BOB), ["bidder"])
        self.assertEqual(roles(STRANGER), [])
        self.assertEqual(json.loads(self.c.get_actions("project_0", ""))["actions"], [])

    def test_actions_by_role_in_delivery(self):
        to_delivery(self.c)
        names = lambda who: [a["action"] for a in json.loads(self.c.get_actions("project_0", who))["actions"]]
        self.assertEqual(names(ALICE), ["submit_milestone"])
        self.assertEqual(names(CLIENT), ["withdraw"])
        self.assertEqual(names(STRANGER), [])
        self.assertEqual(names(BOB), ["withdraw"])

    def test_list_projects_newest_first_with_paging(self):
        for _ in range(5):
            create_project(self.c)
        page = json.loads(self.c.list_projects(0, 2))
        self.assertEqual((page["total"], [p["project_id"] for p in page["projects"]]), (5, ["project_4", "project_3"]))
        page = json.loads(self.c.list_projects(4, 10))
        self.assertEqual([p["project_id"] for p in page["projects"]], ["project_0"])
        self.assertEqual(json.loads(self.c.list_projects(99, 10))["projects"], [])
        self.assertEqual(len(json.loads(self.c.list_projects(0, 1000))["projects"]), 5)

    def test_summary_fields(self):
        to_delivery(self.c)
        row = json.loads(self.c.list_projects(0, 5))["projects"][0]
        self.assertEqual((row["phase"], row["winner"], row["price"], row["milestones_total"], row["milestones_done"]),
                         ("delivery", ALICE, "5000", 2, 0))

    def test_history_records_every_step_in_order(self):
        to_delivery(self.c)
        events = [e["event"] for e in json.loads(self.c.get_history("project_0"))]
        self.assertEqual(events, ["project_created", "bid_placed", "bid_placed", "winner_selected", "award_confirmed"])
        self.assertTrue(all(e["by"] and e["t"] for e in json.loads(self.c.get_history("project_0"))))

    def test_history_is_capped(self):
        create_project(self.c)
        from _bootstrap import m
        p = get(self.c)
        for i in range(400):
            p["history"].append({"t": "x", "event": "e", "by": "y", "detail": str(i)})
        set_caller(CLIENT)
        self.c._log(p, "last", "d")
        self.assertEqual(len(p["history"]), m.MAX_HISTORY)
        self.assertEqual(p["history"][-1]["event"], "last")

    def test_get_bids_and_milestones(self):
        to_delivery(self.c)
        self.assertEqual(len(json.loads(self.c.get_bids("project_0"))), 2)
        self.assertEqual([x["amount"] for x in json.loads(self.c.get_milestones("project_0"))], ["3000", "2000"])

    def test_stats(self):
        to_delivery(self.c)
        create_project(self.c)
        s = json.loads(self.c.get_stats())
        self.assertEqual((s["total"], s["in_progress"], s["bids_placed"]), (2, 2, 2))
        self.assertEqual(s["by_status"], {"delivery": 1, "bidding": 1})

    def test_profile_for_each_role(self):
        to_delivery(self.c)
        set_caller(ALICE)
        self.c.submit_milestone("project_0", 0, DELIVERY_URL, "n")
        set_caller(CLIENT)
        self.c.approve_milestone("project_0", 0)
        alice = json.loads(self.c.get_profile(ALICE))
        self.assertEqual(alice["as_bidder"]["won"], 1)
        self.assertEqual(alice["as_winner"]["awarded"], 1)
        self.assertEqual(alice["as_winner"]["earned_wei"], "3000")
        self.assertEqual(alice["pending_withdrawal"], "3000")
        self.assertIsNone(alice["reliability_bps"])
        bob = json.loads(self.c.get_profile(BOB))
        self.assertEqual((bob["as_bidder"]["bids"], bob["as_bidder"]["lost"]), (1, 1))
        client = json.loads(self.c.get_profile(CLIENT))
        self.assertEqual((client["as_client"]["projects"], client["as_client"]["open"]), (1, 1))
        self.assertEqual(json.loads(self.c.get_profile(STRANGER))["as_bidder"]["bids"], 0)

    def test_profile_reliability_after_completion(self):
        to_delivery(self.c)
        set_caller(ALICE)
        self.c.submit_milestone("project_0", 0, DELIVERY_URL, "n")
        set_caller(CLIENT)
        self.c.approve_milestone("project_0", 0)
        set_caller(ALICE)
        self.c.submit_milestone("project_0", 1, DELIVERY_URL, "n")
        set_caller(CLIENT)
        self.c.approve_milestone("project_0", 1)
        self.assertEqual(json.loads(self.c.get_profile(ALICE))["reliability_bps"], 10000)

    def test_accounting_view_and_balance(self):
        to_delivery(self.c)
        acc = json.loads(self.c.get_accounting())
        self.assertTrue(acc["balanced"])
        self.assertEqual(acc["locked_in_projects"], str(5000 + BOND))
        self.assertEqual(acc["pending_withdrawals"], str(BUDGET - 5000 + BOND))
        self.assertEqual(self.c.get_contract_balance(), str(BUDGET + 2 * BOND))

    def test_withdraw_requires_a_balance_and_empties_it(self):
        set_caller(ALICE)
        with self.assertRaises(UserError):
            self.c.withdraw()
        to_delivery(self.c)
        self.assertEqual(withdraw(self.c, BOB), BOND)
        with self.assertRaises(UserError):
            set_caller(BOB)
            self.c.withdraw()

    def test_activity_feed_merges_all_projects_newest_first(self):
        to_delivery(self.c)
        CLOCK.minutes(1)
        create_project(self.c, title="Second project title")
        feed = json.loads(self.c.get_activity(4))
        self.assertEqual(len(feed), 4)
        self.assertEqual((feed[0]["event"], feed[0]["project_id"], feed[0]["title"]), ("project_created", "project_1", "Second project title"))
        self.assertEqual(feed[1]["event"], "award_confirmed")
        times = [e["t"] for e in feed]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(len(json.loads(self.c.get_activity(999))), 6)
        self.assertEqual(len(json.loads(self.c.get_activity(0))), 1)

    def test_address_projects_cover_every_role(self):
        to_delivery(self.c)
        create_project(self.c)
        client = json.loads(self.c.get_address_projects(CLIENT))
        self.assertEqual([r["project_id"] for r in client], ["project_1", "project_0"])
        self.assertTrue(all(r["roles"] == ["client"] for r in client))
        alice = json.loads(self.c.get_address_projects(ALICE.upper().replace("0X", "0x")))
        self.assertEqual(len(alice), 1)
        self.assertEqual(alice[0]["roles"], ["bidder", "winner"])
        self.assertEqual(alice[0]["my_bids"][0]["status"], "won")
        self.assertIn("released", alice[0])
        bob = json.loads(self.c.get_address_projects(BOB))
        self.assertEqual((bob[0]["roles"], bob[0]["my_bids"][0]["status"]), (["bidder"], "lost"))
        self.assertEqual(json.loads(self.c.get_address_projects(STRANGER)), [])

    def test_rulings_keep_the_evidence_they_judged(self):
        to_delivery(self.c)
        set_caller(ALICE)
        self.c.submit_milestone("project_0", 0, DELIVERY_URL, "first try")
        set_caller(CLIENT)
        self.c.dispute_milestone("project_0", 0, "The first version misses the setup part")
        FakeWorld.ruling = "REJECTED"
        set_caller(STRANGER)
        self.c.rule_dispute("project_0", 0)
        set_caller(ALICE)
        self.c.submit_milestone("project_0", 0, "https://example.com/second.md", "second try")
        set_caller(CLIENT)
        self.c.dispute_milestone("project_0", 0, "Still not complete, the setup part is thin")
        FakeWorld.pages["https://example.com/second.md"] = PAGE_TEXT
        FakeWorld.ruling = "ACCEPTED"
        set_caller(BOB)
        self.c.rule_dispute("project_0", 0)
        first, second = get(self.c)["milestones"][0]["rulings"]
        self.assertEqual((first["delivery_url"], first["note"], first["complaint"], first["ruled_by"]),
                         (DELIVERY_URL, "first try", "The first version misses the setup part", STRANGER))
        self.assertEqual((second["delivery_url"], second["note"], second["ruled_by"]), ("https://example.com/second.md", "second try", BOB))
        self.assertTrue(first["submitted_at"] and first["disputed_at"])

    def test_unknown_project_views_raise(self):
        for fn in (self.c.get_project, self.c.get_bids, self.c.get_milestones, self.c.get_history, self.c.get_phase):
            with self.assertRaises(UserError):
                fn("project_9")
        with self.assertRaises(UserError):
            self.c.get_actions("project_9", ALICE)


if __name__ == "__main__":
    unittest.main()
