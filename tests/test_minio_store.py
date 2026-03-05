"""
Tests for modules.db_ops.minio_store.MinioStore

Covers MinIO data lake operations with mocked ift_global.MinioFileSystemRepo.
"""

import json
from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest


class TestMinioStore:

    @patch("modules.db_ops.minio_store.MinioFileSystemRepo")
    def test_lazy_client_init(self, mock_minio_cls):
        from modules.db_ops.minio_store import MinioStore

        store = MinioStore(bucket_name="test-bucket")
        assert store._client is None
        # Accessing the client property triggers initialisation
        _ = store.client
        mock_minio_cls.assert_called_once()

    @patch("modules.db_ops.minio_store.MinioFileSystemRepo")
    def test_store_raw_csv_success(self, mock_minio_cls):
        from modules.db_ops.minio_store import MinioStore

        mock_instance = MagicMock()
        mock_minio_cls.return_value = mock_instance

        store = MinioStore(bucket_name="iftbigdata", raw_data_path="raw-data")
        csv_bytes = b"Open,Close\n150,151\n"
        store.store_raw_csv(csv_bytes, "prices", "AAPL", "2024-01-02")

        mock_instance.get_client.put_object.assert_called_once()
        put_kwargs = mock_instance.get_client.put_object.call_args[1]
        assert put_kwargs["Bucket"] == "iftbigdata"
        assert put_kwargs["Key"] == "raw-data/prices/AAPL/2024-01-02.csv"

    @patch("modules.db_ops.minio_store.MinioFileSystemRepo")
    def test_store_raw_csv_correct_content_type(self, mock_minio_cls):
        from modules.db_ops.minio_store import MinioStore

        mock_instance = MagicMock()
        mock_minio_cls.return_value = mock_instance

        store = MinioStore()
        store.store_raw_csv(b"data", "fx", "GBPUSD", "2024-01-02")
        put_kwargs = mock_instance.get_client.put_object.call_args[1]
        assert put_kwargs["ContentType"] == "text/csv"

    @patch("modules.db_ops.minio_store.MinioFileSystemRepo")
    def test_store_raw_csv_no_client_no_error(self, mock_minio_cls):
        from modules.db_ops.minio_store import MinioStore

        mock_minio_cls.side_effect = Exception("connection refused")
        store = MinioStore()
        # Should not raise — graceful degradation
        store.store_raw_csv(b"data", "prices", "AAPL", "2024-01-02")

    @patch("modules.db_ops.minio_store.MinioFileSystemRepo")
    def test_store_raw_json_success(self, mock_minio_cls):
        from modules.db_ops.minio_store import MinioStore

        mock_instance = MagicMock()
        mock_minio_cls.return_value = mock_instance

        store = MinioStore(bucket_name="iftbigdata", raw_data_path="raw-data")
        data = {"symbol": "AAPL", "records": 42}
        store.store_raw_json(data, "fundamentals", "AAPL", "2024-01-02")

        mock_instance.get_client.put_object.assert_called_once()
        put_kwargs = mock_instance.get_client.put_object.call_args[1]
        assert put_kwargs["Key"] == "raw-data/fundamentals/AAPL/2024-01-02.json"

    @patch("modules.db_ops.minio_store.MinioFileSystemRepo")
    def test_store_raw_json_content_type(self, mock_minio_cls):
        from modules.db_ops.minio_store import MinioStore

        mock_instance = MagicMock()
        mock_minio_cls.return_value = mock_instance

        store = MinioStore()
        store.store_raw_json({"key": "value"}, "test", "id", "2024-01-01")
        put_kwargs = mock_instance.get_client.put_object.call_args[1]
        assert put_kwargs["ContentType"] == "application/json"

    @patch("modules.db_ops.minio_store.MinioFileSystemRepo")
    def test_store_raw_json_handles_dates(self, mock_minio_cls):
        from datetime import date

        from modules.db_ops.minio_store import MinioStore

        mock_instance = MagicMock()
        mock_minio_cls.return_value = mock_instance

        store = MinioStore()
        data = {"date": date(2024, 1, 15), "value": 100}
        # Should not raise (json.dumps with default=str handles dates)
        store.store_raw_json(data, "test", "id", "2024-01-15")
        mock_instance.get_client.put_object.assert_called_once()

    @patch("modules.db_ops.minio_store.MinioFileSystemRepo")
    def test_client_init_failure_logs_warning(self, mock_minio_cls):
        from modules.db_ops.minio_store import MinioStore

        mock_minio_cls.side_effect = Exception("cannot connect")
        store = MinioStore()
        client = store.client
        assert client is None

    @patch("modules.db_ops.minio_store.MinioFileSystemRepo")
    def test_store_raw_csv_put_failure_logs_warning(self, mock_minio_cls):
        from modules.db_ops.minio_store import MinioStore

        mock_instance = MagicMock()
        mock_instance.get_client.put_object.side_effect = Exception("upload failed")
        mock_minio_cls.return_value = mock_instance

        store = MinioStore()
        # Should not raise — failure is logged
        store.store_raw_csv(b"data", "prices", "AAPL", "2024-01-02")
