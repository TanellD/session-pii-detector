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
from session_alert_review import (  # noqa: E402
    GENERIC_PERSON,
    REVIEW_POLICY,
    REVIEW_SCHEMA,
    review_steps,
    validate_review_document,
)


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
        reviewed_plan = {
            **plan,
            "version": "session-alerts-v2",
            "review_policy": REVIEW_POLICY,
        }
        reviewed_corpus, reviewed_manifest = compile_corpus(reviewed_plan, authored)
        renamed_corpus, renamed_manifest = compile_corpus(
            {**reviewed_plan, "version": "session-alerts-v3"}, authored
        )
        self.assertEqual(
            (renamed_corpus, renamed_manifest), (reviewed_corpus, reviewed_manifest)
        )
        self.assertEqual(reviewed_corpus[0]["schema_version"], REVIEW_SCHEMA)
        with self.assertRaisesRegex(ValueError, "Expected schema"):
            validate_review_document(
                {**reviewed_corpus[0], "schema_version": "unsupported-schema"}
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

    def test_review_checksum_filters_extraction_and_gold(self):
        from benchmarks.pii_smoke.regex_detector import predict
        document = self.document("speaker")
        calculator = Calculator(policy_only=True)
        for value, count in (("000-000-000 16", 0), ("000-000-001 01", 1)):
            text = "СНИЛС: " + value
            message = {"id": "m1", "text": text}
            spans, discarded = adapt_message(document, message,
                predict(text, require_valid_checksum=False), {"SNILS": "snils"}, reviewed=True)
            self.assertEqual(len(spans), count)
            if not count:
                self.assertEqual(discarded, {"invalid_checksum": 1})
            test_document = {**document, "schema_version": REVIEW_SCHEMA,
                             "messages": [message], "entities": spans}
            self.assertEqual(replay(test_document, "per_person", calculator, reviewed=True)["t_pred"], 1 if count else None)
            if not count:
                invalid = {**test_document, "entities": [{"id": "e1", "message_id": "m1",
                    "start": 7, "end": len(text), "type": "snils", "person_id": "p1"}]}
                with self.assertRaisesRegex(ValueError, "checksum"):
                    validate_review_document(invalid)

    def test_rejected_document_does_not_rewrite_accepted_history(self):
        document = self.document("speaker")
        text = "Мой СНИЛС: 000-000-001 01"
        message = {"id": "m1", "text": text}
        document = {**document, "schema_version": REVIEW_SCHEMA,
                    "messages": [message, {"id": "m2", "text": "Поправка: СНИЛС: 000-000-000 16"}],
                    "entities": [{"id": "e1", "message_id": "m1", "start": 11,
                                  "end": len(text), "type": "snils", "person_id": "speaker"}]}
        steps = review_steps(document, Calculator(policy_only=True))
        self.assertTrue(steps[0]["rule_alert"])
        self.assertTrue(steps[1]["rule_alert"])

    def test_review_ledger_requires_active_evidence_after_correction(self):
        plan = {
            "version": "evidence-correction-test",
            "review_policy": REVIEW_POLICY,
            "scenarios": [
                {
                    "id": "evidence-correction",
                    "split": "test",
                    "family": "checksum",
                    "profiles": ["speaker"],
                    "steps": [
                        ["speaker:snils"],
                        ["speaker:snils", "speaker:individual_inn"],
                    ],
                }
            ],
        }
        authored = [
            {
                "id": "evidence-correction",
                "messages": [
                    [
                        "Мой СНИЛС: 000-000-001 01.",
                        ["snils", "000-000-001 01", "speaker"],
                    ],
                    [
                        "Поправка. Мой СНИЛС: 000-000-002 02. ИНН: 000000012373.",
                        ["snils", "000-000-002 02", "speaker"],
                        ["individual_inn", "000000012373", "speaker"],
                    ],
                ],
            }
        ]
        documents, _ = compile_corpus(plan, authored)
        steps = [
            ["red_rule", ["m1-e1"], None, "Valid SNILS"],
            ["red_rule", ["m1-e1"], None, "Old SNILS cited after replacement"],
        ]
        ledger = [{"session_id": "evidence-correction", "steps": steps}]
        calculator = Calculator(policy_only=True)
        with self.assertRaisesRegex(
            ValueError, "superseded: evidence-correction/m2/m1-e1"
        ):
            compile_gold(documents, ledger, calculator, reviewed=True)

        steps[1] = ["red_rule", ["m2-e2"], None, "Valid INN"]
        rows, differences = compile_gold(documents, ledger, calculator, reviewed=True)
        self.assertEqual(differences, [])
        self.assertEqual(rows[0]["t_gold"], 1)
        self.assertEqual(rows[0]["steps"][1]["evidence_ids"], ["m2-e2"])

    def test_review_person_retains_generic_span_and_discards_overlap(self):
        document = {**self.document("speaker"), "schema_version": REVIEW_SCHEMA}
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
        document = {**self.document("speaker"), "schema_version": REVIEW_SCHEMA}
        value = "+7 (000) 001-23-45"
        raw = [
            {
                "type": "PHONE",
                "start": 0,
                "end": len(value),
                "score": 0.9,
                "source": "gliner",
            }
        ]
        for suffix in ("6", "-6", " 6"):
            with self.subTest(suffix=suffix):
                message = {"id": "m1", "text": f"{value}{suffix}"}
                accepted, discarded = adapt_message(
                    document, message, raw, {"PHONE": "phone"}, reviewed=True
                )
                self.assertEqual(accepted, [])
                self.assertEqual(discarded, {"incomplete_phone": 1})

    def test_review_regex_phone_suffix_cannot_trigger_contact_pair(self):
        from benchmarks.pii_smoke.regex_detector import predict

        document = {**self.document("speaker"), "schema_version": REVIEW_SCHEMA}
        mapping = {"PHONE": "phone", "EMAIL": "email"}
        for suffix, expected in (("-6.", None), (" 6.", None), (", код 6.", 1)):
            with self.subTest(suffix=suffix):
                message = {
                    "id": "m1",
                    "text": f"Мой телефон +7 (000) 001-23-45{suffix} Моя почта review@example.invalid.",
                }
                spans, _ = adapt_message(
                    document, message, predict(message["text"]), mapping, reviewed=True
                )
                result = replay(
                    {**document, "messages": [message], "entities": spans},
                    "per_person",
                    Calculator(policy_only=True),
                    reviewed=True,
                )
                self.assertEqual(result["t_pred"], expected)


if __name__ == "__main__":
    unittest.main()
