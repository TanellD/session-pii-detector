import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from annotation_calculator import Calculator  # noqa: E402
from evaluate_session_alerts import evaluate, score  # noqa: E402
from prepare_session_corpus import (  # noqa: E402
    POLICY,
    POLICY_PATH,
    compile_corpus,
    compile_gold,
    digest,
)
from run_session_baselines import adapt_message, owner_from_text, replay  # noqa: E402
from session_alert_review import CORPUS_VERSION, GENERIC_PERSON  # noqa: E402


def gold(session_id, expected, *, uncertainty=None, status=None):
    return {
        "session_id": session_id,
        "status": status or ("positive" if expected is not None else "negative"),
        "t_gold": expected,
        "steps": [{"uncertainty_reason": uncertainty}]
        + [{"uncertainty_reason": None}] * 2,
    }


def prediction(session_id, index, status="complete"):
    return {"session_id": session_id, "status": status, "t_pred": index}


class SessionMetricTests(unittest.TestCase):
    def test_exact_early_late_missed_false_and_true_negative(self):
        cases = [
            ("exact", 2, 2),
            ("early", 2, 1),
            ("late", 2, 3),
            ("missed", 2, None),
            ("false_alert", None, 1),
            ("clean", None, None),
        ]
        report = score(
            [gold(i, g) for i, g, _ in cases], [prediction(i, p) for i, _, p in cases]
        )
        self.assertEqual(
            report["f2_session"]["counts"], {"TP": 3, "FP": 1, "FN": 1, "TN": 1}
        )
        self.assertEqual(
            report["f2_timely"]["counts"], {"TP": 1, "FP": 2, "FN": 2, "TN": 1}
        )
        self.assertEqual(report["f2_session"]["value"], 15 / 20)
        self.assertEqual(report["f2_timely"]["value"], 5 / 15)
        self.assertEqual(
            report["offset"]["signed_distribution"], {"-1": 1, "0": 1, "1": 1}
        )
        self.assertEqual(report["offset"]["exact_rate"]["value"], 1 / 3)
        self.assertEqual(report["offset"]["p95_absolute"], 1)
        self.assertEqual(report["fp0_all_negative"]["value"], 0.5)

    def test_uncertainty_is_not_an_exclusion_and_fp0_scopes_are_explicit(self):
        cases = [
            gold("flagged_positive", 2, uncertainty="insufficient_statistics"),
            gold("flagged_negative", None, uncertainty="specification_gap"),
            gold("certain_negative", None),
            {
                **gold("unknown", None, status="unknown"),
                "unknown_reason": "not_yet_adjudicated",
            },
            gold("failed", None),
            gold("missing", None),
        ]
        predictions = [
            prediction("flagged_positive", 2),
            prediction("flagged_negative", 1),
            prediction("certain_negative", None),
            prediction("unknown", None),
            prediction("failed", None, status="error"),
        ]
        result = score(cases, predictions)
        self.assertEqual(result["coverage"]["value"], 0.5)
        self.assertEqual(result["fp0_all_negative"]["value"], 0.5)
        self.assertEqual(result["fp0_certain_negative"]["value"], 0)
        self.assertEqual(result["uncertainty"]["gold_sessions"]["numerator"], 2)
        self.assertEqual(
            result["exclusions"],
            {
                "unresolved_gold": 1,
                "incomplete_prediction": 1,
                "missing_prediction": 1,
            },
        )
        self.assertEqual(result["outcomes"][3]["reason"], "not_yet_adjudicated")

    def test_zero_denominators_and_invalid_positions(self):
        result = score([gold("positive", 2)], [prediction("positive", None)])
        self.assertEqual(result["f2_session"]["value"], 0)
        self.assertIsNone(result["fp0_all_negative"]["value"])
        self.assertIsNone(result["offset"]["p95_absolute"])
        self.assertEqual(result["offset"]["na_reason"], "no_session_true_positives")
        self.assertIsNone(score([], [])["f2_timely"]["value"])
        result = score([gold("positive", 2)], [prediction("positive", True)])
        self.assertEqual(result["exclusions"], {"invalid_prediction": 1})

    def test_reports_invalid_uncertainty_records_without_scoring_them(self):
        result = score(
            [
                {**gold("invalid_gold", None), "steps": [{}]},
                gold("invalid_prediction", None),
                gold("complete", None),
            ],
            [
                prediction("invalid_gold", None),
                {**prediction("invalid_prediction", None), "uncertain_steps": [{}]},
                prediction("complete", None),
            ],
        )
        self.assertEqual(
            result["exclusions"], {"invalid_gold": 1, "invalid_prediction": 1}
        )
        self.assertEqual(result["coverage"]["numerator"], 1)

    def test_rejects_empty_experiments_and_unrecognized_splits(self):
        provenance = {
            "sessions_sha256": "synthetic-test",
            "policy": POLICY["version"],
            "policy_sha256": digest(POLICY_PATH),
        }
        reference = {**provenance, "sessions": [gold("example", None)]}
        for modes in ({}, {"regex": {}}, {"regex": {"per_person": []}}):
            with self.subTest(modes=modes), self.assertRaises(ValueError):
                evaluate(reference, {**provenance, "modes": modes})
        with self.assertRaisesRegex(ValueError, "Unrecognized split"):
            evaluate(
                reference,
                {
                    **provenance,
                    "modes": {"regex": {"per_person": [prediction("example", None)]}},
                    "session_splits": {"example": "tset"},
                },
            )


class CorpusAndReplayTests(unittest.TestCase):
    def document(self, owner):
        messages = [
            [
                "Мой телефон +7 (000) 000-00-09.",
                ["phone", "+7 (000) 000-00-09", "speaker"],
            ],
            [
                "Запишите почту review@example.invalid.",
                ["email", "review@example.invalid", owner],
            ],
        ]
        plan = {
            "scenarios": [
                {
                    "id": "two-turns",
                    "split": "development",
                    "family": "contacts",
                    "profiles": ["speaker"],
                    "steps": [["speaker:phone"], [f"{owner or 'unknown'}:email"]],
                }
            ]
        }
        documents, _ = compile_corpus(plan, [{"id": "two-turns", "messages": messages}])
        return documents[0]

    def test_three_replays_differ_and_unlinked_is_not_a_common_owner(self):
        calculator = Calculator(policy_only=True)
        same = self.document("speaker")
        other = self.document("other")
        unknown = self.document(None)
        self.assertIsNone(replay(same, "message_only", calculator)["t_pred"])
        self.assertEqual(replay(same, "per_person", calculator)["t_pred"], 2)
        self.assertIsNone(replay(other, "per_person", calculator)["t_pred"])
        self.assertEqual(replay(other, "pooled_known_owners", calculator)["t_pred"], 2)
        self.assertIsNone(replay(unknown, "pooled_known_owners", calculator)["t_pred"])
        self.assertEqual(other["entities"][1]["person_id"], "other")

    def test_ledger_rejects_using_future_evidence(self):
        with self.assertRaisesRegex(ValueError, "future evidence"):
            compile_gold(
                [self.document("speaker")],
                [
                    {
                        "session_id": "two-turns",
                        "steps": [
                            [
                                "combination_rule",
                                ["m1-e1", "m2-e1"],
                                None,
                                "Claim made before email",
                            ],
                            [
                                "combination_rule",
                                ["m1-e1", "m2-e1"],
                                None,
                                "Pair present",
                            ],
                        ],
                    }
                ],
                Calculator(policy_only=True),
            )

    def test_ledger_rejects_insufficient_evidence_for_a_correct_alert(self):
        with self.assertRaisesRegex(ValueError, "Cited spans do not support"):
            compile_gold(
                [self.document("speaker")],
                [
                    {
                        "session_id": "two-turns",
                        "steps": [
                            [None, [], None, "One contact"],
                            [
                                "combination_rule",
                                ["m1-e1"],
                                None,
                                "Cites only the phone",
                            ],
                        ],
                    }
                ],
                Calculator(policy_only=True),
            )

    def test_format_preserves_shared_owner_outside_annotation(self):
        plan = {
            "scenarios": [
                {
                    "id": "shared",
                    "split": "test",
                    "family": "shared-contact",
                    "profiles": ["a", "b"],
                    "steps": [["a:first_name", "b:first_name"], ["shared:email"]],
                }
            ]
        }
        authored = [
            {
                "id": "shared",
                "messages": [
                    [
                        "Я Образимир, он Макетимир.",
                        ["first_name", "Образимир", "a"],
                        ["first_name", "Макетимир", "b"],
                    ],
                    [
                        "Общая почта mixed@example.invalid.",
                        ["email", "mixed@example.invalid", ["a", "b"]],
                    ],
                ],
            }
        ]
        corpus, manifest = compile_corpus(plan, authored)
        self.assertIsNone(corpus[0]["entities"][-1]["person_id"])
        self.assertEqual(manifest[0]["shared_owners"], {"m2-e1": ["a", "b"]})
        self.assertEqual(manifest[0]["message_roles"], {"m1": "user", "m2": "user"})
        reviewed_corpus, reviewed_manifest = compile_corpus(
            {**plan, "version": CORPUS_VERSION}, authored
        )
        self.assertEqual(reviewed_corpus[0]["entities"][-1]["person_id"], ["a", "b"])
        self.assertNotIn("shared_owners", reviewed_manifest[0])
        calculator = Calculator(policy_only=True)
        for strategy in ("message_only", "pooled_known_owners", "per_person"):
            result = replay(reviewed_corpus[0], strategy, calculator, reviewed=True)
            self.assertIsNone(result["t_pred"])
        plan["scenarios"].append(
            {**plan["scenarios"][0], "id": "duplicate", "split": "development"}
        )
        authored.append({**authored[0], "id": "duplicate"})
        with self.assertRaisesRegex(ValueError, "crosses splits"):
            compile_corpus(plan, authored)

    def test_ner_adapter_rejects_generic_person_and_preserves_strong_document(self):
        document = self.document("speaker")
        message = {"id": "m1", "text": "Чужой ИНН: 000000000021."}
        start = message["text"].index("000")
        raw = [
            {
                "type": "INN",
                "start": start,
                "end": start + 12,
                "source": "regex",
                "score": None,
            },
            {
                "type": "PHONE",
                "start": start,
                "end": start + 12,
                "source": "gliner",
                "score": 0.9,
            },
            {"type": "PERSON", "start": 0, "end": 5, "source": "gliner", "score": 0.8},
        ]
        accepted, discarded = adapt_message(
            document, message, raw, {"INN": "individual_inn", "PHONE": "phone"}
        )
        self.assertEqual([e["type"] for e in accepted], ["individual_inn"])
        self.assertEqual(discarded, {"incomplete_phone": 1, "unmapped:PERSON": 1})
        self.assertIsNone(owner_from_text("Это не мой телефон: ", "phone", 20))

    def test_explicit_self_cue_does_not_cross_clauses(self):
        contact = (
            "Я оставлю мой номер завтра. Мой телефон есть в анкете, телефон клиента: "
        )
        self.assertIsNone(owner_from_text(contact, "phone", len(contact)))
        prefix = "Я спросил, кто заполнил заявку. Я вижу, что заявитель — "
        self.assertIsNone(owner_from_text(prefix, "sex", len(prefix)))
        prefix = "Проверьте раздел анкеты: я "
        self.assertEqual(owner_from_text(prefix, "sex", len(prefix)), "speaker")

    def test_review_checksum_keeps_span_and_changes_first_alert(self):
        document = self.document("speaker")
        invalid, valid = "000-000-000 16", "000-000-001 01"
        messages = [
            {"id": f"m{index}", "text": f"СНИЛС: {value}."}
            for index, value in enumerate((invalid, valid), 1)
        ]
        mapping = {"SNILS": "snils"}
        from benchmarks.pii_smoke.regex_detector import predict

        entities = []
        for message in messages:
            raw = predict(message["text"], require_valid_checksum=False)
            spans, discarded = adapt_message(
                document, message, raw, mapping, reviewed=True
            )
            self.assertEqual(len(spans), 1)
            self.assertEqual(discarded, {})
            entities.extend(spans)
        document = {**document, "messages": messages, "entities": entities}
        calculator = Calculator(policy_only=True)
        self.assertEqual(replay(document, "per_person", calculator)["t_pred"], 1)
        checked = replay(document, "per_person", calculator, reviewed=True)
        self.assertEqual((checked["t_pred"], checked["rule_id"]), (2, "red_rule"))

    def test_review_person_retains_generic_span_and_discards_overlap(self):
        document = self.document("speaker")
        text = "Контакт Листослав Сумер, почта listoslav@example.invalid."
        name, email = "Листослав Сумер", "listoslav@example.invalid"
        message = {"id": "m1", "text": text}
        raw = [
            {
                "type": kind,
                "start": text.index(span),
                "end": text.index(span) + len(span),
                "score": 0.9,
                "source": source,
            }
            for kind, span, source in (
                ("PERSON", name, "gliner"),
                ("PERSON", "listoslav", "gliner"),
                ("EMAIL", email, "regex"),
            )
        ]
        accepted, discarded = adapt_message(
            document,
            message,
            raw,
            {"PERSON": GENERIC_PERSON, "EMAIL": "email"},
            reviewed=True,
        )
        self.assertEqual(
            [entity["type"] for entity in accepted], [GENERIC_PERSON, "email"]
        )
        self.assertEqual(discarded, {"overlap": 1})

    def test_review_rejects_partial_model_phone_inside_long_number(self):
        document = self.document("speaker")
        value = "+7 (000) 001-23-45"
        message = {"id": "m1", "text": f"{value}6"}
        raw = [
            {
                "type": "PHONE",
                "start": 0,
                "end": len(value),
                "score": 0.9,
                "source": "gliner",
            }
        ]
        accepted, discarded = adapt_message(
            document, message, raw, {"PHONE": "phone"}, reviewed=True
        )
        self.assertEqual(accepted, [])
        self.assertEqual(discarded, {"incomplete_phone": 1})


if __name__ == "__main__":
    unittest.main()
