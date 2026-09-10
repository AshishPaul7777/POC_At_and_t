"""LWC / Aura classification policy + field R3a regression."""

from app.pipeline.aliases import ui_bundle_aliases
from app.pipeline.classify import _decide
from app.pipeline.indexer import _ui_reference_literals
from app.pipeline.inventory import _parse_aura_cmp, _parse_lwc_meta


def _ev(collector: str, result: str, *, tier=None, payload=None, weight=1.0):
    return {
        "collector_id": collector,
        "result": result,
        "tier": tier,
        "weight": weight,
        "payload": payload or {},
    }


def _c70_unreachable():
    return _ev("C70_reachability", "NO_EVIDENCE_FOUND",
               payload={"reachable": False})


def _c70_self_entry():
    return _ev(
        "C70_reachability", "NO_EVIDENCE_FOUND",
        payload={
            "reachable": True,
            "self_entry_point": True,
            "hops_from_entry_point": 0,
        },
    )


UI_RAN = {"C10_static_index", "C70_reachability"}
FIELD_RAN = {"C10_static_index", "C20_data_population"}


def _lwc(**attrs):
    return {
        "id": 50, "ctype": "LightningComponentBundle", "api_name": "accountCard",
        "namespace": None, "in_scope": True, "attrs": attrs or {},
    }


def _field(**attrs):
    return {
        "id": 10, "ctype": "CustomField", "api_name": "Account.Dead__c",
        "namespace": None, "in_scope": True, "attrs": attrs or {},
    }


class TestUiAliases:
    def test_bundle_forms(self):
        aliases = {a.alias_lc: a.kind for a in ui_bundle_aliases(api_name="accountCard")}
        assert "accountcard" in aliases
        assert "c/accountcard" in aliases
        assert "c:accountcard" in aliases
        assert "c-account-card" in aliases


class TestUiMetaParse:
    def test_exposed_lwc_meta(self, tmp_path):
        meta = tmp_path / "accountCard.js-meta.xml"
        meta.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
            <LightningComponentBundle xmlns="http://soap.sforce.com/2006/04/metadata">
              <apiVersion>59.0</apiVersion>
              <isExposed>true</isExposed>
              <masterLabel>Account Card</masterLabel>
              <targets>
                <target>lightning__RecordPage</target>
              </targets>
            </LightningComponentBundle>
            """,
            encoding="utf-8",
        )
        attrs, ns = _parse_lwc_meta(meta, "accountCard")
        assert ns is False
        assert attrs["is_exposed"] is True
        assert attrs["is_entry_point"] is True
        assert "lightning__RecordPage" in attrs["targets"]
        assert attrs["master_label"] == "Account Card"

    def test_private_lwc_meta(self, tmp_path):
        meta = tmp_path / "helper.js-meta.xml"
        meta.write_text(
            """<?xml version="1.0"?>
            <LightningComponentBundle xmlns="http://soap.sforce.com/2006/04/metadata">
              <isExposed>false</isExposed>
            </LightningComponentBundle>
            """,
            encoding="utf-8",
        )
        attrs, _ = _parse_lwc_meta(meta, "helper")
        assert attrs["is_exposed"] is False
        assert attrs["is_entry_point"] is False

    def test_aura_global(self, tmp_path):
        cmp = tmp_path / "MyWidget.cmp"
        cmp.write_text(
            '<aura:component access="global" implements="flexipage:availableForAllPageTypes">\n'
            "</aura:component>\n",
            encoding="utf-8",
        )
        attrs, ns = _parse_aura_cmp(cmp, "MyWidget")
        assert ns is False
        assert attrs["access"] == "global"
        assert attrs["is_exposed"] is True


class TestUiReferenceExtract:
    def test_flexipage_and_import(self):
        body = """
        <componentName>c:accountCard</componentName>
        import x from 'c/childHelper';
        <c-account-card></c-account-card>
        <aura:dependency resource="markup://c:OtherCmp"/>
        """
        lit = _ui_reference_literals(body)
        assert "c:accountcard" in lit
        assert "c/childhelper" in lit
        assert "c-account-card" in lit
        assert "c:othercmp" in lit


class TestUiVerdicts:
    def test_flexipage_ref_is_used(self):
        ev = [
            _ev("C10_static_index", "EVIDENCE_OF_USE", tier="A",
                payload={"binding_references": 1, "layout_only": False,
                         "binding_source_types": ["FlexiPage"]}),
            _c70_unreachable(),
        ]
        v = _decide(_lwc(is_exposed=True, is_entry_point=True), ev, [], [], UI_RAN)
        assert v.label == "USED"
        assert v.rule_trace[-1]["rule"] == "R3"

    def test_exposed_no_refs_needs_review(self):
        ev = [
            _ev("C10_static_index", "NO_EVIDENCE_FOUND"),
            _c70_self_entry(),
        ]
        v = _decide(_lwc(is_exposed=True, is_entry_point=True), ev, [], [], UI_RAN)
        assert v.label == "NEEDS_REVIEW"
        assert v.rule_trace[-1]["rule"] == "R5"
        assert "EXPOSED_UI" in v.reason_codes[0]

    def test_private_unreachable_is_unused(self):
        ev = [
            _ev("C10_static_index", "NO_EVIDENCE_FOUND"),
            _c70_unreachable(),
        ]
        v = _decide(_lwc(is_exposed=False, is_entry_point=False), ev, [], [], UI_RAN)
        assert v.label == "UNUSED"
        assert v.rule_trace[-1]["rule"] == "R5b"


class TestFieldR3aRegression:
    """UI FlexiPage-as-use must not break field layout-only UNUSED."""

    def test_layout_only_field_still_unused(self):
        ev = [
            _ev("C10_static_index", "EVIDENCE_OF_USE", tier="A",
                payload={"layout_only": True, "layout_names": ["Account Layout"]}),
            _ev("C20_data_population", "NO_EVIDENCE_FOUND"),
        ]
        v = _decide(_field(), ev, [], [], FIELD_RAN)
        assert v.label == "UNUSED"
        assert v.rule_trace[-1]["rule"] == "R3a"
