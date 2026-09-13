"""The dictionary is the contract. These tests check it can be trusted."""

import pytest


def test_every_table_is_defined(contract):
    assert set(contract.tables()) == {
        "official_timeline", "timeline_change_log", "import_run",
        "data_work_items", "team_updates",
    }


def test_official_timeline_field_count(contract):
    # 21 source columns produce 23 fields, because Instrument Short Name feeds three,
    # plus 7 generated. Change this number only when the dictionary changes on purpose.
    assert len(contract.fields("official_timeline")) == 30


def test_exactly_24_source_columns(contract):
    assert len(contract.source_columns()) == 24


def test_source_columns_are_unique(contract):
    cols = contract.source_columns()
    assert len(cols) == len(set(cols)), "a duplicated header would silently drop a column"


def test_natural_key_is_the_three_fields(contract):
    assert contract.natural_key_fields() == [
        "instrument_id", "implementation_need", "fiscal_year"]


def test_key_fields_are_not_nullable(contract):
    for name in contract.natural_key_fields():
        assert not contract.field("official_timeline", name).nullable, \
            f"{name} is part of the key, so it can never be empty"


def test_every_data_type_is_known(contract):
    allowed = {"string", "integer", "date", "timestamp", "boolean"}
    for table in contract.tables():
        for f in contract.fields(table):
            assert f.base_type in allowed, f"{table}.{f.name} has type {f.data_type!r}"


def test_every_controlled_list_reference_exists(contract):
    """A field pointing at a list that is not defined would validate against nothing."""
    lists = set(contract.controlled_lists())
    for table in contract.tables():
        for f in contract.fields(table):
            if f.controlled_list:
                assert f.controlled_list in lists, \
                    f"{table}.{f.name} references the undefined list {f.controlled_list!r}"


def test_primary_keys_are_single_and_not_nullable(contract):
    for table in contract.tables():
        pks = [f for f in contract.fields(table) if f.key == "PK"]
        assert len(pks) == 1, f"{table} should have exactly one primary key"
        assert not pks[0].nullable


def test_team_tables_are_never_written_by_the_import(contract):
    """The governing rule, expressed in the dictionary itself."""
    for table in ("data_work_items", "team_updates"):
        for f in contract.fields(table):
            assert f.written_by != "Import", \
                f"{table}.{f.name} claims the import writes it, which the charter forbids"


def test_official_timeline_is_only_written_by_the_import(contract):
    for f in contract.fields("official_timeline"):
        assert f.written_by == "Import"


def test_every_field_cites_a_source(contract):
    for table in contract.tables():
        for f in contract.fields(table):
            assert f.source_authority, f"{table}.{f.name} does not say where it came from"


def test_cleaning_rules_have_unique_ids(contract):
    ids = [r.id for r in contract.rules()]
    assert len(ids) == len(set(ids))


def test_the_naming_rule_is_not_automatic(contract):
    """C09 reports divergence from the SRS. Auto correcting it would hide a real
    disagreement between two documents that people have to resolve."""
    assert contract.rule("C09").automatic is False


def test_gate_accepts_the_expected_headers(contract):
    missing, unexpected = contract.check_source_columns(contract.source_columns())
    assert missing == [] and unexpected == []


def test_gate_names_a_missing_column(contract):
    cols = [c for c in contract.source_columns() if c != "Fiscal Year"]
    missing, unexpected = contract.check_source_columns(cols)
    assert missing == ["Fiscal Year"]


def test_gate_reports_an_extra_column(contract):
    missing, unexpected = contract.check_source_columns(
        contract.source_columns() + ["Some New Column"])
    assert missing == []
    assert unexpected == ["Some New Column"]


def test_load_refuses_a_missing_file():
    from tracker.contract import load
    with pytest.raises(FileNotFoundError):
        load("/nowhere/at/all.xlsx")
