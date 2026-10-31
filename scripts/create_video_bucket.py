"""Creates the video bucket if it does not exist, waiting for storage to start.

Used by docker-compose against the local S3 stand-in: python -m scripts.create_video_bucket
"""

import sys
import time

from botocore.exceptions import EndpointConnectionError

from app.core.config import Settings
from app.services.video_storage import S3VideoStorage

ATTEMPTS = 30


def main() -> int:
    settings = Settings()
    if not settings.video_storage_enabled:
        print("VIDEO_STORAGE_* is not set; nothing to create")
        return 0
    storage = S3VideoStorage(settings)
    for _ in range(ATTEMPTS):
        try:
            created = storage.ensure_bucket()
        except EndpointConnectionError:
            time.sleep(1)
            continue
        print(f"Bucket {settings.video_storage_bucket} {'created' if created else 'already exists'}")
        return 0
    print("The storage endpoint never answered", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
