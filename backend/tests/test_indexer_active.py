"""A disabled feature-flag CMDT record must not count as an active consumer.

A name found only inside such a record does not prove any business process
currently runs through it — the record's own gating field says otherwise.
"""

from app.pipeline.indexer import _is_active

_DISABLED = """<?xml version="1.0" encoding="UTF-8"?>
<CustomMetadata xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>EnableAuraNeedsReviewDemo</label>
    <protected>false</protected>
    <values>
        <field>Description__c</field>
        <value xsi:type="xsd:string">Gates a demo component's Apex call.</value>
    </values>
    <values>
        <field>Is_Enabled__c</field>
        <value xsi:type="xsd:boolean">false</value>
    </values>
</CustomMetadata>
"""

_ENABLED = _DISABLED.replace(
    '<value xsi:type="xsd:boolean">false</value>',
    '<value xsi:type="xsd:boolean">true</value>',
)

_NO_GATE_FIELD = """<?xml version="1.0" encoding="UTF-8"?>
<CustomMetadata xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Dynamic_Apex_Target_Config</label>
    <values>
        <field>Target_Class_Name__c</field>
        <value xsi:type="xsd:string">CleanupTest_DynamicApexTarget</value>
    </values>
</CustomMetadata>
"""


def test_cmdt_disabled_gate_field_is_inactive():
    assert _is_active("CustomMetadata", _DISABLED) is False


def test_cmdt_enabled_gate_field_is_active():
    assert _is_active("CustomMetadata", _ENABLED) is True


def test_cmdt_without_a_gate_field_is_active():
    assert _is_active("CustomMetadata", _NO_GATE_FIELD) is True


def test_other_metadata_types_unaffected():
    assert _is_active("ApexClass", _DISABLED) is True
