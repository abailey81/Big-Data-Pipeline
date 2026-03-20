"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Unit tests for MongoDB connector
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

"""

from unittest.mock import MagicMock, patch

import pytest

from modules.db_ops.mongo_conn import MongoDBStore


class TestMongoDBStore:
    """Tests for MongoDBStore document operations."""

    def test_init_default_params(self):
        store = MongoDBStore()
        assert store.host == "localhost"
        assert store.port == 27017
        assert store.database_name == "ift_cw1"
        assert store._client is None

    def test_init_custom_params(self):
        store = MongoDBStore(
            host="mongo.example.com", port=27018, username="user", password="pass", database="testdb"
        )
        assert store.host == "mongo.example.com"
        assert store.port == 27018
        assert store.database_name == "testdb"

    @patch("modules.db_ops.mongo_conn.PYMONGO_AVAILABLE", False)
    def test_client_returns_none_when_pymongo_unavailable(self):
        store = MongoDBStore()
        assert store.client is None
        assert store.db is None

    @patch("modules.db_ops.mongo_conn.PYMONGO_AVAILABLE", True)
    @patch("modules.db_ops.mongo_conn.MongoClient")
    def test_client_lazy_init_success(self, mock_mongo_client):
        mock_instance = MagicMock()
        mock_mongo_client.return_value = mock_instance
        mock_instance.__getitem__ = MagicMock(return_value=MagicMock())

        store = MongoDBStore()
        client = store.client
        assert client is not None
        mock_instance.admin.command.assert_called_once_with("ping")

    @patch("modules.db_ops.mongo_conn.PYMONGO_AVAILABLE", True)
    @patch("modules.db_ops.mongo_conn.MongoClient")
    def test_client_connection_failure_returns_none(self, mock_mongo_client):
        mock_mongo_client.side_effect = Exception("Connection refused")
        store = MongoDBStore()
        assert store.client is None
        assert store.db is None

    @patch("modules.db_ops.mongo_conn.PYMONGO_AVAILABLE", False)
    def test_store_document_when_db_is_none(self):
        store = MongoDBStore()
        store._client = None
        result = store.store_document("test_collection", {"key": "value"})
        assert result is None

    @patch("modules.db_ops.mongo_conn.PYMONGO_AVAILABLE", False)
    def test_store_documents_when_db_is_none(self):
        store = MongoDBStore()
        store._client = None
        result = store.store_documents("test_collection", [{"key": "value"}])
        assert result == 0

    def test_store_documents_empty_list(self):
        store = MongoDBStore()
        result = store.store_documents("test_collection", [])
        assert result == 0

    @patch("modules.db_ops.mongo_conn.PYMONGO_AVAILABLE", False)
    def test_find_documents_when_db_is_none(self):
        store = MongoDBStore()
        store._client = None
        result = store.find_documents("test_collection", {"symbol": "AAPL"})
        assert result == []

    @patch("modules.db_ops.mongo_conn.PYMONGO_AVAILABLE", True)
    @patch("modules.db_ops.mongo_conn.MongoClient")
    def test_store_document_success(self, mock_mongo_client):
        mock_instance = MagicMock()
        mock_mongo_client.return_value = mock_instance
        mock_db = MagicMock()
        mock_instance.__getitem__ = MagicMock(return_value=mock_db)
        mock_collection = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)
        mock_collection.insert_one.return_value = MagicMock(inserted_id="abc123")

        store = MongoDBStore()
        _ = store.client  # trigger lazy init
        result = store.store_document("esg_reports", {"symbol": "AAPL"})
        assert result == "abc123"

    @patch("modules.db_ops.mongo_conn.PYMONGO_AVAILABLE", True)
    @patch("modules.db_ops.mongo_conn.MongoClient")
    def test_store_documents_success(self, mock_mongo_client):
        mock_instance = MagicMock()
        mock_mongo_client.return_value = mock_instance
        mock_db = MagicMock()
        mock_instance.__getitem__ = MagicMock(return_value=mock_db)
        mock_collection = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)
        mock_collection.insert_many.return_value = MagicMock(inserted_ids=["id1", "id2"])

        store = MongoDBStore()
        _ = store.client
        docs = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        result = store.store_documents("esg_reports", docs)
        assert result == 2

    def test_close_resets_client(self):
        store = MongoDBStore()
        store._client = MagicMock()
        store._db = MagicMock()
        store.close()
        assert store._client is None
        assert store._db is None

    def test_store_document_real_insert(self):
        """store_document inserts into real MongoDB and returns an ID."""
        store = MongoDBStore(database="ift_cw1_test")
        if store.db is None:
            pytest.skip("MongoDB not available")
        result = store.store_document("_test_col", {"test_key": "test_val"})
        assert result is not None
        assert isinstance(result, str)
        # Clean up
        store.db["_test_col"].drop()
        store.close()

    def test_store_documents_real_insert(self):
        """store_documents inserts multiple docs into real MongoDB."""
        store = MongoDBStore(database="ift_cw1_test")
        if store.db is None:
            pytest.skip("MongoDB not available")
        docs = [{"sym": "AAPL"}, {"sym": "MSFT"}, {"sym": "GOOG"}]
        count = store.store_documents("_test_col", docs)
        assert count == 3
        store.db["_test_col"].drop()
        store.close()

    def test_store_documents_empty_returns_zero(self):
        """store_documents returns 0 for empty list."""
        store = MongoDBStore(database="ift_cw1_test")
        if store.db is None:
            pytest.skip("MongoDB not available")
        result = store.store_documents("_test_col", [])
        assert result == 0
        store.close()

    def test_find_documents_returns_results(self):
        """find_documents queries and returns real docs."""
        store = MongoDBStore(database="ift_cw1_test")
        if store.db is None:
            pytest.skip("MongoDB not available")
        store.db["_test_col"].insert_one({"symbol": "AAPL", "value": 42})
        result = store.find_documents("_test_col", {"symbol": "AAPL"})
        assert len(result) >= 1
        assert result[0]["symbol"] == "AAPL"
        store.db["_test_col"].drop()
        store.close()

    def test_find_documents_no_match(self):
        """find_documents returns [] when no docs match."""
        store = MongoDBStore(database="ift_cw1_test")
        if store.db is None:
            pytest.skip("MongoDB not available")
        result = store.find_documents("_test_col", {"symbol": "NONEXISTENT_XYZ"})
        assert result == []
        store.close()

    def test_store_document_error_returns_none(self):
        """store_document returns None when MongoDB rejects the write."""
        store = MongoDBStore(database="ift_cw1_test")
        if store.db is None:
            pytest.skip("MongoDB not available")
        # Insert a doc with explicit _id, then try to insert same _id again
        store.db["_test_col"].insert_one({"_id": "dup_test", "val": 1})
        result = store.store_document("_test_col", {"_id": "dup_test", "val": 2})
        assert result is None  # duplicate key error → caught → returns None
        store.db["_test_col"].drop()
        store.close()

    def test_store_documents_error_returns_zero(self):
        """store_documents returns 0 when MongoDB rejects the batch."""
        store = MongoDBStore(database="ift_cw1_test")
        if store.db is None:
            pytest.skip("MongoDB not available")
        store.db["_test_col"].insert_one({"_id": "dup_batch", "val": 1})
        docs = [{"_id": "dup_batch", "val": 2}]  # duplicate _id
        result = store.store_documents("_test_col", docs)
        assert result == 0  # duplicate key error → caught → returns 0
        store.db["_test_col"].drop()
        store.close()

    def test_find_documents_error_returns_empty(self):
        """find_documents returns [] when query causes an error."""
        store = MongoDBStore(database="ift_cw1_test")
        if store.db is None:
            pytest.skip("MongoDB not available")
        # Pass an invalid query operator to trigger a real MongoDB error
        result = store.find_documents("_test_col", {"$invalid_op": True})
        assert result == []
        store.close()

    def test_find_documents_with_limit(self):
        """find_documents respects limit parameter."""
        store = MongoDBStore(database="ift_cw1_test")
        if store.db is None:
            pytest.skip("MongoDB not available")
        for i in range(5):
            store.db["_test_col"].insert_one({"idx": i})
        result = store.find_documents("_test_col", {}, limit=2)
        assert len(result) == 2
        store.db["_test_col"].drop()
        store.close()

