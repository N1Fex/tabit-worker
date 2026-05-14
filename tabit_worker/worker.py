from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tabit_worker.conversion import DEFAULT_BACKEND, DEFAULT_BPM, add_common_arguments, convert_audio_bytes

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional import during bootstrap
    load_dotenv = None


DEFAULT_AUDIO_BUCKET = "audio-temp"
DEFAULT_RESULT_BUCKET = "musicxml"
DEFAULT_JOB_QUEUE = "queue.convert.job"
DEFAULT_RESULT_QUEUE = "queue.result"


@dataclass(frozen=True)
class WorkerConfig:
    rabbitmq_url: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_secure: bool
    source_bucket: str = DEFAULT_AUDIO_BUCKET
    result_bucket: str = DEFAULT_RESULT_BUCKET
    job_queue: str = DEFAULT_JOB_QUEUE
    result_queue: str = DEFAULT_RESULT_QUEUE
    bpm: str = DEFAULT_BPM
    backend: str = DEFAULT_BACKEND
    ffmpeg_path: str | None = None


class MinioStorage:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, secure: bool) -> None:
        try:
            from minio import Minio
        except ImportError as error:
            raise RuntimeError("The 'minio' package is required to run the worker.") from error

        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    def get_bytes(self, bucket: str, object_key: str) -> bytes:
        response = self._client.get_object(bucket, object_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def put_bytes(self, bucket: str, object_key: str, data: bytes, content_type: str) -> None:
        from io import BytesIO

        self._client.put_object(
            bucket,
            object_key,
            data=BytesIO(data),
            length=len(data),
            content_type=content_type,
        )


class RabbitPublisher:
    def __init__(self, channel, queue_name: str) -> None:
        self._channel = channel
        self._queue_name = queue_name
        self._declare_queue(queue_name)

    def publish(self, payload: dict) -> None:
        self._channel.basic_publish(
            exchange="",
            routing_key=self._queue_name,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            properties=self._build_properties(),
        )

    def _declare_queue(self, queue_name: str) -> None:
        self._channel.queue_declare(queue=queue_name, durable=True)

    def _build_properties(self):
        try:
            import pika
        except ImportError as error:
            raise RuntimeError("The 'pika' package is required to run the worker.") from error
        return pika.BasicProperties(content_type="application/json", delivery_mode=2)


class JobProcessor:
    def __init__(
        self,
        storage: MinioStorage,
        result_publisher: RabbitPublisher,
        source_bucket: str = DEFAULT_AUDIO_BUCKET,
        result_bucket: str = DEFAULT_RESULT_BUCKET,
        bpm: str = DEFAULT_BPM,
        backend: str = DEFAULT_BACKEND,
        ffmpeg_path: str | None = None,
    ) -> None:
        self._storage = storage
        self._result_publisher = result_publisher
        self._source_bucket = source_bucket
        self._result_bucket = result_bucket
        self._bpm = bpm
        self._backend = backend
        self._ffmpeg_path = ffmpeg_path

    def process_message(self, body: bytes) -> dict:
        payload = json.loads(body.decode("utf-8"))
        return self.process_payload(payload)

    def process_payload(self, payload: dict) -> dict:
        job_id = str(payload["id"])
        user_id = str(payload["userId"])
        object_key = str(payload["objectKey"])
        job_uuid = str(payload["uuid"])
        result_object_key = f"{user_id}/{job_uuid}.musicxml"

        processing = _build_result_payload(job_id, user_id, None, "PROCESSING")
        self._result_publisher.publish(processing)
        print(f"Job started: {processing}")

        try:
            audio_bytes = self._storage.get_bytes(self._source_bucket, object_key)
            suffix = _infer_audio_suffix(object_key, audio_bytes)
            musicxml_bytes, _bpm = convert_audio_bytes(
                audio_bytes,
                suffix=suffix,
                bpm_argument=self._bpm,
                backend=self._backend,
                ffmpeg_path=self._ffmpeg_path,
            )
            self._storage.put_bytes(
                self._result_bucket,
                result_object_key,
                musicxml_bytes,
                content_type="application/vnd.recordare.musicxml+xml",
            )
            result = _build_result_payload(job_id, user_id, result_object_key, "COMPLETED")
        except Exception as error:
            print(error)
            result = _build_result_payload(job_id, user_id, None, "FAILED")

        self._result_publisher.publish(result)
        return result


class RabbitWorker:
    def __init__(self, config: WorkerConfig) -> None:
        self._config = config

    def run(self) -> None:
        try:
            import pika
        except ImportError as error:
            raise RuntimeError("The 'pika' package is required to run the worker.") from error

        connection = pika.BlockingConnection(pika.URLParameters(self._config.rabbitmq_url))
        channel = connection.channel()
        channel.queue_declare(queue=self._config.job_queue, durable=True)
        channel.queue_declare(queue=self._config.result_queue, durable=True)
        channel.basic_qos(prefetch_count=1)

        storage = MinioStorage(
            endpoint=self._config.minio_endpoint,
            access_key=self._config.minio_access_key,
            secret_key=self._config.minio_secret_key,
            secure=self._config.minio_secure,
        )
        publisher = RabbitPublisher(channel, self._config.result_queue)
        processor = JobProcessor(
            storage=storage,
            result_publisher=publisher,
            source_bucket=self._config.source_bucket,
            result_bucket=self._config.result_bucket,
            bpm=self._config.bpm,
            backend=self._config.backend,
            ffmpeg_path=self._config.ffmpeg_path,
        )

        def callback(ch, method, _properties, body: bytes) -> None:
            try:
                result = processor.process_message(body)
                print(f"Processed job result: {result}")
            except Exception as e:
                print(f"Error: ", e)
            finally:
                ch.basic_ack(delivery_tag=method.delivery_tag)

        channel.basic_consume(queue=self._config.job_queue, on_message_callback=callback)
        print(f"Listening on {self._config.job_queue}")
        try:
            channel.start_consuming()
        finally:
            connection.close()


def build_parser() -> argparse.ArgumentParser:
    _load_environment()
    parser = argparse.ArgumentParser(description="Run the RabbitMQ to MinIO conversion worker.")
    parser.add_argument("--rabbitmq-url", default=os.getenv("RABBITMQ_URL"), help="RabbitMQ connection URL.")
    parser.add_argument("--minio-endpoint", default=os.getenv("MINIO_ENDPOINT"), help="MinIO endpoint, e.g. localhost:9000.")
    parser.add_argument("--minio-access-key", default=os.getenv("MINIO_ACCESS_KEY"), help="MinIO access key.")
    parser.add_argument("--minio-secret-key", default=os.getenv("MINIO_SECRET_KEY"), help="MinIO secret key.")
    parser.add_argument(
        "--minio-secure",
        default=os.getenv("MINIO_SECURE", "false"),
        help="Use HTTPS for MinIO: true/false. Default: false.",
    )
    parser.add_argument("--source-bucket", default=os.getenv("MINIO_BUCKET_AUDIO_TEMP", DEFAULT_AUDIO_BUCKET))
    parser.add_argument("--result-bucket", default=os.getenv("MINIO_BUCKET_MUSICXML", DEFAULT_RESULT_BUCKET))
    parser.add_argument("--job-queue", default=os.getenv("RABBITMQ_JOB_QUEUE", DEFAULT_JOB_QUEUE))
    parser.add_argument("--result-queue", default=os.getenv("RABBITMQ_RESULT_QUEUE", DEFAULT_RESULT_QUEUE))
    add_common_arguments(parser)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    missing = [
        name
        for name, value in {
            "--rabbitmq-url": args.rabbitmq_url,
            "--minio-endpoint": args.minio_endpoint,
            "--minio-access-key": args.minio_access_key,
            "--minio-secret-key": args.minio_secret_key,
        }.items()
        if not value
    ]
    if missing:
        parser.error(f"Missing required settings: {', '.join(missing)}")

    config = WorkerConfig(
        rabbitmq_url=args.rabbitmq_url,
        minio_endpoint=args.minio_endpoint,
        minio_access_key=args.minio_access_key,
        minio_secret_key=args.minio_secret_key,
        minio_secure=_parse_bool(args.minio_secure),
        source_bucket=args.source_bucket,
        result_bucket=args.result_bucket,
        job_queue=args.job_queue,
        result_queue=args.result_queue,
        bpm=args.bpm,
        backend=args.backend,
        ffmpeg_path=args.ffmpeg,
    )
    RabbitWorker(config).run()
    return 0


def _load_environment() -> None:
    if load_dotenv is not None:
        load_dotenv()


def _parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "y", "on"}


def _infer_audio_suffix(object_key: str, data: bytes) -> str:
    suffix = Path(object_key).suffix.lower()
    if suffix in {".wav", ".mp3"}:
        return suffix
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return ".wav"
    if data.startswith(b"ID3"):
        return ".mp3"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return ".mp3"
    raise ValueError("Unable to determine input audio format from object key or file header.")


def _build_result_payload(
    job_id: str,
    user_id: str,
    result_object_key: str | None,
    status: str,
) -> dict:
    return {
        "id": job_id,
        "userId": user_id,
        "resultObjectKey": result_object_key,
        "status": status,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
