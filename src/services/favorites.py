"""JM and Bika cloud favorites: browsing guards, the locally recorded catalog and account changes.

Images of favorited comics are downloaded by library sync; the catalog joins each favorite with the
local state recorded by that source's library worker.
"""
import asyncio
import json
import logging
import time

from src.errors import ComicApiError


class FavoriteCatalog:
    source = ""
    label = ""
    # Listing orders the whole-collection catalog can be refreshed with; the first is the default.
    catalog_sorts = ("",)

    def __init__(self, store, service, library):
        self.store, self.service, self.library = store, service, library
        self.log = logging.getLogger(f"comic.{self.source}_favorites")
        self.account_changing = False
        self.catalog_task = None
        self.catalog_state = {"active": False, "sort": "", "pages": 0, "total_pages": 0, "error": "",
                              "started_at": None, "finished_at": None}

    def comic_id(self, value):
        raise NotImplementedError

    def catalog_scope(self, sort):
        """Keyword arguments for the favorites listing that covers the whole collection in ``sort`` order."""
        return {}

    def owner(self):
        return self.store.account_owner((self.store.account(self.source) or {}).get("account"))

    def require_account(self):
        if self.account_changing:
            raise ComicApiError(f"{self.label}账号正在变更", 409, "account_changing", self.source)
        plugin = self.service.source(self.source)
        if "favorites" not in plugin.capabilities:
            raise ComicApiError(f"当前{self.label}插件不支持收藏夹", 400, "unsupported", self.source)
        status = plugin.account_status()
        if not (status.get("authenticated") or status.get("configured")):
            raise ComicApiError(f"{self.label}收藏需要绑定账号", 401, "login_required", self.source)
        return plugin

    def guard_account_change(self):
        if self.account_changing or self.library.source_active(self.source):
            raise ComicApiError(f"书库同步或账号请求进行中，停止后才能变更{self.label}账号", 409, "sync_active", self.source)
        self.account_changing = True

    def catalog(self, local="", page=1, page_size=20):
        """The locally recorded favorites joined with local state, filtered and paginated on the server.

        ``local`` is "downloaded" (verified copies) or "missing" (everything else, including broken copies).
        """
        self.require_account()
        owner = self.owner()
        if not owner:
            raise ComicApiError(f"{self.label}收藏需要绑定账号", 401, "login_required", self.source)
        worker = self.library.worker(self.source)
        data = self.store.favorite_catalog(self.source, owner, str(worker.root()), worker.status_table)
        rows = data["rows"]
        downloaded = sum(row["status"] == "downloaded" for row in rows)
        if local:
            rows = [row for row in rows if (row["status"] == "downloaded") == (local == "downloaded")]
        chunk = rows[(page - 1) * page_size:page * page_size]
        items, states = [], {}
        for row in chunk:
            item = json.loads(row["item"]) if row["item"] else {"title": row["title"] or row["comic_id"]}
            item.update(id=row["comic_id"], source=self.source)
            items.append(item)
            if row["status"]:
                states[row["comic_id"]] = {key: row[key] for key in ("status", "title", "path", "images", "message", "checked_at")}
        return {"complete": data["complete"], "updated_at": data["updated_at"], "sort": data["sort"],
                "counts": {"all": len(data["rows"]), "downloaded": downloaded, "missing": len(data["rows"]) - downloaded},
                "total": len(rows), "page": page, "page_size": page_size, "has_next": page * page_size < len(rows),
                "items": items, "local": states, "refresh": dict(self.catalog_state)}

    def refresh_catalog(self, sort=None):
        """Re-read every favorites page in the background and replace the local catalog with the result."""
        self.require_account()
        sort = sort or self.catalog_sorts[0]
        if sort not in self.catalog_sorts:
            raise ComicApiError("收藏排序无效", 422, "invalid_request", self.source)
        if self.catalog_task and not self.catalog_task.done():
            raise ComicApiError("收藏列表正在更新", 409, "catalog_active", self.source)
        self.catalog_state = {"active": True, "sort": sort, "pages": 0, "total_pages": 0, "error": "",
                              "started_at": time.time(), "finished_at": None}
        self.catalog_task = asyncio.create_task(self._refresh_catalog(sort, self.owner()))
        return dict(self.catalog_state)

    async def _refresh_catalog(self, sort, owner):
        state = self.catalog_state
        try:
            ids, items, expected = [], {}, None
            for page in range(1, 1001):
                if owner != self.owner():
                    raise ComicApiError(f"更新期间{self.label}账号发生变更", 409, "account_changed", self.source)
                data = await self.service.call(self.source, "favorites", page=page, **self.catalog_scope(sort))
                if expected is None:
                    expected = data["total"]
                    state["total_pages"] = max(1, -(-expected // max(1, data["page_size"])))
                elif data["total"] != expected:
                    raise ComicApiError("收藏在更新期间发生变化，需要重新更新", 409, "favorites_changed", self.source)
                for item in data["items"]:
                    comic_id = self.comic_id(item["id"])
                    if comic_id in items:
                        raise ComicApiError("收藏分页重复，未更新收藏列表", 502, "invalid_response", self.source)
                    ids.append(comic_id)
                    items[comic_id] = item
                state["pages"] = page
                if not data["has_next"]:
                    if len(ids) != expected:
                        raise ComicApiError("收藏分页数量不一致，未更新收藏列表", 502, "invalid_response", self.source)
                    break
                if not data["items"]:
                    raise ComicApiError("收藏分页提前返回空页", 502, "invalid_response", self.source)
            else:
                raise ComicApiError("收藏超过 1000 页上限，未更新收藏列表", 422, "sync_too_large", self.source)
            if owner != self.owner():
                raise ComicApiError(f"更新期间{self.label}账号发生变更", 409, "account_changed", self.source)
            record = getattr(self.store, f"record_{self.source}_favorites")
            await asyncio.to_thread(record, owner, ids, True, items, sort)
        except Exception as exc:
            state["error"] = str(exc) if isinstance(exc, ComicApiError) else "收藏列表更新失败"
            self.log.warning("%s favorites catalog refresh failed", self.source, exc_info=not isinstance(exc, ComicApiError))
        finally:
            state.update(active=False, finished_at=time.time())

    async def stop(self):
        if self.catalog_task and not self.catalog_task.done():
            self.catalog_task.cancel()


class JmFavorites(FavoriteCatalog):
    source = "jm"
    label = "禁漫"
    catalog_sorts = ("mr", "mp")

    def comic_id(self, value):
        value = str(value)
        if not value.isascii() or not value.isdigit() or len(value) > 20:
            raise ComicApiError("禁漫返回了无效编号", 502, "invalid_response", "jm")
        return value

    def catalog_scope(self, sort):
        return {"folder_id": "0", "order": sort}


class BikaFavorites(FavoriteCatalog):
    source = "bika"
    label = "哔咔"
    catalog_sorts = ("dd", "da", "ld", "vd")

    def comic_id(self, value):
        value = str(value).lower()
        if len(value) != 24 or any(char not in "0123456789abcdef" for char in value):
            raise ComicApiError("哔咔返回了无效编号", 502, "invalid_response", "bika")
        return value

    def catalog_scope(self, sort):
        return {"sort": sort}
