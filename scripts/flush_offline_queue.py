"""Scheduled entrypoint for offline queue flushing."""

import asyncio
import json

import memory_service


def main() -> None:
    memory_service.CLIENT.configure_from_env()
    result = asyncio.run(memory_service.flush_offline_queue())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
