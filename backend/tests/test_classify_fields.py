"""Field / layout-only classification rules (R3a, R2, R6)."""

from app.pipeline.classify import _decide


def _ev(collector: str, result: str, *, tier=None, payload=None, weight=1.0):
    return {
        "collector_id": collector,
        "result": result,
        "tier": tier,
        "weight": weight,
        "payload": payload or {},
    }


def _field(**attrs):
    return {
        "id": 10, "ctype": "CustomField", "api_name": "Account.Dead__c",
        "namespace": None, "in_scope": True, "attrs": attrs or {},
    }


FIELD_RAN = {"C10_static_index", "C20_data_population"}


class TestLayoutOnly:
    def test_layout_only_no_data_is_unused(self):
        ev = [
            _ev("C10_static_index", "EVIDENCE_OF_USE", tier="A",
                payload={"layout_only": True, "layout_names": ["Account Layout"]}),
            _ev("C20_data_population", "NO_EVIDENCE_FOUND"),
        ]
        v = _decide(_field(), ev, [], [], FIELD_RAN)
        assert v.label == "UNUSED"
        assert v.rule_trace[-1]["rule"] == "R3a"
        assert "LAYOUT_ONLY_NO_DATA" in v.reason_codes[0]

    def test_layout_only_with_flag_is_needs_review(self):
        ev = [
            _ev("C10_static_index", "EVIDENCE_OF_USE", tier="A",
                payload={"layout_only": True, "layout_names": ["Account Layout"]}),
            _ev("C20_data_population", "NO_EVIDENCE_FOUND"),
        ]
        flags = [type("F", (), {"code": "DYNAMIC_APEX_IN_SCOPE", "detail": "taint"})()]
        v = _decide(_field(), ev, [], flags, FIELD_RAN)
        assert v.label == "NEEDS_REVIEW"
        assert v.rule_trace[-1]["rule"] == "R6"

    def test_layout_only_with_recent_flag_still_unused(self):
        ev = [
            _ev("C10_static_index", "EVIDENCE_OF_USE", tier="A",
                payload={"layout_only": True, "layout_names": ["Account Layout"]}),
            _ev("C20_data_population", "NO_EVIDENCE_FOUND"),
        ]
        flags = [type("F", (), {"code": "RECENTLY_CHANGED", "detail": "new"})()]
        v = _decide(_field(), ev, [], flags, FIELD_RAN)
        assert v.label == "UNUSED"
        assert v.rule_trace[-1]["rule"] == "R3a"

    def test_layout_only_plus_dependency_api_is_used(self):
        """Non-layout Tier-A must not be overridden by R3a."""
        ev = [
            _ev("C10_static_index", "EVIDENCE_OF_USE", tier="A",
                payload={"layout_only": True, "layout_names": ["Account Layout"]}),
            _ev("C30_dependency_api", "EVIDENCE_OF_USE", tier="A",
                payload={"dependencies": 1}),
            _ev("C20_data_population", "NO_EVIDENCE_FOUND"),
        ]
        v = _decide(_field(), ev, [], [], FIELD_RAN)
        assert v.label == "USED"
        assert v.rule_trace[-1]["rule"] == "R3"

    def test_layout_only_with_gaps_is_not_unused(self):
        """R3a requires a PASS completeness gate."""
        ev = [
            _ev("C10_static_index", "EVIDENCE_OF_USE", tier="A",
                payload={"layout_only": True, "layout_names": ["Account Layout"]}),
            _ev("C20_data_population", "NO_EVIDENCE_FOUND"),
        ]
        gaps = [type("G", (), {"collector_id": "C20_data_population",
                               "reason": "timeout"})()]
        v = _decide(_field(), ev, gaps, [], FIELD_RAN)
        # Gate FAIL but used evidence exists → skip R2 early path; R3a blocked
        # by gate; falls through to R3 USED (layout Tier-A still present).
        assert v.label == "USED"
        assert v.rule_trace[-1]["rule"] == "R3"


    def test_permission_only_no_data_is_unused(self):
        """FLS / permission-set presence alone is not business use."""
        ev = [
            _ev("C10_static_index", "INCONCLUSIVE", tier="C",
                payload={"weak_references": 2,
                         "weak_source_types": ["PermissionSet", "Profile"]}),
            _ev("C20_data_population", "NO_EVIDENCE_FOUND"),
        ]
        v = _decide(_field(), ev, [], [], FIELD_RAN)
        assert v.label == "UNUSED"
        assert v.rule_trace[-1]["rule"] == "R7a"
        assert "PRESENCE_ONLY_NO_DATA" in v.reason_codes[0]

    def test_permission_only_with_data_is_used_via_tier_b(self):
        """Record data is Tier B → R4 USED before weak rules."""
        ev = [
            _ev("C10_static_index", "INCONCLUSIVE", tier="C",
                payload={"weak_references": 1,
                         "weak_source_types": ["PermissionSet"]}),
            _ev("C20_data_population", "EVIDENCE_OF_USE", tier="B",
                payload={"populated": 10, "total": 100}),
        ]
        v = _decide(_field(), ev, [], [], FIELD_RAN)
        assert v.label == "USED"
        assert v.rule_trace[-1]["rule"] == "R4"

    def test_permission_only_with_review_flag_stays_review(self):
        ev = [
            _ev("C10_static_index", "INCONCLUSIVE", tier="C",
                payload={"weak_references": 1,
                         "weak_source_types": ["PermissionSet"]}),
            _ev("C20_data_population", "NO_EVIDENCE_FOUND"),
        ]
        flags = [type("F", (), {"code": "DYNAMIC_APEX_IN_SCOPE", "detail": "taint"})()]
        v = _decide(_field(), ev, [], flags, FIELD_RAN)
        assert v.label == "NEEDS_REVIEW"
        assert v.rule_trace[-1]["rule"] == "R6"


class TestCompleteness:
    def test_missing_required_collector_needs_review(self):
        ev = [_ev("C10_static_index", "NO_EVIDENCE_FOUND")]
        v = _decide(_field(), ev, [], [], {"C10_static_index"})
        assert v.label == "NEEDS_REVIEW"
        assert "INSUFFICIENT_EVIDENCE" in v.reason_codes[0]
        assert "C20_data_population" in v.reason_codes[0]
