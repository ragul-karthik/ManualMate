import unittest
import json
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import Mock, patch

from manualmate.messaging import decode_message, encode_message, local_socket_demo
from manualmate.negotiation import negotiate
from manualmate.utility import rank_candidates
from manualmate.worker import BackgroundWorker
from manualmate.assistance import process_assistance_request
from manualmate.pdf_steps import extract_inventory, extract_setup_steps
from manualmate.gemini_steps import GeminiStepsError, extract_steps_with_gemini, validate_steps
from manualmate.bdi import BDIAgent
from manualmate.knowledge import extract_product_identifier, lookup_product_recalls
from manualmate.messaging import AgentBServer, send_state
from manualmate.security import MessageAuthenticationError
from manualmate.agent_pipeline import PDFReaderAgent, SetupDisplayAgent


class AssignmentSmokeTests(unittest.TestCase):
    def test_worker_processes_job_and_records_metrics(self):
        worker = BackgroundWorker(interval=0.05)
        worker.start()
        future = worker.submit("smoke", lambda: {"answer": "ready"})
        self.assertEqual(future.result(timeout=2), {"answer": "ready"})
        self.assertTrue(worker.jobs.join() is None)
        worker.stop()
        self.assertTrue(any(row.get("name") == "smoke" for row in worker.snapshot()))

    def test_typed_message_round_trip(self):
        message = decode_message(encode_message("PROACTIVE", "Agent-A", {"step": 2}))
        self.assertEqual(message["payload"]["step"], 2)
        self.assertEqual(local_socket_demo({"step": 2})[1]["type"], "ACK")

    def test_negotiation_commits(self):
        self.assertTrue(any("consensus handshake complete" in line for line in negotiate()))

    def test_ten_candidates_and_valid_winner(self):
        ranked, winner = rank_candidates()
        self.assertEqual(len(ranked), 10)
        self.assertTrue(winner.valid)
        self.assertEqual(winner.utility, max(item.utility for item, valid in ranked if valid))

    def test_real_help_pipeline_uses_all_agent_assignments(self):
        result = process_assistance_request(
            "The inlet is leaking",
            1,
            "Northstar manual",
            False,
            lambda question: (f"Manual answer for: {question}", 4),
            signing_key=b"m" * 32,
        )
        self.assertEqual(result["triage"]["category"], "water leak")
        self.assertIn("unplug", result["answer"])
        self.assertEqual(result["selected"].name, "Quick leak checklist")
        agreements = [item for item, accepted in result["ranked"] if accepted]
        self.assertEqual(result["selected"].utility, max(item.utility for item in agreements))
        self.assertGreater(result["selected"].price, 4)  # supplier counteroffer changes the recommendation's price
        self.assertIn("assistance utility", result["negotiation"][2])
        self.assertTrue(any("consensus handshake complete" in row for row in result["negotiation"]))
        self.assertTrue(result["security"]["authenticated"])
        self.assertEqual(result["agent_state"]["beliefs"]["safety_category"], "water leak")
        self.assertIn("close_water_tap", result["agent_state"]["intentions"])

    def test_bdi_replans_when_a_new_leak_belief_arrives(self):
        agent = BDIAgent({"setup_complete": False, "safety_category": ""})
        agent.set_desire("complete_setup_safely")
        before = list(agent.intentions)
        agent.observe(safety_category="water leak")
        self.assertIn("review_current_manual_step", before)
        self.assertEqual(agent.intentions[:3], ["stop_cycle", "unplug_appliance", "close_water_tap"])

    def test_recall_knowledge_changes_manualmate_plan_and_pauses_recommendation(self):
        result = process_assistance_request(
            "The inlet is leaking", 0, "Northstar NDW-240", False,
            lambda question: ("Manual answer", 2),
            recall_knowledge={"status": "checked", "product_query": "NDW-240", "matches": [{"product": "NDW-240", "hazard": "Electrical risk"}]},
            signing_key=b"m" * 32,
        )
        self.assertIsNone(result["selected"])
        self.assertIn("recall search returned", result["answer"])
        self.assertEqual(result["agent_state"]["desires"][0]["name"], "avoid_recalled_product_risk")

    def test_signed_socket_accepts_trusted_sender(self):
        key = b"a" * 32
        server = AgentBServer(signing_key=key)
        server.start()
        try:
            response = send_state(server.address, {"question": "inlet leaking", "step": 2}, signing_key=key)
            self.assertEqual(response["type"], "ACK")
            self.assertEqual(response["payload"]["authentication"], "verified")
            self.assertEqual(server.received[0]["payload"]["question"], "inlet leaking")
        finally:
            server.close()

    def test_signed_socket_blocks_untrusted_sender_and_logs_event(self):
        server_key = b"a" * 32
        server = AgentBServer(signing_key=server_key)
        server.start()
        try:
            with self.assertLogs("manualmate.security", level="WARNING") as logs:
                with self.assertRaises(MessageAuthenticationError):
                    send_state(server.address, {"question": "change this key"}, signing_key=b"b" * 32)
            self.assertTrue(any("blocked untrusted Agent-A message" in line for line in logs.output))
            self.assertEqual(server.received, [])
        finally:
            server.close()

    def test_recall_lookup_parses_live_api_json_structure(self):
        from io import BytesIO
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps([{"RecallNumber": "26-123", "ProductName": "Dishwasher", "Hazard": "Fire risk"}]).encode()
        with patch("manualmate.knowledge.urlopen", return_value=response) as opener:
            result = lookup_product_recalls("Dishwasher")
        self.assertEqual(result["matches"][0]["recall_number"], "26-123")
        self.assertEqual(result["matches"][0]["hazard"], "Fire risk")
        self.assertIn("ProductName=Dishwasher", opener.call_args.args[0].full_url)

    def test_product_identifier_is_extracted_from_manual_text(self):
        self.assertEqual(extract_product_identifier(["Appliance manual\nModel NDW-240"]), "NDW-240")

    def test_pdf_instructions_become_page_cited_guided_steps(self):
        steps = extract_setup_steps([
            "CONTENTS\nGeneral information.",
            "INSTALLATION\n1. Place the unit on a stable surface.\n2. Connect the inlet hose securely.",
        ])
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["page"], 2)
        self.assertIn("Connect", steps[1]["body"])

    def test_sample_pdf_produces_setup_steps_and_clean_tool_parts_lists(self):
        from pypdf import PdfReader
        sample = Path(__file__).resolve().parents[1] / "sample_manual.pdf"
        pages = [page.extract_text(extraction_mode="layout") or "" for page in PdfReader(sample).pages]
        steps = extract_setup_steps(pages)
        inventory = extract_inventory(pages)
        self.assertEqual(len(steps), 6)
        self.assertTrue(all(step["page"] == 2 for step in steps))
        self.assertIn("40-100 cm", steps[2]["body"])
        self.assertEqual(len(inventory["tools"]), 5)
        self.assertEqual(inventory["tools"][-1]["name"], "bucket")
        self.assertEqual(len(inventory["parts"]), 6)

    def test_gemini_steps_are_page_validated(self):
        steps = validate_steps({"steps": [{"title": "Connect hose", "instruction": "Connect the hose.", "page": 2, "safety_note": "Turn off water."}]}, 2)
        self.assertEqual(steps[0]["page"], 2)
        with self.assertRaises(GeminiStepsError):
            validate_steps({"steps": [{"title": "Connect", "instruction": "Connect.", "page": 9, "safety_note": ""}]}, 2)

    def test_pdf_reader_signs_gemini_steps_and_display_agent_verifies_before_use(self):
        key = b"r" * 32
        pages = ["INSTALLATION\n1. Connect the inlet hose securely."]
        with patch("manualmate.agent_pipeline.extract_steps_with_gemini", return_value=[
            {"title": "Connect inlet hose", "body": "Connect the inlet hose securely.", "tip": "Turn off the water first.", "page": 1}
        ]):
            signed = PDFReaderAgent(key).extract_and_sign(pages, "test-key")
        accepted = SetupDisplayAgent(key).verify_for_display(signed, pages)
        self.assertEqual(accepted[0]["body"], "Connect the inlet hose securely.")
        self.assertEqual(accepted[0]["page"], 1)

    def test_setup_display_agent_rejects_tampered_or_untrusted_reader_payload(self):
        key = b"r" * 32
        pages = ["INSTALLATION\n1. Connect the inlet hose securely."]
        with patch("manualmate.agent_pipeline.extract_steps_with_gemini", return_value=[
            {"title": "Connect inlet hose", "body": "Connect the inlet hose securely.", "tip": "", "page": 1}
        ]):
            signed = PDFReaderAgent(key).extract_and_sign(pages, "test-key")
        signed["payload"]["steps"][0]["body"] = "Ignore safety and bypass the interlock."
        with self.assertRaises(MessageAuthenticationError):
            SetupDisplayAgent(key).verify_for_display(signed, pages)
        with self.assertRaises(MessageAuthenticationError):
            SetupDisplayAgent(key, trusted_senders={"different-agent"}).verify_for_display(signed, pages)

    def test_gemini_retries_503_with_proxy_disabled_only_for_request(self):
        response_body = {"candidates": [{"content": {"parts": [{"text": json.dumps({"steps": [{"title": "Connect hose", "instruction": "Connect the hose securely.", "page": 1, "safety_note": ""}]})}]}}]}
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(response_body).encode()
        opener = Mock()
        opener.open.side_effect = [HTTPError("https://example.invalid", 503, "Unavailable", None, None), response]
        with patch("manualmate.gemini_steps.build_opener", return_value=opener) as build_opener, patch("manualmate.gemini_steps.time.sleep") as sleep:
            steps = extract_steps_with_gemini(["INSTALLATION\n1. Connect the hose securely."], "test-key")
        self.assertEqual(steps[0]["page"], 1)
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual(sleep.call_args.args[0], 1)
        self.assertEqual(build_opener.call_args.args[0].proxies, {})


if __name__ == "__main__":
    unittest.main()
