"""The extraction helpers absorb Rox envelope differences. TestLiveShapes is
pinned to payloads captured from the live API; the rest cover shapes the API
could plausibly return."""

from app.rox.client import (
    flatten_hierarchy,
    unwrap,
)

#: Captured live from GET /hierarchy/customers
LIVE_HIERARCHY = [
    {
        "customer_name": "The Walt Disney Company",
        "customer_id": "2e579183-16df-455b-991f-91988d5ff875",
        "domain": "disneycareers.com",
        "hierarchy_parent_id": None,
        "children": [],
        "agent_status": "AVAILABLE",
    },
    {
        "customer_name": "Tesla",
        "customer_id": "ad901858-3f00-4a97-b44d-e3e840da4167",
        "domain": "tesla.com",
        "hierarchy_parent_id": None,
        "children": [],
        "agent_status": "AVAILABLE",
    },
]


class TestLiveShapes:
    """Pinned against payloads actually returned by core.roxai.dev."""

    def test_live_hierarchy_flattens(self):
        out = flatten_hierarchy(LIVE_HIERARCHY)
        assert len(out) == 2
        assert out[0]["_id"] == "2e579183-16df-455b-991f-91988d5ff875"
        assert out[0]["_name"] == "The Walt Disney Company"
        assert out[1]["_name"] == "Tesla"
        assert all(a["_parent_id"] is None for a in out)


class TestUnwrap:
    def test_strips_data_envelope(self):
        assert unwrap({"data": {"id": 1}}) == {"id": 1}

    def test_strips_results_envelope(self):
        assert unwrap({"results": [1, 2]}) == [1, 2]

    def test_passes_through_bare_payload(self):
        assert unwrap({"id": 1, "name": "Acme"}) == {"id": 1, "name": "Acme"}

    def test_leaves_scalar_data_key_alone(self):
        # `data` holding a scalar is a real field, not an envelope
        assert unwrap({"data": "hello"}) == {"data": "hello"}


class TestFlattenHierarchy:
    def test_flat_list(self):
        out = flatten_hierarchy([{"id": "1", "name": "Acme"}, {"id": "2", "name": "Beta"}])
        assert [a["_id"] for a in out] == ["1", "2"]
        assert all(a["_parent_id"] is None for a in out)

    def test_nested_children_get_parent_ids(self):
        tree = [
            {
                "id": "1",
                "name": "Parent Co",
                "children": [
                    {"id": "2", "name": "Sub A"},
                    {"id": "3", "name": "Sub B", "children": [{"id": "4", "name": "Deep"}]},
                ],
            }
        ]
        out = flatten_hierarchy(tree)
        by_id = {a["_id"]: a for a in out}
        assert set(by_id) == {"1", "2", "3", "4"}
        assert by_id["2"]["_parent_id"] == "1"
        assert by_id["4"]["_parent_id"] == "3"

    def test_handles_data_envelope_and_customers_key(self):
        payload = {"data": {"customers": [{"entity_id": "x1", "customer_name": "Acme"}]}}
        out = flatten_hierarchy(payload)
        assert len(out) == 1
        assert out[0]["_id"] == "x1"
        assert out[0]["_name"] == "Acme"

    def test_descends_through_container_without_id(self):
        # a wrapper node with no id/name should not become a fake account
        payload = {"nodes": [{"id": "a", "name": "Real"}]}
        out = flatten_hierarchy(payload)
        assert [a["_id"] for a in out] == ["a"]

    def test_empty(self):
        assert flatten_hierarchy({}) == []
        assert flatten_hierarchy([]) == []
