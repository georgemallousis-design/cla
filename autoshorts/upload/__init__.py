"""Publishing finished videos.

Contract
--------
Each uploader exposes ``upload(cfg, video_path, meta) -> UploadResult`` where ``meta`` is
the platform dict produced by autoshorts.metadata.build_metadata(...)[platform].
Uploaders never raise for API refusals; they return UploadResult(ok=False, error=...).
Missing optional dependencies or credentials produce a clear AutoShortsError with setup steps.

* upload.youtube  - YouTube Data API v3 (OAuth desktop flow, resumable upload)
* upload.tiktok   - TikTok Content Posting API (inbox draft or direct post)
"""
