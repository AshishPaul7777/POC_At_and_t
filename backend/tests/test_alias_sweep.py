"""The distinctiveness guard on alias sweeping.

Regression cover for a real false positive: `Account.Active__c` reduces to the
alias `active`, which a bare-token sweep matched against the English word in a
doc-comment and against `<status>Active</status>` in every Apex meta file. The
field collected a dozen Apex classes as "references" that mention it nowhere.

The property under test is one-directional and worth stating plainly: a form
that a real reference can take must stay sweepable, and a form that ordinary
prose produces must not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline.aliases import (
    AMBIGUOUS_BARE_WORDS,
    MIN_BARE_ALIAS_LENGTH,
    field_aliases,
    is_sweepable,
    method_aliases,
    object_aliases,
)
from app.pipeline.indexer import NON_REFERENCE_KINDS, _is_apex_meta


# -- forms a genuine reference actually takes ---------------------------------

@pytest.mark.parametrize("alias", [
    "active__c",                      # Apex, SOQL, formulas
    "account.active__c",              # layouts, LWC schema imports, merge fields
    "account$active__c",              # report and CRT column refs
    "legacy_code",                    # bare name of a multi-word field
    "aisle_location__c",
    "numberoflocations",              # long enough to be distinctive on its own
    "currentgenerators",
    "origgoalid",
    "c.getaccounts",                  # Aura binding
    "@salesforce/apex/AccountController.getAccountList".lower(),
    "00ngl00004uzemz",                # 15-char record id
    "00ngl00004uzemzuaj",             # 18-char record id
    "customer order",                 # multi-word label: only ever a literal
])
def test_real_reference_forms_stay_sweepable(alias):
    assert is_sweepable(alias), f"{alias!r} is a form a real reference takes"


# -- forms that are indistinguishable from prose ------------------------------

@pytest.mark.parametrize("alias", [
    "active",       # the bug that started this
    "status",       # <status>Active</status>
    "apiversion",
    "description",
    "picture",
    "level",
    "brand",
    "city",
    "customer",
    "languages",
    "type",
    "name",
    "class",        # Apex keyword
    "test",
    "sla",          # too short to be anything but a collision
    "ext",
    "",
])
def test_prose_forms_are_not_sweepable(alias):
    assert not is_sweepable(alias), f"{alias!r} would match ordinary prose"


def test_underscore_beats_the_stop_list():
    """A stop word is only ambiguous as a *bare* word.

    `active_flag__c` and `is_active` are unambiguous despite containing a
    stop word, because no sentence produces them by accident.
    """
    assert is_sweepable("active_flag__c")
    assert is_sweepable("is_active")
    assert is_sweepable("active.status")


def test_stop_list_entries_are_lowercase_and_bare():
    """Aliases are lowercased before comparison, so an entry with a capital or
    an underscore could never match and would be silently dead weight."""
    for w in AMBIGUOUS_BARE_WORDS:
        assert w == w.lower(), f"{w!r} would never be compared against"
        assert "_" not in w, f"{w!r} is exempted by the structural check anyway"


def test_length_floor_is_enforced_for_unlisted_words():
    # A short word absent from the stop list must still be refused: the list
    # cannot be exhaustive, so the floor is the backstop.
    assert not is_sweepable("qtr")
    assert not is_sweepable("abcde")                       # 5 < the floor
    assert is_sweepable("a" * MIN_BARE_ALIAS_LENGTH)


# -- the guard applies to the alias set of a real field ------------------------

def test_active_field_keeps_precise_aliases_and_disowns_the_stripped_one():
    """The case this whole module exists for.

    `Active__c` with the suffix removed is `Active`, which Salesforce never uses
    to mean this field. It stays in the table -- a reviewer asking "did you
    consider the label?" should see that it was considered and rejected -- but
    its kind puts it outside what counts as a reference.
    """
    aliases = field_aliases(api_name="Active__c", parent_object="Account",
                            label="Active", sf_id="00Ngl00004UzEmZUAJ")
    kinds = {(a.alias_lc, a.kind) for a in aliases}

    assert ("active", "stripped_suffix") in kinds
    assert ("active", "label") in kinds
    for kind in ("stripped_suffix", "label"):
        assert kind in NON_REFERENCE_KINDS

    # Everything that can identify the field unambiguously is a reference.
    by_alias = {a.alias_lc: a.kind for a in aliases}
    for precise in ("active__c", "account.active__c", "account$active__c"):
        assert precise in by_alias
        assert by_alias[precise] not in NON_REFERENCE_KINDS
        assert is_sweepable(precise)


def test_object_stripped_suffix_is_also_disowned():
    aliases = object_aliases(api_name="Retail_Product__c", label="Retail Product")
    kinds = {(a.alias_lc, a.kind) for a in aliases}
    assert ("retail_product", "stripped_suffix") in kinds
    # The __r relationship form IS a name the platform uses.
    assert ("retail_product__r", "relationship_name") in kinds


def test_apex_method_plain_names_stay_references():
    """A method really is called `doGet` -- nothing was stripped to get there.

    Retyping these would break Aura bindings and Visualforce getters, which is
    the opposite error: losing real evidence.
    """
    aliases = method_aliases(class_name="ContactsResource", method_name="doGet")
    for a in aliases:
        assert a.kind not in NON_REFERENCE_KINDS, f"{a.alias_lc} ({a.kind})"


def test_a_distinctive_field_loses_nothing():
    aliases = field_aliases(api_name="Aisle_Location__c",
                            parent_object="Inventory_Item__c",
                            label="Aisle Location")
    for a in aliases:
        assert is_sweepable(a.alias_lc), f"{a.alias_lc!r} ({a.kind}) was dropped"


# -- Apex meta files -----------------------------------------------------------

def test_apex_meta_files_are_recognised():
    assert _is_apex_meta(Path("classes/PromotionController.cls-meta.xml"))
    assert _is_apex_meta(Path("triggers/AccountTrigger.trigger-meta.xml"))


def test_other_meta_files_are_not_skipped():
    """LWC and email meta files carry targets, targetConfigs and object
    bindings -- real references. Skipping those would lose recall."""
    assert not _is_apex_meta(Path("lwc/accountList/accountList.js-meta.xml"))
    assert not _is_apex_meta(Path("email/welcome.email-meta.xml"))
    assert not _is_apex_meta(Path("staticresources/logo.resource-meta.xml"))
    assert not _is_apex_meta(Path("objects/Account.object-meta.xml"))
