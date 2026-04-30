"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Apache Kafka producer and consumer for event-driven ingestion
Project : Systematic Equity Pipeline - Flow-Based Multi-Factor Equity Strategy

Implements Kafka-based event streaming for decoupling data ingestion
(producers) from processing/storage (consumers).  The assignment spec
states: "To handle data ingestion and processing, you can leverage on
Apache Kafka."

Topics:
  - market.prices        — daily OHLCV price events
  - market.fundamentals  — quarterly/annual financial data events
  - market.fx            — FX rate events
  - market.macro         — VIX, risk-free rate, benchmark events
  - esg.scores           — ESG sustainability score events

"""

import json
import os
from typing import Callable

from modules.utils.info_logger import pipeline_logger

try:
    from confluent_kafka import Consumer, KafkaError, Producer

    KAFKA_AVAILABLE = True
except ImportError:
    Producer = None
    Consumer = None
    KafkaError = None
    KAFKA_AVAILABLE = False

# Default Kafka topics used by the pipeline
TOPICS = {
    "prices": "market.prices",
    "fundamentals": "market.fundamentals",
    "fx": "market.fx",
    "macro": "market.macro",
    "esg": "esg.scores",
    "sentiment": "market.sentiment",
}


class KafkaProducerClient:
    """Kafka producer for publishing pipeline data events.

    Publishes serialised JSON messages to Kafka topics, keyed by
    ticker symbol for partition affinity.

    :param bootstrap_servers: Kafka bootstrap server address
    :type bootstrap_servers: str
    """

    def __init__(self, bootstrap_servers: str = None):
        self.bootstrap_servers = bootstrap_servers or os.environ.get(
            "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
        )
        self._producer = None

    @property
    def producer(self):
        """Lazy-initialise Kafka producer.

        :return: Confluent Kafka producer or None
        :rtype: Producer or None
        """
        if self._producer is None:
            if not KAFKA_AVAILABLE:
                pipeline_logger.warning("confluent-kafka not installed — Kafka disabled")
                return None
            try:
                self._producer = Producer(
                    {
                        "bootstrap.servers": self.bootstrap_servers,
                        "client.id": "cw1-pipeline-producer",
                        "acks": "all",
                    }
                )
                pipeline_logger.info(f"Kafka producer connected to {self.bootstrap_servers}")
            except Exception as e:
                pipeline_logger.warning(
                    f"Could not connect to Kafka: {e}. " "Event streaming will be skipped."
                )
                self._producer = None
        return self._producer

    def publish(self, topic: str, key: str, value: dict):
        """Publish a single event to a Kafka topic.

        :param topic: Target Kafka topic
        :type topic: str
        :param key: Message key (typically ticker symbol)
        :type key: str
        :param value: Event payload as dictionary
        :type value: dict
        """
        if self.producer is None:
            return
        try:
            self.producer.produce(
                topic=topic,
                key=key.encode("utf-8"),
                value=json.dumps(value, default=str).encode("utf-8"),
            )
            self.producer.poll(0)
        except Exception as e:
            pipeline_logger.warning(f"Failed to publish to {topic}/{key}: {e}")

    def publish_batch(self, topic: str, events: list[dict], key_field: str = "symbol"):
        """Publish a batch of events to a Kafka topic.

        :param topic: Target Kafka topic
        :type topic: str
        :param events: List of event payloads
        :type events: list[dict]
        :param key_field: Dict key to use as message key
        :type key_field: str
        """
        if self.producer is None or not events:
            return
        for event in events:
            key = str(event.get(key_field, "unknown"))
            self.publish(topic, key, event)
        self.producer.flush(timeout=10)

    def flush(self):
        """Flush all pending messages."""
        if self._producer is not None:
            self._producer.flush(timeout=10)

    def close(self):
        """Flush and close the Kafka producer."""
        self.flush()
        self._producer = None


class KafkaConsumerClient:
    """Kafka consumer for reading pipeline data events.

    Subscribes to one or more topics and processes messages via
    a user-supplied callback function.

    :param bootstrap_servers: Kafka bootstrap server address
    :type bootstrap_servers: str
    :param group_id: Consumer group identifier
    :type group_id: str
    :param topics: List of topics to subscribe to
    :type topics: list[str]
    """

    def __init__(
        self,
        bootstrap_servers: str = None,
        group_id: str = "cw1-pipeline-consumer",
        topics: list[str] = None,
    ):
        self.bootstrap_servers = bootstrap_servers or os.environ.get(
            "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
        )
        self.group_id = group_id
        self.topics = topics or list(TOPICS.values())
        self._consumer = None

    @property
    def consumer(self):
        """Lazy-initialise Kafka consumer.

        :return: Confluent Kafka consumer or None
        :rtype: Consumer or None
        """
        if self._consumer is None:
            if not KAFKA_AVAILABLE:
                pipeline_logger.warning("confluent-kafka not installed — Kafka consumer disabled")
                return None
            try:
                self._consumer = Consumer(
                    {
                        "bootstrap.servers": self.bootstrap_servers,
                        "group.id": self.group_id,
                        "auto.offset.reset": "earliest",
                    }
                )
                self._consumer.subscribe(self.topics)
                pipeline_logger.info(f"Kafka consumer subscribed to {self.topics}")
            except Exception as e:
                pipeline_logger.warning(f"Could not create Kafka consumer: {e}")
                self._consumer = None
        return self._consumer

    def consume(self, callback: Callable[[str, dict], None], max_messages: int = 100, timeout: float = 1.0):
        """Consume messages and pass to callback.

        :param callback: Function(topic, payload) called per message
        :type callback: Callable
        :param max_messages: Maximum messages to consume
        :type max_messages: int
        :param timeout: Poll timeout in seconds
        :type timeout: float
        """
        if self.consumer is None:
            return
        count = 0
        while count < max_messages:
            msg = self.consumer.poll(timeout)
            if msg is None:
                break
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    pipeline_logger.warning(f"Kafka consumer error: {msg.error()}")
                continue
            try:
                topic = msg.topic()
                payload = json.loads(msg.value().decode("utf-8"))
                callback(topic, payload)
                count += 1
            except Exception as e:
                pipeline_logger.warning(f"Error processing Kafka message: {e}")

    def close(self):
        """Close the Kafka consumer."""
        if self._consumer is not None:
            self._consumer.close()
            self._consumer = None
