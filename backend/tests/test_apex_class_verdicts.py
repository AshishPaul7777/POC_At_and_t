"""Apex class vs method used/unused — RestResource and parent promotion.

A method USED must force its parent class USED. An API-exposed class/method
must never be UNUSED solely because nothing inside the org calls it.
"""

from app.pipeline.classify import Verdict, _decide, _promote_classes_with_used_methods
from app.pipeline.graph import Graph, Node, compute_reachability
from app.pipeline.inventory import _is_class_entry_point, _is_entry_point


def _ev(collector: str, result: str, *, tier=None, payload=None, weight=1.0):
    return {
        "collector_id": collector,
        "result": result,
        "tier": tier,
        "weight": weight,
        "payload": payload or {},
    }


def _c70_self_entry():
    return _ev(
        "C70_reachability", "NO_EVIDENCE_FOUND",
        payload={
            "reachable": True,
            "self_entry_point": True,
            "hops_from_entry_point": 0,
        },
    )


def _c70_unreachable():
    return _ev(
        "C70_reachability", "NO_EVIDENCE_FOUND",
        payload={"reachable": False},
    )


def _c70_reached(*, hops=1):
    return _ev(
        "C70_reachability", "EVIDENCE_OF_USE", tier="A",
        payload={"reachable": True, "hops_from_entry_point": hops, "path": ["Trigger", "Cls"]},
    )


class TestClassEntryPointInventory:
    def test_restresource_on_class_is_entry_point(self):
        st = {"interfaces": [], "methods": []}
        assert _is_class_entry_point(st, ["RestResource"], []) is True

    def test_httpget_method_makes_class_entry_point(self):
        methods = [{
            "name": "getAccounts",
            "modifiers": ["global", "static"],
            "annotations": [{"name": "HttpGet"}],
        }]
        assert _is_class_entry_point({"interfaces": []}, [], methods) is True

    def test_system_schedulable_makes_class_entry_point(self):
        """SymbolTable uses System.Schedulable, not the bare interface name."""
        st = {"interfaces": ["System.Schedulable"], "methods": []}
        assert _is_class_entry_point(st, [], []) is True

    def test_classify_kinds_from_existing_attrs(self):
        from app.pipeline.apex_kind import classify_apex_class_kind

        assert classify_apex_class_kind(
            {"is_test": False, "interfaces": [], "annotations": [], "is_entry_point": True},
            [{"annotations": ["HttpGet"], "modifiers": ["global", "static"]}],
        ) == "rest"
        assert classify_apex_class_kind(
            {"is_test": False, "interfaces": ["System.Schedulable"],
             "annotations": [], "is_entry_point": False},
            [],
        ) == "scheduled"
        assert classify_apex_class_kind(
            {"is_test": False, "interfaces": ["Database.Batchable", "Database.Stateful"],
             "annotations": [], "is_entry_point": True},
            [],
        ) == "batch"
        assert classify_apex_class_kind(
            {"is_test": True, "interfaces": [], "annotations": [], "is_entry_point": True},
            [{"annotations": ["IsTest"]}],
        ) == "test"

        from app.pipeline.apex_kind import list_apex_class_kinds
        assert list_apex_class_kinds(
            {"is_test": False,
             "interfaces": ["Database.Batchable", "System.Schedulable"],
             "annotations": [], "is_entry_point": True},
            [],
        ) == ["scheduled", "batch"]

    def test_plain_helper_class_is_not_entry_point(self):
        methods = [{
            "name": "format",
            "modifiers": ["public", "static"],
            "annotations": [],
        }]
        assert _is_class_entry_point({"interfaces": []}, [], methods) is False

    def test_httpget_method_flag(self):
        assert _is_entry_point(
            ["global", "static"], ["HttpGet"], {"interfaces": []}, "getAccounts"
        ) is True

    def test_bare_global_helper_is_not_entry_point(self):
        """Visibility alone must not mark a helper as an API surface."""
        assert _is_entry_point(
            ["global", "static"], [], {"interfaces": []}, "formatName"
        ) is False

    def test_bare_global_method_does_not_make_class_entry(self):
        methods = [{
            "name": "formatName",
            "modifiers": ["global", "static"],
            "annotations": [],
        }]
        assert _is_class_entry_point({"interfaces": []}, [], methods) is False

    def test_webservice_still_entry_point(self):
        assert _is_entry_point(
            ["global", "webservice", "static"], [], {"interfaces": []}, "doSoap"
        ) is True


class TestReachabilityOwnership:
    def test_declares_does_not_mark_methods_used(self):
        """Class entry root must not auto-USED every method via declares."""
        g = Graph()
        g.nodes["component:1"] = Node(
            "component:1", "component", "AccountRESTService", "ApexClass",
            component_id=1, is_entry_point=True, entry_reason="REST",
        )
        g.nodes["artifact:10"] = Node(
            "artifact:10", "artifact", "AccountRESTService", "ApexClass",
            artifact_id=10,
        )
        g.nodes["component:2"] = Node(
            "component:2", "component", "AccountRESTService.getAccounts",
            "ApexMethod", component_id=2, is_entry_point=True,
            entry_reason="HttpGet",
        )
        g.nodes["component:3"] = Node(
            "component:3", "component", "AccountRESTService.helper",
            "ApexMethod", component_id=3,
        )
        g.add_edge("component:1", "artifact:10", kind="implements")
        g.add_edge("artifact:10", "component:2", kind="declares")
        g.add_edge("artifact:10", "component:3", kind="declares")
        g.add_edge("component:2", "component:1", kind="member_of")
        g.add_edge("component:3", "component:1", kind="member_of")

        compute_reachability(g)

        assert g.nodes["component:1"].reachable and g.nodes["component:1"].distance == 0
        assert g.nodes["component:2"].reachable and g.nodes["component:2"].distance == 0
        # helper is not an entry point and declares is not traversed
        assert not g.nodes["component:3"].reachable

    def test_member_of_at_distance_zero_does_not_reach_class(self):
        """Uncalled entry method must not invent reachability on its class."""
        g = Graph()
        g.nodes["component:1"] = Node(
            "component:1", "component", "Helper", "ApexClass",
            component_id=1,  # not an entry class
        )
        g.nodes["component:2"] = Node(
            "component:2", "component", "Helper.doWork", "ApexMethod",
            component_id=2, is_entry_point=True, entry_reason="AuraEnabled",
        )
        g.add_edge("component:2", "component:1", kind="member_of")

        compute_reachability(g)

        assert g.nodes["component:2"].distance == 0
        assert not g.nodes["component:1"].reachable

    def test_member_of_promotes_class_when_method_reached_from_elsewhere(self):
        g = Graph()
        g.nodes["artifact:99"] = Node(
            "artifact:99", "artifact", "SomeTrigger", "ApexTrigger",
            artifact_id=99, is_entry_point=True, entry_reason="trigger",
        )
        g.nodes["component:2"] = Node(
            "component:2", "component", "Svc.run", "ApexMethod",
            component_id=2,
        )
        g.nodes["component:1"] = Node(
            "component:1", "component", "Svc", "ApexClass",
            component_id=1,
        )
        g.add_edge("artifact:99", "component:2")  # real reference
        g.add_edge("component:2", "component:1", kind="member_of")

        compute_reachability(g)

        assert g.nodes["component:2"].reachable
        assert g.nodes["component:2"].distance == 1
        assert g.nodes["component:1"].reachable
        assert g.nodes["component:1"].distance == 2


class TestClassifyRestAndPromotion:
    APEX_RAN = {"C10_static_index", "C40_runtime", "C70_reachability"}
    METHOD_RAN = {"C10_static_index", "C70_reachability"}

    def test_rest_class_and_http_method_are_needs_review_not_unused(self):
        cls = {
            "id": 1, "ctype": "ApexClass", "api_name": "AccountRESTService",
            "namespace": None, "in_scope": True, "attrs": {"is_entry_point": True},
        }
        method = {
            "id": 2, "ctype": "ApexMethod",
            "api_name": "AccountRESTService.getAccounts",
            "namespace": None, "in_scope": True,
            "attrs": {"is_entry_point": True}, "parent_id": 1,
        }
        c10 = _ev("C10_static_index", "NO_EVIDENCE_FOUND")
        c40 = _ev("C40_runtime", "NO_EVIDENCE_FOUND")

        cv = _decide(cls, [c10, c40, _c70_self_entry()], [], [], self.APEX_RAN)
        mv = _decide(method, [c10, _c70_self_entry()], [], [], self.METHOD_RAN)

        assert cv.label == "NEEDS_REVIEW"
        assert "UNCALLED_ENTRY_POINT" in cv.reason_codes[0]
        assert mv.label == "NEEDS_REVIEW"
        assert "UNCALLED_ENTRY_POINT" in mv.reason_codes[0]

    def test_used_method_promotes_parent_class_to_used(self):
        comps = [
            {"id": 1, "ctype": "ApexClass", "parent_id": None},
            {"id": 2, "ctype": "ApexMethod", "parent_id": 1},
            {"id": 3, "ctype": "ApexMethod", "parent_id": 1},
        ]
        verdicts = [
            Verdict(1, "UNUSED", 70, reason_codes=["UNREACHABLE"]),
            Verdict(2, "USED", 85, reason_codes=["BINDING_REFERENCE"]),
            Verdict(3, "UNUSED", 70, reason_codes=["UNREACHABLE"]),
        ]
        out = _promote_classes_with_used_methods(comps, verdicts)
        by_id = {v.component_id: v for v in out}
        assert by_id[1].label == "USED"
        assert by_id[1].reason_codes == ["HAS_USED_METHOD"]
        assert by_id[3].label == "UNUSED"  # unused method stays unused

    def test_unused_methods_do_not_force_class_unused(self):
        """Class already USED stays USED when sibling methods are unused."""
        comps = [
            {"id": 1, "ctype": "ApexClass", "parent_id": None},
            {"id": 2, "ctype": "ApexMethod", "parent_id": 1},
        ]
        verdicts = [
            Verdict(1, "USED", 90, reason_codes=["BINDING_REFERENCE"]),
            Verdict(2, "UNUSED", 70, reason_codes=["UNREACHABLE"]),
        ]
        out = _promote_classes_with_used_methods(comps, verdicts)
        assert out[0].label == "USED"
        assert out[1].label == "UNUSED"

    def test_plain_unreachable_class_still_unused(self):
        cls = {
            "id": 1, "ctype": "ApexClass", "api_name": "DeadHelper",
            "namespace": None, "in_scope": True,
            "attrs": {"is_entry_point": False},
        }
        ev = [
            _ev("C10_static_index", "NO_EVIDENCE_FOUND"),
            _ev("C40_runtime", "NO_EVIDENCE_FOUND"),
            _c70_unreachable(),
        ]
        v = _decide(cls, ev, [], [], self.APEX_RAN)
        assert v.label == "UNUSED"
        assert "UNREACHABLE" in v.reason_codes[0]

    def test_missing_c70_blocks_apex_unused(self):
        """Without reachability, Apex must not be called UNUSED via R9."""
        cls = {
            "id": 1, "ctype": "ApexClass", "api_name": "DeadHelper",
            "namespace": None, "in_scope": True,
            "attrs": {"is_entry_point": False},
        }
        ev = [
            _ev("C10_static_index", "NO_EVIDENCE_FOUND"),
            _ev("C40_runtime", "NO_EVIDENCE_FOUND"),
        ]
        v = _decide(cls, ev, [], [], {"C10_static_index", "C40_runtime"})
        assert v.label == "NEEDS_REVIEW"
        assert "INSUFFICIENT_EVIDENCE" in v.reason_codes[0]
        assert "C70_reachability" in v.reason_codes[0]

    def test_uncertainty_flags_block_unreachable_unused(self):
        """R6 must beat R5b: flags suppress UNUSED even when unreachable."""
        cls = {
            "id": 1, "ctype": "ApexClass", "api_name": "DynamicScheduledJobInvoker",
            "namespace": None, "in_scope": True,
            "attrs": {"is_entry_point": False},
        }
        ev = [
            _ev("C10_static_index", "NO_EVIDENCE_FOUND"),
            _ev("C40_runtime", "NO_EVIDENCE_FOUND"),
            _c70_unreachable(),
        ]
        flags = [
            type("F", (), {"code": "DYNAMIC_APEX_IN_SCOPE",
                           "detail": "blast radius"})(),
        ]
        v = _decide(cls, ev, [], flags, self.APEX_RAN)
        assert v.label == "NEEDS_REVIEW"
        assert v.rule_trace[-1]["rule"] == "R6"
        assert "DYNAMIC_APEX_IN_SCOPE" in v.reason_codes[0]

    def test_recently_changed_alone_does_not_block_unused(self):
        """RECENTLY_CHANGED is informational — still allows UNUSED via R5b."""
        cls = {
            "id": 1, "ctype": "ApexClass", "api_name": "DeadHelper",
            "namespace": None, "in_scope": True,
            "attrs": {"is_entry_point": False},
        }
        ev = [
            _ev("C10_static_index", "NO_EVIDENCE_FOUND"),
            _ev("C40_runtime", "NO_EVIDENCE_FOUND"),
            _c70_unreachable(),
        ]
        flags = [
            type("F", (), {"code": "RECENTLY_CHANGED", "detail": "new"})(),
        ]
        v = _decide(cls, ev, [], flags, self.APEX_RAN)
        assert v.label == "UNUSED"
        assert "UNREACHABLE" in v.reason_codes[0]

    def test_test_class_unreachable_is_needs_review(self):
        cls = {
            "id": 1, "ctype": "ApexClass", "api_name": "FooTest",
            "namespace": None, "in_scope": True,
            "attrs": {"is_entry_point": False, "is_test": True},
        }
        ev = [
            _ev("C10_static_index", "NO_EVIDENCE_FOUND"),
            _ev("C40_runtime", "NO_EVIDENCE_FOUND"),
            _c70_unreachable(),
        ]
        v = _decide(cls, ev, [], [], self.APEX_RAN)
        assert v.label == "NEEDS_REVIEW"
        assert "TEST_ONLY" in v.reason_codes[0]

    def test_distance_zero_self_entry_is_not_tier_a_used(self):
        """C70 self-root payload must not fire R3 USED."""
        method = {
            "id": 2, "ctype": "ApexMethod",
            "api_name": "AccountRESTService.getAccounts",
            "namespace": None, "in_scope": True,
            "attrs": {"is_entry_point": True},
        }
        v = _decide(
            method,
            [_ev("C10_static_index", "NO_EVIDENCE_FOUND"), _c70_self_entry()],
            [], [], self.METHOD_RAN,
        )
        assert v.label != "USED"
        assert v.label == "NEEDS_REVIEW"
