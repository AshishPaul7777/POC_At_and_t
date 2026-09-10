"""The code view must not resurrect the `Active__c` false positive.

A coloured line reads as more authoritative than a table row, so every guard the
indexer applies has to hold here too. These tests are written against the same
cases as `test_alias_sweep.py`, from the other end: that file asserts which
edges are created, this one asserts which characters get highlighted.
"""

import pytest

from app.pipeline.highlight import (
    build_allowed,
    line_index,
    locate,
    object_member_spans,
    to_segments,
)

# Component ids used throughout: 1 = Account.Active__c, 2 = Retail_Product__c.SKU__c
ACTIVE, SKU = 1, 2


def allowed_for_active():
    """`Active__c` as the alias table really holds it, suffix-stripped form and all."""
    return build_allowed([
        (ACTIVE, "active__c", "api_name"),
        (ACTIVE, "account.active__c", "qualified"),
        (ACTIVE, "active", "stripped_suffix"),   # must never match
        (ACTIVE, "active", "label"),             # must never match
    ])


def edges(*pairs):
    return set(pairs)


class TestNonReferenceKinds:
    def test_stripped_suffix_and_label_are_dropped_from_the_lookup(self):
        allowed = allowed_for_active()
        assert "active__c" in allowed
        # The bare word resolves to nothing at all, so no call site can match it.
        assert "active" not in allowed

    def test_prose_mention_of_active_is_not_highlighted(self):
        src = "// Retrieves active promoted records\npublic class Foo {}\n"
        hits = locate(src, ".cls", allowed_for_active(),
                      edges((ACTIVE, "comment_mention"), (ACTIVE, "token_sweep")))
        assert hits == []

    def test_status_active_in_metadata_is_not_highlighted(self):
        src = "<CustomObject><status>Active</status></CustomObject>"
        hits = locate(src, ".object", allowed_for_active(),
                      edges((ACTIVE, "token_sweep")))
        assert hits == []

    def test_the_real_api_name_is_highlighted(self):
        src = "public class Foo { void go() { a.Active__c = true; } }"
        hits = locate(src, ".cls", allowed_for_active(),
                      edges((ACTIVE, "token_sweep")))
        assert len(hits) == 1
        assert src[hits[0].start:hits[0].end] == "Active__c"

    def test_qualified_form_in_a_layout_is_highlighted(self):
        # The tokeniser does not span dots -- `_TOKEN_RE` has no `.` in it -- so
        # `Account.Active__c` yields `account` and `active__c`, and it is the
        # api_name half that carries the edge. Highlighting the whole qualified
        # string would be wider than what the indexer actually matched, so the
        # narrower span is the correct one.
        src = "<Layout><field>Account.Active__c</field></Layout>"
        hits = locate(src, ".layout", allowed_for_active(),
                      edges((ACTIVE, "token_sweep")))
        assert [src[h.start:h.end] for h in hits] == ["Active__c"]


class TestEdgeInheritance:
    """A position is only a highlight if the analysis recorded an edge for it."""

    def test_no_edge_means_no_highlight(self):
        src = "a.Active__c = true;"
        assert locate(src, ".cls", allowed_for_active(), edges()) == []

    def test_edge_for_a_different_match_kind_does_not_count(self):
        # The component is referenced from this file, but as a string literal.
        # The bare identifier occurrence must not borrow that edge.
        src = "a.Active__c = true;"
        hits = locate(src, ".cls", allowed_for_active(),
                      edges((ACTIVE, "string_literal")))
        assert hits == []


class TestLiterals:
    def test_quoted_field_name_is_highlighted_without_its_quotes(self):
        allowed = build_allowed([(SKU, "sku__c", "api_name")])
        src = "obj.get('SKU__c');"
        hits = locate(src, ".cls", allowed, edges((SKU, "string_literal")))
        assert len(hits) == 1
        assert src[hits[0].start:hits[0].end] == "SKU__c"

    def test_field_inside_dynamic_soql_is_highlighted_not_the_whole_query(self):
        allowed = build_allowed([(SKU, "sku__c", "api_name")])
        src = "Database.query('SELECT SKU__c FROM Retail_Product__c');"
        hits = locate(src, ".cls", allowed, edges((SKU, "string_literal")))
        assert [src[h.start:h.end] for h in hits] == ["SKU__c"]


class TestOverlaps:
    def test_report_column_form_wins_and_keeps_the_loser_as_also(self):
        # Customer_Order__c$Legacy_Code__c overlaps two plain token matches.
        allowed = build_allowed([
            (10, "customer_order__c$legacy_code__c", "report_qualified"),
            (11, "legacy_code__c", "api_name"),
        ])
        src = "<column>Customer_Order__c$Legacy_Code__c</column>"
        hits = locate(src, ".report", allowed,
                      edges((10, "token_sweep"), (11, "token_sweep")))
        assert len(hits) == 1, "overlapping ranges would force nested spans"
        assert hits[0].component_id == 10
        assert 11 in hits[0].also


class TestSegments:
    def test_offsets_are_columns_within_their_own_line(self):
        src = "line one\npublic Active__c x;\n"
        hits = locate(src, ".cls", allowed_for_active(),
                      edges((ACTIVE, "token_sweep")))
        segs = to_segments(src, line_index(src), hits)
        assert len(segs) == 1
        seg = segs[0]
        assert seg["line"] == 1
        assert src.split("\n")[1][seg["col"]:seg["end_col"]] == "Active__c"


class TestObjectMemberSpans:
    OBJ = """<?xml version="1.0" encoding="UTF-8"?>
<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">
    <fields>
        <fullName>Legacy_Code__c</fullName>
        <type>Text</type>
    </fields>
    <fields>
        <fullName>Order_Status__c</fullName>
        <valueSet>
            <valueSetDefinition>
                <value>
                    <fullName>New</fullName>
                </value>
            </valueSetDefinition>
        </valueSet>
    </fields>
</CustomObject>
"""

    def test_each_field_gets_its_own_span(self):
        members, complete = object_member_spans(self.OBJ)
        assert complete
        assert "fields:Legacy_Code__c" in members
        assert "fields:Order_Status__c" in members

    def test_a_picklist_value_is_not_mistaken_for_a_field(self):
        members, _ = object_member_spans(self.OBJ)
        # <fullName>New</fullName> lives inside valueSet, not inside a <fields>
        # child, so asking lxml for `fields` elements never sees it.
        assert "fields:New" not in members

    def test_spans_cover_the_whole_element(self):
        members, _ = object_member_spans(self.OBJ)
        lines = self.OBJ.split("\n")
        span = members["fields:Legacy_Code__c"]
        block = lines[span["start_line"]:span["end_line"] + 1]
        assert "<fields>" in block[0]
        assert "</fields>" in block[-1]
        assert any("Legacy_Code__c" in b for b in block)

    def test_unparseable_xml_reports_incomplete_rather_than_guessing(self):
        members, complete = object_member_spans("not xml at all <<<")
        assert members == {}
        assert complete is False


@pytest.mark.parametrize("suffix", [".cls", ".trigger", ".layout", ".object"])
def test_empty_source_is_safe(suffix):
    assert locate("", suffix, allowed_for_active(), edges()) == []
