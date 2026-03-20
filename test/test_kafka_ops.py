"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Unit tests for Kafka producer and consumer
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

"""

import json
from unittest.mock import MagicMock, call, patch

import pytest

from modules.db_ops.kafka_ops import TOPICS, KafkaConsumerClient, KafkaProducerClient


class TestKafkaTopics:
    """Tests for Kafka topic definitions."""

    def test_topics_contains_required_keys(self):
        assert "prices" in TOPICS
        assert "fundamentals" in TOPICS
        assert "fx" in TOPICS
        assert "esg" in TOPICS

    def test_topic_naming_convention(self):
        for key, topic in TOPICS.items():
            assert "." in topic, f"Topic {key} should use dot notation"


class TestKafkaProducerClient:
    """Tests for KafkaProducerClient."""

    def test_init_default_servers(self):
        client = KafkaProducerClient()
        assert client.bootstrap_servers == "localhost:9092"
        assert client._producer is None

    def test_init_custom_servers(self):
        client = KafkaProducerClient(bootstrap_servers="kafka:29092")
        assert client.bootstrap_servers == "kafka:29092"

    @patch("modules.db_ops.kafka_ops.KAFKA_AVAILABLE", False)
    def test_producer_returns_none_when_kafka_unavailable(self):
        client = KafkaProducerClient()
        assert client.producer is None

    @patch("modules.db_ops.kafka_ops.KAFKA_AVAILABLE", True)
    @patch("modules.db_ops.kafka_ops.Producer")
    def test_producer_lazy_init_success(self, mock_producer_cls):
        mock_instance = MagicMock()
        mock_producer_cls.return_value = mock_instance

        client = KafkaProducerClient()
        producer = client.producer
        assert producer is not None
        mock_producer_cls.assert_called_once()

    @patch("modules.db_ops.kafka_ops.KAFKA_AVAILABLE", True)
    @patch("modules.db_ops.kafka_ops.Producer")
    def test_producer_connection_failure(self, mock_producer_cls):
        mock_producer_cls.side_effect = Exception("Broker unavailable")
        client = KafkaProducerClient()
        assert client.producer is None

    def test_publish_when_producer_is_none(self):
        client = KafkaProducerClient()
        client._producer = None
        client.publish("test.topic", "KEY", {"data": 1})

    @patch("modules.db_ops.kafka_ops.KAFKA_AVAILABLE", True)
    @patch("modules.db_ops.kafka_ops.Producer")
    def test_publish_success(self, mock_producer_cls):
        mock_instance = MagicMock()
        mock_producer_cls.return_value = mock_instance

        client = KafkaProducerClient()
        _ = client.producer
        client.publish("market.prices", "AAPL", {"close": 150.0})

        mock_instance.produce.assert_called_once()
        args = mock_instance.produce.call_args
        assert args.kwargs["topic"] == "market.prices"
        assert args.kwargs["key"] == b"AAPL"

    @patch("modules.db_ops.kafka_ops.KAFKA_AVAILABLE", True)
    @patch("modules.db_ops.kafka_ops.Producer")
    def test_publish_batch(self, mock_producer_cls):
        mock_instance = MagicMock()
        mock_producer_cls.return_value = mock_instance

        client = KafkaProducerClient()
        _ = client.producer
        events = [
            {"symbol": "AAPL", "esg": 17.2},
            {"symbol": "MSFT", "esg": 22.1},
        ]
        client.publish_batch("esg.scores", events)
        assert mock_instance.produce.call_count == 2
        mock_instance.flush.assert_called_once()

    def test_publish_batch_empty_events(self):
        client = KafkaProducerClient()
        client._producer = MagicMock()
        client.publish_batch("test.topic", [])

    def test_flush_when_no_producer(self):
        client = KafkaProducerClient()
        client.flush()

    def test_close_resets_producer(self):
        client = KafkaProducerClient()
        client._producer = MagicMock()
        client.close()
        assert client._producer is None


class TestKafkaConsumerClient:
    """Tests for KafkaConsumerClient."""

    def test_init_defaults(self):
        client = KafkaConsumerClient()
        assert client.bootstrap_servers == "localhost:9092"
        assert client.group_id == "cw1-pipeline-consumer"
        assert len(client.topics) == len(TOPICS)

    def test_init_custom_topics(self):
        client = KafkaConsumerClient(topics=["custom.topic"])
        assert client.topics == ["custom.topic"]

    @patch("modules.db_ops.kafka_ops.KAFKA_AVAILABLE", False)
    def test_consumer_returns_none_when_unavailable(self):
        client = KafkaConsumerClient()
        assert client.consumer is None

    def test_consume_when_consumer_is_none(self):
        client = KafkaConsumerClient()
        client._consumer = None
        callback = MagicMock()
        client.consume(callback)
        callback.assert_not_called()

    def test_close_resets_consumer(self):
        client = KafkaConsumerClient()
        mock_consumer = MagicMock()
        client._consumer = mock_consumer
        client.close()
        mock_consumer.close.assert_called_once()
        assert client._consumer is None

    def test_consumer_creation_failure_sets_none(self):
        """Consumer property handles init failure gracefully."""
        from modules.db_ops.kafka_ops import KafkaConsumerClient

        with patch("modules.db_ops.kafka_ops.KAFKA_AVAILABLE", True), \
             patch("modules.db_ops.kafka_ops.Consumer", side_effect=Exception("no broker")):
            client = KafkaConsumerClient(bootstrap_servers="invalid:0000", topics=["test"])
            result = client.consumer
            assert result is None

    def test_consume_with_messages(self):
        """consume() processes valid messages and counts them."""
        from modules.db_ops.kafka_ops import KafkaConsumerClient

        mock_msg = MagicMock()
        mock_msg.error.return_value = None
        mock_msg.topic.return_value = "test.topic"
        mock_msg.value.return_value = b'{"key": "val"}'

        client = KafkaConsumerClient()
        mock_consumer = MagicMock()
        mock_consumer.poll.side_effect = [mock_msg, None]
        client._consumer = mock_consumer

        callback = MagicMock()
        client.consume(callback, max_messages=5)
        callback.assert_called_once_with("test.topic", {"key": "val"})

    def test_consume_skips_error_messages(self):
        """consume() skips messages with errors."""
        from modules.db_ops.kafka_ops import KafkaConsumerClient

        mock_err = MagicMock()
        mock_err.code.return_value = -1  # not PARTITION_EOF

        mock_msg = MagicMock()
        mock_msg.error.return_value = mock_err

        client = KafkaConsumerClient()
        mock_consumer = MagicMock()
        mock_consumer.poll.side_effect = [mock_msg, None]
        client._consumer = mock_consumer

        callback = MagicMock()
        client.consume(callback)
        callback.assert_not_called()

    def test_consume_handles_callback_exception(self):
        """consume() catches exceptions from callback processing."""
        from modules.db_ops.kafka_ops import KafkaConsumerClient

        mock_msg = MagicMock()
        mock_msg.error.return_value = None
        mock_msg.value.return_value = b"not json"  # will fail json.loads

        client = KafkaConsumerClient()
        mock_consumer = MagicMock()
        mock_consumer.poll.side_effect = [mock_msg, None]
        client._consumer = mock_consumer

        callback = MagicMock()
        client.consume(callback)
        callback.assert_not_called()
