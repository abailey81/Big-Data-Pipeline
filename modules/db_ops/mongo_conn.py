"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : MongoDB document store operations
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Provides a MongoDB client for storing semi-structured data that does
not fit neatly into the PostgreSQL relational schema.  Uses include:

  - Raw API response caching (variable JSON structures)
  - ESG sustainability reports (sparse, per-company fields)
  - News sentiment documents (variable-length text + metadata)

The assignment spec states: "Two database systems are provided by
default MongoDB & PostgreSQL." This module implements the MongoDB
component alongside the existing PostgreSQL operations in sql_conn.py.

"""

import os
from datetime import datetime, timezone
from typing import Optional

from modules.utils.info_logger import pipeline_logger

try:
    from pymongo import MongoClient
    from pymongo.errors import ConnectionFailure, OperationFailure

    PYMONGO_AVAILABLE = True
except ImportError:
    MongoClient = None
    ConnectionFailure = Exception
    OperationFailure = Exception
    PYMONGO_AVAILABLE = False


class MongoDBStore:
    """MongoDB document store for semi-structured pipeline data.

    Connects to the MongoDB instance defined in docker-compose.yml
    (service: ``mongodb``, database: ``ift_cw1``).  Collections are
    auto-created on first insert if they do not exist.

    :param host: MongoDB host
    :type host: str
    :param port: MongoDB port
    :type port: int
    :param username: MongoDB username
    :type username: str
    :param password: MongoDB password
    :type password: str
    :param database: Target database name
    :type database: str
    """

    def __init__(
        self,
        host: str = None,
        port: int = None,
        username: str = None,
        password: str = None,
        database: str = "ift_cw1",
    ):
        self.host = host or os.environ.get("MONGO_HOST", "localhost")
        self.port = port or int(os.environ.get("MONGO_PORT", "27017"))
        self.username = username or os.environ.get("MONGO_USERNAME", "ift_bigdata")
        self.password = password or os.environ.get("MONGO_PASSWORD", "mongo_password")
        self.database_name = database
        self._client = None
        self._db = None

    @property
    def client(self):
        """Lazy-initialise MongoDB client.

        :return: PyMongo client or None if unavailable
        :rtype: MongoClient or None
        """
        if self._client is None:
            if not PYMONGO_AVAILABLE:
                pipeline_logger.warning("pymongo not installed — MongoDB storage disabled")
                return None
            try:
                self._client = MongoClient(
                    host=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    authSource="admin",
                    serverSelectionTimeoutMS=5000,
                    socketTimeoutMS=30000,
                    connectTimeoutMS=10000,
                )
                self._client.admin.command("ping")
                self._db = self._client[self.database_name]
                pipeline_logger.info(f"Connected to MongoDB at {self.host}:{self.port}")
            except (ConnectionFailure, OperationFailure, Exception) as e:
                pipeline_logger.warning(
                    f"Could not connect to MongoDB: {e}. " "Document storage will be skipped."
                )
                self._client = None
                self._db = None
        return self._client

    @property
    def db(self):
        """Returns the MongoDB database object.

        :return: Database handle or None
        """
        if self.client is None:
            return None
        return self._db

    def store_document(self, collection: str, document: dict) -> Optional[str]:
        """Insert a single document into a MongoDB collection.

        :param collection: Target collection name
        :type collection: str
        :param document: Document to store
        :type document: dict
        :return: Inserted document ID as string, or None
        :rtype: str or None
        """
        if self.db is None:
            return None
        try:
            document["ingested_at"] = datetime.now(timezone.utc)
            result = self.db[collection].insert_one(document)
            return str(result.inserted_id)
        except Exception as e:
            pipeline_logger.warning(f"Failed to store document in {collection}: {e}")
            return None

    def store_documents(self, collection: str, documents: list[dict]) -> int:
        """Insert multiple documents into a MongoDB collection.

        :param collection: Target collection name
        :type collection: str
        :param documents: List of documents to store
        :type documents: list[dict]
        :return: Number of documents inserted
        :rtype: int
        """
        if self.db is None or not documents:
            return 0
        try:
            now = datetime.now(timezone.utc)
            for doc in documents:
                doc["ingested_at"] = now
            result = self.db[collection].insert_many(documents)
            count = len(result.inserted_ids)
            pipeline_logger.info(f"Stored {count} documents in MongoDB:{collection}")
            return count
        except Exception as e:
            pipeline_logger.warning(f"Failed to store documents in {collection}: {e}")
            return 0

    def find_documents(self, collection: str, query: dict, limit: int = 0) -> list[dict]:
        """Query documents from a MongoDB collection.

        :param collection: Collection name
        :type collection: str
        :param query: MongoDB query filter
        :type query: dict
        :param limit: Maximum documents to return (0 = unlimited)
        :type limit: int
        :return: List of matching documents
        :rtype: list[dict]
        """
        if self.db is None:
            return []
        try:
            cursor = self.db[collection].find(query).limit(limit)
            return list(cursor)
        except Exception as e:
            pipeline_logger.warning(f"Failed to query {collection}: {e}")
            return []

    def close(self):
        """Close the MongoDB connection."""
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None
