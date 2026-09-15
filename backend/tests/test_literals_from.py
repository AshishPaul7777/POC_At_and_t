"""A component name mentioned inside prose (an audit-log message) is not a
dynamic reference, unlike a name inside a SOQL string, which is.
"""

from app.pipeline.indexer import _literals_from
from app.pipeline.source_text import extract

_LOG_MESSAGE_APEX = """
public class Foo {
    void run() {
        insert new Log__c(
            Notes__c = 'auraCleanupTestNeedsReviewCard ran its gated task.'
        );
    }
}
"""

_SOQL_APEX = """
public class Foo {
    void run() {
        String q = 'SELECT Legacy_Code__c FROM Order__c';
    }
}
"""


def test_prose_inside_a_literal_is_not_tokenised():
    literals = _literals_from(extract(".cls", _LOG_MESSAGE_APEX))
    assert "auracleanuptestneedsreviewcard" not in literals


def test_soql_inside_a_literal_is_still_tokenised():
    literals = _literals_from(extract(".cls", _SOQL_APEX))
    assert literals.get("legacy_code__c") == "string_literal"
