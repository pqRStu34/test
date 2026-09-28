import os
import sys
import logging
import requests
from typing import Dict, Any, List, Optional, Set

logger = logging.getLogger("pocketbase_client")

class PocketBaseClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8090", admin_email: str = "", admin_password: str = ""):
        self.base_url = base_url.rstrip("/")
        self.admin_email = admin_email or os.environ.get("POCKETBASE_ADMIN_EMAIL", "admin@pocketbase.local")
        self.admin_password = admin_password or os.environ.get("POCKETBASE_ADMIN_PASSWORD", "admin123456789")
        self.token = ""
        self.collection_name = "completed_syncs"

    def authenticate_admin(self) -> bool:
        try:
            url = f"{self.base_url}/api/admins/auth-with-password"
            resp = requests.post(url, json={"identity": self.admin_email, "password": self.admin_password}, timeout=5)
            if resp.status_code == 200:
                self.token = resp.json().get("token", "")
                return True
            url2 = f"{self.base_url}/api/collections/_superusers/auth-with-password"
            resp2 = requests.post(url2, json={"identity": self.admin_email, "password": self.admin_password}, timeout=5)
            if resp2.status_code == 200:
                self.token = resp2.json().get("token", "")
                return True
        except Exception as e:
            logger.debug(f"PocketBase admin auth error: {e}")
        return False

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def ensure_collection(self) -> bool:
        self.authenticate_admin()
        url = f"{self.base_url}/api/collections/{self.collection_name}"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=5)
            if resp.status_code == 200:
                return True
        except Exception as e:
            logger.warning(f"Error checking PocketBase collection: {e}")

        create_url = f"{self.base_url}/api/collections"
        schema_payload = {
            "name": self.collection_name,
            "type": "base",
            "schema": [
                {"name": "message_id", "type": "text", "required": True, "unique": True},
                {"name": "file_name", "type": "text"},
                {"name": "anime_id", "type": "text"},
                {"name": "language", "type": "text"},
                {"name": "quality", "type": "text"},
                {"name": "episode_number", "type": "number"},
                {"name": "thumbnail", "type": "text"},
                {"name": "stream_link", "type": "text"},
                {"name": "is_mp4", "type": "bool"},
                {"name": "ia_synced", "type": "bool"},
                {"name": "channel_id", "type": "text"},
                {"name": "ia_item", "type": "text"},
                {"name": "file_size", "type": "number"},
                {"name": "synced_at", "type": "text"}
            ],
            "listRule": "",
            "viewRule": "",
            "createRule": "",
            "updateRule": "",
            "deleteRule": ""
        }
        try:
            create_resp = requests.post(create_url, headers=self._headers(), json=schema_payload, timeout=10)
            if create_resp.status_code in (200, 201):
                logger.info(f"Successfully created PocketBase collection '{self.collection_name}'.")
                return True
            else:
                logger.error(f"Failed to create PocketBase collection: {create_resp.status_code} {create_resp.text}")
        except Exception as e:
            logger.error(f"Failed to connect to PocketBase to create collection: {e}")
        return False

    def get_completed_message_ids(self) -> Set[str]:
        ids: Set[str] = set()
        page = 1
        per_page = 500

        while True:
            url = f"{self.base_url}/api/collections/{self.collection_name}/records?page={page}&perPage={per_page}&fields=message_id"
            try:
                resp = requests.get(url, headers=self._headers(), timeout=10)
                if resp.status_code != 200:
                    break
                data = resp.json()
                items = data.get("items", [])
                for item in items:
                    mid = str(item.get("message_id", "")).strip()
                    if mid:
                        ids.add(mid)
                if page >= data.get("totalPages", 1) or len(items) == 0:
                    break
                page += 1
            except Exception as e:
                logger.error(f"Error fetching completed message IDs from PocketBase: {e}")
                break

        return ids

    def record_completed_sync(self, data: Dict[str, Any]) -> bool:
        message_id = str(data.get("message_id", "")).strip()
        if not message_id:
            return False

        url_search = f"{self.base_url}/api/collections/{self.collection_name}/records?filter=(message_id='{message_id}')"
        try:
            resp = requests.get(url_search, headers=self._headers(), timeout=5)
            if resp.status_code == 200 and resp.json().get("totalItems", 0) > 0:
                record_id = resp.json()["items"][0]["id"]
                patch_url = f"{self.base_url}/api/collections/{self.collection_name}/records/{record_id}"
                patch_resp = requests.patch(patch_url, headers=self._headers(), json=data, timeout=5)
                return patch_resp.status_code == 200
        except Exception as e:
            logger.debug(f"Error checking existing record in PocketBase: {e}")

        url_create = f"{self.base_url}/api/collections/{self.collection_name}/records"
        try:
            resp = requests.post(url_create, headers=self._headers(), json=data, timeout=5)
            return resp.status_code in (200, 201)
        except Exception as e:
            logger.error(f"Failed to record sync in PocketBase: {e}")
            return False

    def query_by_anime_id(self, anime_id: str) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        page = 1
        per_page = 200

        while True:
            url = (
                f"{self.base_url}/api/collections/{self.collection_name}/records"
                f"?filter=(anime_id='{anime_id}')&sort=+episode_number&page={page}&perPage={per_page}"
            )
            try:
                resp = requests.get(url, headers=self._headers(), timeout=10)
                if resp.status_code != 200:
                    break
                data = resp.json()
                items = data.get("items", [])
                records.extend(items)
                if page >= data.get("totalPages", 1) or len(items) == 0:
                    break
                page += 1
            except Exception as e:
                logger.error(f"Error querying PocketBase for anime_id {anime_id}: {e}")
                break

        return records
