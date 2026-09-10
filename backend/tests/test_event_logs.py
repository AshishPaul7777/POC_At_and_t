"""Unit tests for Event Monitoring CSV / ENTRY_POINT ingestion."""

from pathlib import Path

from app.pipeline.event_logs import (
    EventLogIndex,
    _ingest_csv_text,
    _parse_entry_point,
    load_event_logs,
    rest_path_keys_for_class,
)


def test_loads_bundled_event_csvs():
    root = Path(__file__).resolve().parents[1] / "event_data"
    idx = load_event_logs(root)
    assert idx.files_read
    assert idx.rows > 0
    assert idx.hit_for_class("InventoryReplenishmentService")
    assert idx.hit_for_class("InventoryReplenishmentService").executions >= 1
    assert idx.hit_for_method("ExternalCalloutService.getSampleData")
    assert idx.hit_for_method("ExternalCalloutService.getSampleData").callouts >= 1
    assert idx.hit_for_trigger("CustomerOrderTrigger")
    assert idx.hit_for_trigger("CustomerOrderTrigger").triggers >= 1


def test_parse_entry_point_shapes():
    assert _parse_entry_point("AccountController.getAccountList") == (
        "class", "AccountController", "getAccountList")
    assert _parse_entry_point("CustomerOrderRestService.createCustomerOrder()") == (
        "class", "CustomerOrderRestService", "createCustomerOrder")
    assert _parse_entry_point("LoyaltyMembershipExpirationScheduler") == (
        "class", "LoyaltyMembershipExpirationScheduler", "")
    assert _parse_entry_point(
        "ApexCalloutEventTrigger on Apex_Callout_Event trigger event AfterInsert"
    ) == ("trigger", "ApexCalloutEventTrigger", "afterinsert")
    assert _parse_entry_point("execute_anonymous_apex")[0] == "skip"
    assert _parse_entry_point("TRIGGERS")[0] == "skip"
    assert _parse_entry_point("VF- /apex/viewApexClass.apexp")[0] == "skip"


def test_ingest_live_style_apex_execution_csv():
    csv_text = (
        "EVENT_TYPE,TIMESTAMP_DERIVED,ENTRY_POINT\n"
        "ApexExecution,2026-09-08T06:00:00.000Z,AccountController.getAccountList\n"
        "ApexExecution,2026-09-08T06:01:00.000Z,execute_anonymous_apex\n"
        "ApexExecution,2026-09-08T06:02:00.000Z,"
        "OrderTriggerEventTrigger on Order_Trigger_Event trigger event AfterInsert\n"
        "ApexExecution,2026-09-08T06:03:00.000Z,VF- /apex/editTraceFlag.apexp\n"
    )
    idx = EventLogIndex(source="event_log_file")
    _ingest_csv_text(csv_text, idx, label="test", event_hint="ApexExecution")
    assert idx.hit_for_method("AccountController.getAccountList")
    assert idx.hit_for_class("AccountController")
    assert idx.hit_for_trigger("OrderTriggerEventTrigger")
    assert not idx.hit_for_class("execute_anonymous_apex")
    assert not idx.hit_for_class("editTraceFlag")


def test_ingest_ui_object_field_and_rest():
    lightning = (
        "EVENT_TYPE,TIMESTAMP_DERIVED,COMPONENT_NAME,PAGE_ENTITY_TYPE\n"
        "LightningInteraction,2026-09-08T06:00:00.000Z,"
        "force:detailPanel;c:loyaltyMembershipCard,Loyalty_Membership__c\n"
        "LightningInteraction,2026-09-08T06:01:00.000Z,c:accountList,\n"
    )
    page = (
        "EVENT_TYPE,TIMESTAMP_DERIVED,PAGE_ENTITY_TYPE\n"
        "LightningPageView,2026-09-08T06:00:00.000Z,Customer_Order__c\n"
        "LightningPageView,2026-09-08T06:01:00.000Z,Contact\n"
    )
    rest = (
        "EVENT_TYPE,TIMESTAMP_DERIVED,ENTITY_NAME,QUERY\n"
        "RestApi,2026-09-08T06:00:00.000Z,Inventory_Item__c,"
        '"SOQL Query: SELECT COUNT(Aisle_Location__c) FROM Inventory_Item__c"\n'
    )
    apex_rest = (
        "EVENT_TYPE,TIMESTAMP_DERIVED,URI,METHOD\n"
        "ApexRestApi,2026-09-08T06:00:00.000Z,/CustomerOrder/,POST\n"
    )
    dml = (
        "EVENT_TYPE,TIMESTAMP_DERIVED,KEY_PREFIX,DML_TYPE\n"
        "DatabaseSave,2026-09-08T06:00:00.000Z,a03,INSERT\n"
    )
    idx = EventLogIndex(source="event_log_file")
    _ingest_csv_text(lightning, idx, event_hint="LightningInteraction")
    _ingest_csv_text(page, idx, event_hint="LightningPageView")
    _ingest_csv_text(rest, idx, event_hint="RestApi")
    _ingest_csv_text(apex_rest, idx, event_hint="ApexRestApi")
    _ingest_csv_text(dml, idx, event_hint="DatabaseSave")

    assert idx.hit_for_ui("loyaltyMembershipCard")
    assert idx.hit_for_ui("accountList")
    assert idx.hit_for_object("Loyalty_Membership__c")
    assert idx.hit_for_object("Customer_Order__c")
    assert not idx.hit_for_object("Contact")
    assert idx.hit_for_object("Inventory_Item__c")
    assert idx.hit_for_field("Aisle_Location__c")
    assert idx.hit_for_key_prefix("a03")
    assert idx.by_rest_path.get("customerorder")
    assert idx.hit_for_apex_class("CustomerOrderRestService")
    assert "customerorder" in rest_path_keys_for_class("CustomerOrderRestService")
