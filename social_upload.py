"""
social_upload.py — publish a finished video as a Facebook Page Reel
and/or an Instagram Reel via the Meta Graph API.

Requires (see setup notes):
  - A Facebook Page + linked Instagram Business account
  - A Meta Developer App (Development Mode is fine for your own accounts)
  - A long-lived Page Access Token with pages_show_list,
    pages_read_engagement, pages_manage_posts permissions
  - Your Page ID and Instagram Business Account ID

Facebook accepts a direct file upload (via rupload.facebook.com).
Instagram requires a PUBLIC video_url instead — it fetches the file
itself — so this module temporarily hosts the file on litterbox.catbox.moe
(free, no signup, auto-expires) just long enough for Instagram to grab it.
"""

import os
import time
import requests

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


# ── Facebook Page Reels ──────────────────────────────────────────────
def upload_facebook_reel(page_id: str, page_access_token: str, video_path: str,
                          title: str = "", description: str = ""):
    """Publishes video_path as a Reel on the given Facebook Page. Returns (ok, video_id_or_error)."""
    try:
        # 1. Start the upload session
        start_resp = requests.post(
            f"{GRAPH_BASE}/{page_id}/video_reels",
            data={"upload_phase": "start", "access_token": page_access_token},
            timeout=30
        )
        start_resp.raise_for_status()
        start_data = start_resp.json()
        video_id = start_data.get("video_id")
        upload_url = start_data.get("upload_url")
        if not video_id or not upload_url:
            return False, f"لم يتم استلام video_id/upload_url: {start_data}"

        # 2. Upload the raw video bytes to the returned rupload.facebook.com URL
        file_size = os.path.getsize(video_path)
        with open(video_path, "rb") as f:
            upload_resp = requests.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {page_access_token}",
                    "offset": "0",
                    "file_size": str(file_size),
                },
                data=f,
                timeout=300
            )
        upload_resp.raise_for_status()

        # 3. Finish — publish the Reel
        finish_resp = requests.post(
            f"{GRAPH_BASE}/{page_id}/video_reels",
            data={
                "upload_phase": "finish",
                "video_id": video_id,
                "title": title,
                "description": description,
                "video_state": "PUBLISHED",
                "access_token": page_access_token,
            },
            timeout=60
        )
        finish_resp.raise_for_status()
        finish_data = finish_resp.json()
        if finish_data.get("success"):
            return True, video_id
        return False, f"فشل النشر: {finish_data}"

    except requests.exceptions.RequestException as e:
        return False, f"خطأ في اتصال Facebook: {e}"
    except Exception as e:
        return False, f"خطأ غير متوقع: {e}"


# ── Temporary public hosting for Instagram (needs a public video_url) ──
def _host_temporarily(video_path: str, expiry: str = "1h") -> str:
    """Uploads to litterbox.catbox.moe, returns a public URL that auto-expires. Raises on failure."""
    with open(video_path, "rb") as f:
        resp = requests.post(
            "https://litterbox.catbox.moe/resources/internals/api.php",
            data={"reqtype": "fileupload", "time": expiry},
            files={"fileToUpload": f},
            timeout=180
        )
    resp.raise_for_status()
    url = resp.text.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"فشل الاستضافة المؤقتة: {url}")
    return url


# ── Instagram Reels ──────────────────────────────────────────────────
def upload_instagram_reel(ig_user_id: str, access_token: str, video_path: str,
                           caption: str = "", share_to_feed: bool = True,
                           max_wait_seconds: int = 240):
    """Publishes video_path as an Instagram Reel. Returns (ok, media_id_or_error)."""
    try:
        public_url = _host_temporarily(video_path)

        # 1. Create the Reels container
        create_resp = requests.post(
            f"{GRAPH_BASE}/{ig_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": public_url,
                "caption": caption,
                "share_to_feed": str(share_to_feed).lower(),
                "access_token": access_token,
            },
            timeout=30
        )
        create_resp.raise_for_status()
        creation_id = create_resp.json().get("id")
        if not creation_id:
            return False, f"لم يتم إنشاء الحاوية: {create_resp.json()}"

        # 2. Poll until Instagram finishes processing the video
        waited = 0
        poll_interval = 10
        while waited < max_wait_seconds:
            status_resp = requests.get(
                f"{GRAPH_BASE}/{creation_id}",
                params={"fields": "status_code", "access_token": access_token},
                timeout=30
            )
            status_resp.raise_for_status()
            status_code = status_resp.json().get("status_code")
            if status_code == "FINISHED":
                break
            if status_code == "ERROR":
                return False, "فشلت معالجة الفيديو على انستغرام (status_code=ERROR)"
            time.sleep(poll_interval)
            waited += poll_interval
        else:
            return False, "انتهت مهلة الانتظار لمعالجة الفيديو على انستغرام"

        # 3. Publish
        publish_resp = requests.post(
            f"{GRAPH_BASE}/{ig_user_id}/media_publish",
            data={"creation_id": creation_id, "access_token": access_token},
            timeout=30
        )
        publish_resp.raise_for_status()
        media_id = publish_resp.json().get("id")
        if media_id:
            return True, media_id
        return False, f"فشل النشر: {publish_resp.json()}"

    except requests.exceptions.RequestException as e:
        return False, f"خطأ في اتصال Instagram: {e}"
    except Exception as e:
        return False, f"خطأ غير متوقع: {e}"
