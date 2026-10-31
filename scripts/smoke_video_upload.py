"""End-to-end check of a running deployment's video path against real storage.

    python -m scripts.smoke_video_upload http://localhost:8000 local-ai-key

Registers an owner (or a worker, if the owner exists), uploads a small file
through the presigned URL exactly as a browser would, confirms it, then plays
the AI worker: claims the job, downloads the file, reports progress, delivers
an analysis. Exits non-zero at the first step that does not behave.
"""

import secrets
import sys

import httpx

PAYLOAD = b"\x00\x00\x00\x18ftypmp42" + secrets.token_bytes(4096)
ANALYSIS = {
    "video_name": "smoke.mp4", "duration_sec": 1.0, "frames_analyzed": 24, "max_cattle": 1, "max_people": 0,
    "alerts": [], "avg_confidence": 0.9, "outside_ratio": 0.0,
}


def check(response: httpx.Response, expected: int, step: str) -> httpx.Response:
    if response.status_code != expected:
        print(f"FAIL {step}: {response.status_code} {response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)
    print(f"ok   {step}")
    return response


def main(base_url: str, ai_key: str) -> int:
    with httpx.Client(base_url=base_url, timeout=30) as api:
        check(api.get("/health"), 200, "health")
        token = check(api.post("/api/auth/register", json={
            "email": f"smoke-{secrets.token_hex(4)}@smartcattle.example", "password": secrets.token_urlsafe(16),
            "full_name": "Smoke Test",
        }), 201, "register").json()["access_token"]
        user = {"Authorization": f"Bearer {token}"}
        ai = {"X-API-Key": ai_key}

        ticket = check(api.post("/api/videos/uploads", headers=user, json={
            "file_name": "smoke.mp4", "size_bytes": len(PAYLOAD), "content_type": "video/mp4",
        }), 201, "start upload").json()
        check(httpx.request(ticket["upload_method"], ticket["upload_url"], headers=ticket["upload_headers"],
                            content=PAYLOAD, timeout=30), 200, "PUT to presigned URL")
        upload_id = ticket["upload"]["id"]
        check(api.post(f"/api/videos/uploads/{upload_id}/complete", headers=user), 200, "complete upload")

        # Drain anything older in the queue until our video comes up.
        for _ in range(20):
            job = check(api.post("/api/ai/video-jobs/claim", headers=ai), 200, "claim").json()
            if job["job_id"] == upload_id:
                break
            api.post(f"/api/ai/video-jobs/{job['job_id']}/fail", headers=ai, json={"error": "smoke test drain"})
        else:
            print("FAIL claim: our upload never came up", file=sys.stderr)
            return 1
        downloaded = check(httpx.get(job["download_url"], timeout=30), 200, "download").content
        if downloaded != PAYLOAD:
            print("FAIL download: content differs from what was uploaded", file=sys.stderr)
            return 1
        check(api.put(f"/api/ai/video-jobs/{upload_id}/progress", headers=ai,
                      json={"frames_processed": 12, "frames_total": 24}), 204, "progress")
        done = check(api.post(f"/api/ai/video-jobs/{upload_id}/complete", headers=ai, json=ANALYSIS),
                     200, "complete job").json()
        if done["status"] != "completed" or done["analysis_id"] is None:
            print(f"FAIL final state: {done}", file=sys.stderr)
            return 1
    print("video path works end to end")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
