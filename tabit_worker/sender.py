import json
import pika

payload = {
    "jobId": "job-1",
    "userId": "user-123",
    "objectKey": "550e8400-e29b-41d4-a716-446655440000.mp3",
    "uuid": "550e8400-e29b-41d4-a716-446655440000",
}

connection = pika.BlockingConnection(
    pika.URLParameters("amqp://rmuser:rmpassword@localhost:5672/%2F")
)
channel = connection.channel()
channel.queue_declare(queue="queue.convert.job", durable=True)
channel.basic_publish(
    exchange="",
    routing_key="queue.convert.job",
    body=json.dumps(payload).encode("utf-8"),
    properties=pika.BasicProperties(
        content_type="application/json",
        delivery_mode=2,
    ),
)
connection.close()
print("sent")
