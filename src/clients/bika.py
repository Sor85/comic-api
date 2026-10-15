import hashlib
import hmac
import json
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import List, Dict, Any
from urllib.parse import urlparse, urlencode

from src.clients.base import BaseClient
from src.config import Config
from src.errors import ComicApiError
from src.metadata import comic_authors, comic_labels, comic_page_count


class BikaClient(BaseClient):
    def __init__(self, store=None):
        super().__init__()
        self.api_base = Config.BIKA_DEFAULT_API_BASE
        self._auth_lock = threading.RLock()
        self._favorite_pages = {}
        self.authorization = ""
        from src.storage import Store
        self.store = store or Store()
        data_dir = os.environ.get("DATA_DIR", "")
        if data_dir:
            self.token_file = os.path.join(data_dir, ".bika_token")
        else:
            self.token_file = str(Path(__file__).resolve().parents[2] / ".bika_token")
        self.credentials_file = os.path.join(os.path.dirname(self.token_file), ".bika_credentials.json")

        # Import once. An empty SQL record after logout must not resurrect legacy files.
        if self.store.account("bika") is None:
            legacy = {}
            if os.path.exists(self.credentials_file):
                with open(self.credentials_file, encoding="utf-8") as stream:
                    legacy.update(json.load(stream))
            if os.path.exists(self.token_file):
                legacy["token"] = Path(self.token_file).read_text().strip()
            legacy.setdefault("account", os.environ.get("BIKA_ACCOUNT", "").strip())
            legacy.setdefault("password", os.environ.get("BIKA_PASSWORD", ""))
            self.store.save_account("bika", legacy)

        stored_account, stored_password = self._read_persisted_credentials()
        self._account, self._password = stored_account, stored_password

        self.authorization = self._read_persisted_token()

    def _read_persisted_credentials(self):
        data = self.store.account("bika") or {}
        return data.get("account", ""), data.get("password", "")

    def _read_persisted_token(self) -> str:
        return (self.store.account("bika") or {}).get("token", "")

    def _persist_token(self, token: str) -> None:
        data = self.store.account("bika") or {}
        self.store.save_account("bika", dict(data, token=token))

    def _persist_credentials(self, account: str, password: str) -> None:
        data = self.store.account("bika") or {}
        self.store.save_account("bika", dict(data, account=account, password=password))

    def _clear_authorization_locked(self, expected_token: str = "") -> None:
        if expected_token and self.authorization not in ("", expected_token):
            return

        self.authorization = ""
        self._persist_token("")

    def _credentials_available(self) -> bool:
        return bool(self._account and self._password)

    def ensure_authenticated(self) -> None:
        if self.authorization:
            return

        with self._auth_lock:
            if self.authorization:
                return

            persisted_token = self._read_persisted_token()
            if persisted_token:
                self.authorization = persisted_token
                return

            if not self._credentials_available():
                raise ComicApiError("请先绑定图源账号", 401, "login_required", "bika")

            self._login_locked(self._account, self._password)

    def clean_path(self, path: str) -> str:
        """提取纯路径，包括后面的 Query String"""
        if path.startswith("http"):
            try:
                parsed = urlparse(path)
                query = f"?{parsed.query}" if parsed.query else ""
                path = f"{parsed.path}{query}"
            except Exception:
                pass
        return path.lstrip("/")

    def create_signature(self, path: str, timestamp: int, nonce: str, method: str) -> str:
        """
        hmac-sha256 签名计算：
        raw = (path + timestamp + nonce + method + api_key).lower()
        """
        raw = f"{path}{timestamp}{nonce}{method}{Config.BIKA_API_KEY}".lower()
        secret = Config.BIKA_SECRET_KEY.encode("utf-8")
        return hmac.new(secret, raw.encode("utf-8"), hashlib.sha256).hexdigest()

    def _send_bika_request(
        self,
        path: str,
        method: str = "GET",
        params: dict = None,
        json_body: dict = None,
        authorization: str = "",
        allow_redirects: bool = True,
    ):
        url = f"{self.api_base}{path}"

        # 如果存在 params，必须将其拼接到签名 path 的尾部
        sign_path = path
        if params:
            query_str = urlencode(params)
            if query_str:
                sign_path = f"{path}?{query_str}"

        cleaned_path = self.clean_path(sign_path)

        timestamp = int(time.time())
        nonce = uuid.uuid4().hex

        signature = self.create_signature(cleaned_path, timestamp, nonce, method)

        headers = {
            "api-key": Config.BIKA_API_KEY,
            "accept": "application/vnd.picacomic.com.v1+json",
            "app-channel": "3",
            "time": str(timestamp),
            "nonce": nonce,
            "signature": signature,
            "app-version": "2.2.1.3.3.4",
            "app-uuid": "defaultUuid",
            "app-platform": "android",
            "app-build-version": "45",
            "user-agent": "okhttp/3.8.1",
            "content-type": "application/json; charset=UTF-8",
            "image-quality": "original"
        }

        if authorization:
            headers["authorization"] = authorization

        kwargs = {
            "headers": headers,
            "timeout": 15,
            "allow_redirects": allow_redirects,
        }
        if params:
            kwargs["params"] = params

        if json_body is not None:
            kwargs["data"] = json.dumps(json_body).encode("utf-8")

        return self.request(method, url, **kwargs)

    @staticmethod
    def _response_error(res) -> str:
        try:
            return str(res.json().get("message", ""))
        except Exception:
            return ""

    @staticmethod
    def _is_auth_failure(res, err_msg: str) -> bool:
        return res.status_code == 401 or err_msg.lower() == "unauthorized"

    def _login_locked(self, account: str, password: str) -> str:
        res = self._send_bika_request(
            "auth/sign-in",
            method="POST",
            json_body={"email": account, "password": password},
            authorization="",
        )
        if res.status_code < 200 or res.status_code >= 300:
            raise ComicApiError("图源登录失败，请检查账号或稍后重试",
                                401 if res.status_code in (400, 401) else 502, "login_failed", "bika")

        token = res.json().get("data", {}).get("token", "")
        if not token:
            raise ComicApiError("登录响应缺少 Token", 502, "invalid_response", "bika")

        self.store.save_account("bika", {"account": account, "password": password, "token": token})
        self._account = account
        self._password = password
        self.authorization = token
        self._favorite_pages.clear()
        return token

    def _refresh_authorization(self, failed_token: str) -> None:
        with self._auth_lock:
            if self.authorization and self.authorization != failed_token:
                return

            persisted_token = self._read_persisted_token()
            if persisted_token and persisted_token != failed_token:
                self.authorization = persisted_token
                return

            if not self._credentials_available():
                self._clear_authorization_locked(failed_token)
                raise ComicApiError("登录已失效，请重新绑定图源账号", 401, "login_required", "bika")

            try:
                self._login_locked(self._account, self._password)
            except Exception:
                self._clear_authorization_locked(failed_token)
                raise

    def bika_request(
        self,
        path: str,
        method: str = "GET",
        params: dict = None,
        json_body: dict = None,
        _auth_retry: bool = False,
        _retry_safe: bool = True,
    ) -> Any:
        used_token = self.authorization
        res = self._send_bika_request(path, method, params, json_body, used_token,
                                      allow_redirects=_retry_safe)
        if res.status_code < 200 or res.status_code >= 300:
            err_msg = self._response_error(res)
            if self._is_auth_failure(res, err_msg):
                if used_token and not _auth_retry and _retry_safe:
                    self._refresh_authorization(used_token)
                    return self.bika_request(path, method, params, json_body, _auth_retry=True)

                if used_token:
                    with self._auth_lock:
                        self._clear_authorization_locked(used_token)

            raise ComicApiError(f"图源返回 HTTP {res.status_code}",
                                res.status_code if res.status_code in (401, 403, 404, 429) else 502,
                                "login_required" if res.status_code == 401 else "upstream_error", "bika")

        return res.json()

    def login(self, account: str, password: str) -> str:
        """登录哔咔"""
        account = account.strip()
        if not account or not password:
            raise ComicApiError("哔咔账号和密码不能为空", 422, "invalid_request", "bika")
        with self._auth_lock:
            return self._login_locked(account, password)

    @staticmethod
    def _favorite_id(comic_id: str) -> str:
        if not isinstance(comic_id, str) or not re.fullmatch(r"[0-9a-fA-F]{24}", comic_id):
            raise ComicApiError("漫画编号无效", 422, "invalid_request", "bika")
        return comic_id.lower()

    def _favorite_owner(self):
        return self.store.account_owner(self._account)

    def favorites(self, page=1, sort="dd") -> dict:
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 1000 or sort not in ("dd", "da", "ld", "vd"):
            raise ComicApiError("收藏查询参数无效", 422, "invalid_request", "bika")
        with self._auth_lock:
            self.ensure_authenticated()
            try:
                response = self.bika_request("users/favourite", params={"s": sort, "page": str(page)})
                data = response["data"]["comics"]
                if not isinstance(data, dict) or not isinstance(data.get("docs"), list):
                    raise ValueError()
                values = [data[key] for key in ("page", "limit", "total", "pages")]
                if any(isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit() for value in values):
                    raise ValueError()
                actual_page, limit, total, pages = map(int, values)
                if actual_page != page or not 1 <= limit <= 1000 or pages not in ({0, 1} if total == 0 else {math.ceil(total / limit)}):
                    raise ValueError()
                items = self._parse_comics_list(response)
                ids = [self._favorite_id(item["id"]) for item in items]
                if len(items) != len(data["docs"]) or len(set(ids)) != len(ids) or len(ids) != min(limit, max(0, total - (page - 1) * limit)):
                    raise ValueError()
                for item, comic_id in zip(items, ids):
                    item["id"] = comic_id
            except Exception as exc:
                self._favorite_pages.pop(sort, None)
                if isinstance(exc, ComicApiError) and exc.code != "invalid_request":
                    raise
                raise ComicApiError("哔咔收藏数据不完整，未作为空收藏处理", 502, "invalid_response", "bika") from exc
            result = {"items": items, "total": total, "page": page, "page_size": limit, "has_next": page < pages}
            self._record_favorite_page(result, sort)
            return result

    def _record_favorite_page(self, data, sort):
        owner = self._favorite_owner()
        if not owner:
            return
        ids = [item["id"] for item in data["items"]]
        self.store.record_bika_favorites(owner, ids)
        page, total = data["page"], data["total"]
        if page == 1:
            self._favorite_pages[sort] = {"next": 1, "total": total, "limit": data["page_size"], "ids": set()}
        scan = self._favorite_pages.get(sort)
        if (not scan or page != scan["next"] or total != scan["total"]
                or data["page_size"] != scan["limit"] or scan["ids"].intersection(ids)):
            self._favorite_pages.pop(sort, None)
            return
        scan["ids"].update(ids)
        scan["next"] += 1
        if not data["has_next"]:
            if len(scan["ids"]) == total:
                self.store.record_bika_favorites(owner, scan["ids"], complete=True)
            self._favorite_pages.pop(sort, None)

    def favorite_status(self, comic_id: str) -> dict:
        comic_id = self._favorite_id(comic_id)
        with self._auth_lock:
            owner = self._favorite_owner()
            if not owner or not (self.authorization or self._credentials_available()):
                raise ComicApiError("哔咔收藏需要绑定账号", 401, "login_required", "bika")
            return {"comic_id": comic_id, "favorited": self.store.bika_favorite_status(owner, comic_id)}

    def add_favorite(self, comic_id: str) -> dict:
        return self._set_favorite(comic_id, True)

    def remove_favorite(self, comic_id: str) -> dict:
        return self._set_favorite(comic_id, False)

    def _set_favorite(self, comic_id: str, wanted: bool) -> dict:
        comic_id = self._favorite_id(comic_id)
        verb, expected = ("加入", "favourite") if wanted else ("取消", "un_favourite")
        # Serialize the live check and toggle with login/logout and other favorite changes.
        with self._auth_lock:
            self.ensure_authenticated()
            owner = self._favorite_owner()
            if not owner:
                raise ComicApiError("哔咔收藏需要绑定账号", 401, "login_required", "bika")
            response = self.bika_request(f"comics/{comic_id}")
            data = response.get("data") if isinstance(response, dict) else None
            comic = data.get("comic") if isinstance(data, dict) else None
            if (not isinstance(comic, dict) or str(comic.get("_id", "")).lower() != comic_id
                    or not isinstance(comic.get("isFavourite"), bool)):
                raise ComicApiError(f"哔咔未返回有效收藏状态，未执行{verb}", 502, "invalid_response", "bika")
            current = comic["isFavourite"]
            self._favorite_pages.clear()
            self.store.set_bika_favorite(owner, comic_id, current)
            if current != wanted:
                self.store.set_bika_favorite(owner, comic_id, None)
                try:
                    response = self.bika_request(f"comics/{comic_id}/favourite", method="POST", _retry_safe=False)
                except Exception as exc:
                    if isinstance(exc, ComicApiError) and exc.code in ("login_required", "busy"):
                        raise
                    raise ComicApiError(f"哔咔{verb}收藏结果未确认，再次操作前将重新检查远端状态", 502,
                                        "favorite_unknown", "bika") from exc
                data = response.get("data") if isinstance(response, dict) else None
                if not isinstance(data, dict) or data.get("action") not in ("favourite", "un_favourite"):
                    raise ComicApiError(f"哔咔未确认{verb}收藏，再次操作前将重新检查远端状态", 502,
                                        "favorite_unknown", "bika")
                if data["action"] != expected:
                    self.store.set_bika_favorite(owner, comic_id, not wanted)
                    opposite = "取消" if wanted else "加入"
                    raise ComicApiError(f"上游返回{opposite}收藏结果，收藏状态存在并发冲突，需在哔咔核对", 409,
                                        "favorite_conflict", "bika")
            self.store.set_bika_favorite(owner, comic_id, wanted)
            return {"success": True, "comic_id": comic_id, "favorited": wanted,
                    "already_favorited": current}

    def search(self, keyword: str, page: int = 1, sort: str = "dd", category: str = "") -> List[Dict[str, Any]]:
        """搜索漫画"""
        self.ensure_authenticated()
        res = self.bika_request("comics/advanced-search", method="POST", params={
            "page": str(page)
        }, json_body={
            "keyword": keyword,
            "sort": sort or "dd",
            "categories": [category] if category else []
        })
        return self._parse_comics_list(res)

    def search_tag(self, tag: str, page: int = 1, sort: str = "dd", category: str = ""):
        self.ensure_authenticated()
        params = {"page": str(page), "t": tag, "s": sort or "dd"}
        if category:
            params["c"] = category
        return self._parse_comics_list(self.bika_request("comics", method="GET", params=params))

    def search_categories(self):
        self.ensure_authenticated()
        response = self.bika_request("categories", method="GET")
        rows = response.get("data", {}).get("categories")
        if not isinstance(rows, list):
            raise ComicApiError("哔咔分类响应格式无效", 502, "invalid_response", "bika")
        return comic_labels([row.get("title") for row in rows if isinstance(row, dict) and not row.get("isWeb")])

    def get_comic_detail(self, comic_id: str) -> Dict[str, Any]:
        """获取详情并加载全部章节分页"""
        self.ensure_authenticated()
        res = self.bika_request(f"comics/{comic_id}", method="GET")
        data = res.get("data", {}).get("comic", {})
        title = data.get("title", "")
        description = data.get("description", "")

        cover = data.get("thumb", {})
        cover_url = ""
        if isinstance(cover, dict):
            cover_url = f"{cover.get('fileServer', '')}/static/{cover.get('path', '')}"

        eps_count = int(data.get("epsCount", 0))
        total_pages = max(1, math.ceil(eps_count / 40))

        eps_docs = []
        for page in range(1, total_pages + 1):
            eps_res = self.bika_request(f"comics/{comic_id}/eps", method="GET", params={"page": str(page)})
            eps_docs.extend(eps_res.get("data", {}).get("eps", {}).get("docs", []))

        chapters = []
        for idx, doc in enumerate(eps_docs):
            ch_id = str(doc.get("order", ""))
            if not ch_id:
                continue
            ch_name = doc.get("title") or doc.get("name") or f"第{doc.get('order', idx + 1)}话"
            chapters.append({
                "id": ch_id,
                "name": ch_name,
                "order": int(ch_id)
            })

        chapters.sort(key=lambda x: x["order"])
        authors = comic_authors(data.get("author"))

        return {
            "id": comic_id,
            "title": title,
            "cover": cover_url,
            "description": description,
            "author": " / ".join(authors),
            "authors": authors,
            "page_count": comic_page_count(data.get("pagesCount")),
            "chapters": chapters,
            "tags": comic_labels(data.get("tags")),
            "categories": comic_labels(data.get("categories")),
            "source": "bika"
        }

    def get_chapter_images(self, comic_id: str, chapter_id: str) -> List[str]:
        """获取章节页面图片"""
        self.ensure_authenticated()
        image_urls = []
        page = 1
        total_pages = 1

        while page <= total_pages:
            res = self.bika_request(f"comics/{comic_id}/order/{chapter_id}/pages", method="GET",
                                    params={"page": str(page)})
            data = res.get("data", {})
            pages_meta = data.get("pages", {})

            total_pages = int(pages_meta.get("pages", 1))
            docs = pages_meta.get("docs", [])

            for doc in docs:
                media = doc.get("media", {})
                if isinstance(media, dict):
                    img_url = f"{media.get('fileServer', '')}/static/{media.get('path', '')}"
                    image_urls.append(img_url)

            page += 1

        return image_urls

    def _parse_comics_list(self, raw_res: dict) -> List[Dict[str, Any]]:
        """内部辅助解析哔咔返回的漫画数组"""
        data = raw_res.get("data", {})
        comics = data.get("comics", {})
        docs = comics.get("docs", []) if isinstance(comics, dict) else (comics if isinstance(comics, list) else [])

        results = []
        for item in docs:
            cid = str(item.get("_id", "") or item.get("id", ""))
            title = item.get("title", "")
            if not cid or not title:
                continue
            cover = item.get("thumb", {})
            cover_url = ""
            if isinstance(cover, dict):
                cover_url = f"{cover.get('fileServer', '')}/static/{cover.get('path', '')}"
            authors = comic_authors(item.get("author"))
            results.append({
                "id": cid,
                "title": title,
                "cover": cover_url,
                "source": "bika",
                "author": " / ".join(authors),
                "authors": authors,
                "page_count": comic_page_count(item.get("pagesCount")),
                "category": " · ".join(entry["label"] for entry in comic_labels(item.get("categories"))),
                "categories": comic_labels(item.get("categories")),
                "tags": comic_labels(item.get("tags")),
                "description": item.get("description", "")
            })
        return results

    def get_random(self) -> List[Dict[str, Any]]:
        """获取随机本子"""
        self.ensure_authenticated()
        res = self.bika_request("comics/random", method="GET")
        return self._parse_comics_list(res)

    def get_leaderboard(self, mode: str = "day") -> List[Dict[str, Any]]:
        """获取排行榜 (day/week/month)"""
        days = {"day": "H24", "week": "D7", "month": "D30"}.get(mode, "H24")
        self.ensure_authenticated()
        res = self.bika_request("comics/leaderboard", method="GET", params={"tt": days, "ct": "VC"})
        return self._parse_comics_list(res)

    def get_category_comics(self, category_name: str, page: int = 1, sort: str = "dd") -> List[Dict[str, Any]]:
        """
        筛选分类下的本子
        sort: dd=最新, da=最旧, ld=最多喜欢, vd=最多观看
        """
        s = sort if sort in {"dd", "da", "ld", "vd"} else "dd"
        self.ensure_authenticated()
        res = self.bika_request("comics", method="GET", params={"page": str(page), "c": category_name, "s": s})
        return self._parse_comics_list(res)

    def get_latest(self, page: int = 1, sort: str = "dd") -> List[Dict[str, Any]]:
        """
        获取本子列表（支持排序）
        sort: dd=最新, da=最旧, ld=最多喜欢, vd=最多观看
        """
        s = sort if sort in {"dd", "da", "ld", "vd"} else "dd"
        self.ensure_authenticated()
        res = self.bika_request("comics", method="GET", params={"page": str(page), "s": s})
        return self._parse_comics_list(res)
