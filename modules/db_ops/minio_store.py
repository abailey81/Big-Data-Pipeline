"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : MinIO data lake storage operations
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

"""

import json
import os

import pandas as pd
from ift_global import MinioFileSystemRepo

from modules.utils.info_logger import pipeline_logger


def _sanitise_for_json(obj):
    """Recursively convert non-serialisable dict keys/values to strings."""
    if isinstance(obj, dict):
        return {str(k): _sanitise_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitise_for_json(item) for item in obj]
    if isinstance(obj, pd.Timestamp):
        return str(obj)
    return obj


class MinioStore:
    """Handles raw data storage in MinIO data lake.

    Uses ift_global.MinioFileSystemRepo for MinIO connectivity.

    :param bucket_name: MinIO bucket name
    :type bucket_name: str
    :param raw_data_path: Base path for raw data within the bucket
    :type raw_data_path: str
    """

    def __init__(self, bucket_name: str = "iftbigdata", raw_data_path: str = "raw-data"):
        self.bucket_name = bucket_name
        self.raw_data_path = raw_data_path
        self._client = None

    @property
    def client(self):
        """Lazy-initialise MinIO client."""
        if self._client is None:
            try:
                self._client = MinioFileSystemRepo(
                    bucket_name=self.bucket_name,
                    user=os.environ.get("MINIO_USER"),
                    password=os.environ.get("MINIO_PASSWORD"),
                    endpoint_url=os.environ.get("MINIO_URL"),
                )
            except Exception as e:
                pipeline_logger.warning(
                    f"Could not connect to MinIO: {e}. " "Raw data storage will be skipped."
                )
        return self._client

    def store_raw_csv(self, data_bytes: bytes, category: str, identifier: str, date_str: str):
        """Store raw CSV data in MinIO.

        :param data_bytes: CSV content as bytes
        :type data_bytes: bytes
        :param category: Data category (prices, fx, vix)
        :type category: str
        :param identifier: File identifier (symbol or currency pair)
        :type identifier: str
        :param date_str: Date string for the file path
        :type date_str: str
        """
        if self.client is None:
            return
        path = f"{self.raw_data_path}/{category}/{identifier}/{date_str}.csv"
        try:
            self.client.get_client.put_object(
                Bucket=self.bucket_name, Key=path, Body=data_bytes, ContentType="text/csv"
            )
            pipeline_logger.info(f"Stored raw CSV: {path}")
        except Exception as e:
            pipeline_logger.warning(f"Failed to store {path} in MinIO: {e}")

    def store_raw_json(self, data_dict: dict, category: str, identifier: str, date_str: str):
        """Store raw JSON data in MinIO.

        :param data_dict: Data dictionary to store as JSON
        :type data_dict: dict
        :param category: Data category (fundamentals)
        :type category: str
        :param identifier: File identifier (symbol)
        :type identifier: str
        :param date_str: Date string for the file path
        :type date_str: str
        """
        if self.client is None:
            return
        path = f"{self.raw_data_path}/{category}/{identifier}/{date_str}.json"
        try:
            safe_dict = _sanitise_for_json(data_dict)
            json_bytes = json.dumps(safe_dict, default=str).encode("utf-8")
            self.client.get_client.put_object(
                Bucket=self.bucket_name, Key=path, Body=json_bytes, ContentType="application/json"
            )
            pipeline_logger.info(f"Stored raw JSON: {path}")
        except Exception as e:
            pipeline_logger.warning(f"Failed to store {path} in MinIO: {e}")
